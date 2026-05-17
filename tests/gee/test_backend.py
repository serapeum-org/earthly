"""Tests for `earthlens.gee.backend` — the `GEE` data source.

Earth Engine and the HTTP download are fully faked via ``monkeypatch``:
``ee`` is replaced with a small chainable recorder
(`_FakeImageCollection` / `_FakeImage` / `_FakeGeometry`), `requests`
with a stub that returns non-zip bytes, and `EarthEngineAuth.initialize`
with a stub that returns a fixed project. The real shipped
`gee_data_catalog.yaml` is used (no network).
"""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from earthlens.base import SpatialExtent, TemporalExtent
from earthlens.gee import backend as backend_module
from earthlens.gee.backend import GEE
from earthlens.gee.catalog import Dataset, Extent

# -- fakes ------------------------------------------------------------------


class _FakeImage:
    """Recorder standing in for an `ee.Image` (the composited result)."""

    def __init__(self, label: str = "image", reducer: str | None = None):
        self.label = label
        self.reducer = reducer
        self.calls: list[tuple[str, tuple]] = []
        self.download_params: dict | None = None
        self.download_params_list: list[dict] = []

    def select(self, bands):
        self.calls.append(("select", (tuple(bands),)))
        return self

    def clip(self, geom):
        self.calls.append(("clip", (geom,)))
        return self

    def getDownloadURL(self, params):  # noqa: N802 - mirrors the ee API
        self.download_params = dict(params)
        self.download_params_list.append(dict(params))
        return "http://fake.test/download.tif"


class _FakeImageCollection:
    """Recorder standing in for an `ee.ImageCollection`.

    Chain methods (`filterDate`, `filterBounds`, `select`) return a new
    instance carrying the accumulated call log; the reducer convenience
    methods (`mean`/`median`/`mosaic`/...) return a :class:`_FakeImage`.
    """

    def __init__(self, source, calls: list | None = None):
        self.source = source
        self.calls: list[tuple[str, tuple]] = list(calls or [])

    def _chain(self, name: str, *args) -> "_FakeImageCollection":
        return _FakeImageCollection(self.source, self.calls + [(name, args)])

    def filterDate(self, start, end):  # noqa: N802
        return self._chain("filterDate", start, end)

    def filterBounds(self, geom):  # noqa: N802
        return self._chain("filterBounds", geom)

    def select(self, bands):
        return self._chain("select", tuple(bands))

    def _reduce(self, name: str) -> _FakeImage:
        self.calls.append((name, ()))
        return _FakeImage(label=f"{name}({self.source})", reducer=name)

    def mean(self):
        return self._reduce("mean")

    def median(self):
        return self._reduce("median")

    def min(self):
        return self._reduce("min")

    def max(self):
        return self._reduce("max")

    def mode(self):
        return self._reduce("mode")

    def mosaic(self):
        return self._reduce("mosaic")

    def sum(self):
        return self._reduce("sum")

    def method_names(self) -> list[str]:
        """Return just the names of the recorded chain calls (for assertions)."""
        return [name for name, _ in self.calls]


class _FakeGeometry:
    """Stands in for `ee.Geometry.Rectangle(...)` output (or a gdf geometry)."""

    def __init__(self, coords):
        self.coords = coords


class _FakeTask:
    """Stands in for an `ee.batch.Task` returned by `ee.batch.Export.image.to*`."""

    def __init__(self, kwargs: dict, states: list[str] | None = None, error: str | None = None):
        self.kwargs = kwargs
        self._states = list(states or ["COMPLETED"])
        self._error = error
        self.started = False
        self.poll_count = 0

    def start(self):
        self.started = True

    def status(self) -> dict:
        self.poll_count += 1
        state = self._states[min(self.poll_count - 1, len(self._states) - 1)]
        out = {"state": state}
        if state == "FAILED" and self._error:
            out["error_message"] = self._error
        return out


class _FakeExportImage:
    """Stands in for `ee.batch.Export.image` (`toDrive` / `toCloudStorage`)."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.tasks: list[_FakeTask] = []
        self.next_task_states: list[str] | None = None
        self.next_task_error: str | None = None

    def _make(self, method: str, **kwargs) -> _FakeTask:
        self.calls.append((method, dict(kwargs)))
        task = _FakeTask(kwargs, self.next_task_states, self.next_task_error)
        self.tasks.append(task)
        return task

    def toDrive(self, **kwargs):  # noqa: N802
        return self._make("toDrive", **kwargs)

    def toCloudStorage(self, **kwargs):  # noqa: N802
        return self._make("toCloudStorage", **kwargs)

    def toAsset(self, **kwargs):  # noqa: N802
        return self._make("toAsset", **kwargs)


class _FakeEE:
    """A minimal stand-in for the `ee` module."""

    EEException = RuntimeError  # only needed for the non-service-account path

    def __init__(self):
        self.ic_log: list = []
        self.image_log: list = []
        self.export_image = _FakeExportImage()
        self.batch = SimpleNamespace(Export=SimpleNamespace(image=self.export_image))

    def ImageCollection(self, source):  # noqa: N802
        if isinstance(source, list):
            source = ("list", len(source))
        self.ic_log.append(source)
        return _FakeImageCollection(source)

    def Image(self, asset_id):  # noqa: N802
        self.image_log.append(asset_id)
        return _FakeImage(label=f"Image({asset_id})")

    @property
    def Geometry(self):  # noqa: N802
        return SimpleNamespace(Rectangle=lambda coords: _FakeGeometry(coords))


class _FakeHTTPResponse:
    """Stand-in for `requests.get(...)` exposing `.content` + `raise_for_status`."""

    def __init__(self, body: bytes):
        self.content = body

    def raise_for_status(self):
        return None


class _FakePyramidsHandle:
    """Stand-in for a `pyramids.dataset.Dataset` returned by `from_bytes`."""

    def __init__(self, body: bytes):
        self._body = body

    def to_file(self, path: str) -> None:
        from pathlib import Path as _Path

        _Path(path).write_bytes(self._body)


class _FakePyramidsDataset:
    """Stand-in for `pyramids.dataset.Dataset` — captures `from_*` calls."""

    from_bytes_calls: list[dict] = []
    from_archive_calls: list[dict] = []

    @classmethod
    def reset(cls) -> None:
        cls.from_bytes_calls = []
        cls.from_archive_calls = []

    @classmethod
    def from_bytes(cls, data, *, suffix: str = ".tif", name=None, read_only: bool = True):
        cls.from_bytes_calls.append({"data": data, "suffix": suffix})
        return _FakePyramidsHandle(data)

    @classmethod
    def from_archive(cls, url_or_path, *, kind: str = "auto", member_glob: str = "*",
                     band_names=None, align: bool = False, no_data_value=None, path=None):
        cls.from_archive_calls.append({
            "url_or_path": str(url_or_path), "kind": kind,
            "member_glob": member_glob, "path": path,
        })
        from pathlib import Path as _Path

        if path is not None:
            _Path(path).write_bytes(b"unpacked-from-archive")


# A 4-byte big-endian TIFF magic + filler — emphatically not a zip.
_FAKE_TIFF_BYTES = b"MM\x00*" + b"\x00" * 64


# -- fixtures ---------------------------------------------------------------


@pytest.fixture(scope="function")
def fake_ee(monkeypatch) -> _FakeEE:
    """Replace `ee` in the backend (and `create_feature`/`requests`) with fakes.

    Returns:
        _FakeEE: The fake `ee` module (its `ic_log` / `image_log` record
        constructions for assertions).
    """
    fake = _FakeEE()
    monkeypatch.setattr(backend_module, "ee", fake)
    monkeypatch.setattr(
        backend_module, "requests",
        SimpleNamespace(get=lambda url, timeout=None: _FakeHTTPResponse(_FAKE_TIFF_BYTES)),
    )
    _FakePyramidsDataset.reset()
    monkeypatch.setattr(backend_module, "PyramidsDataset", _FakePyramidsDataset)
    monkeypatch.setattr(backend_module, "create_feature", lambda gdf: SimpleNamespace(geometry=lambda: _FakeGeometry("from-gdf")))
    monkeypatch.setattr(
        backend_module.EarthEngineAuth, "initialize",
        staticmethod(lambda service_account, service_key, project=None: project or "fake-project"),
    )
    return fake


@pytest.fixture(scope="function")
def make_gee(fake_ee, tmp_path):
    """Return a factory that builds a `GEE` against the fakes.

    The factory accepts the same keyword arguments as `GEE`, with sane
    defaults (a small bbox over Egypt, `path=tmp_path`, a service
    account so the stubbed `EarthEngineAuth.initialize` runs).

    Returns:
        Callable[..., GEE]: The factory.
    """

    def _factory(**overrides) -> GEE:
        params = dict(
            start="2000-02-11",
            end="2000-02-12",
            variables={"USGS/SRTMGL1_003": ["elevation"]},
            lat_lim=[29.9, 30.0],
            lon_lim=[31.2, 31.3],
            path=str(tmp_path),
            scale=90.0,
            service_account="sa@x.iam",
            service_key="key.json",
        )
        params.update(overrides)
        return GEE(**params)

    return _factory


# -- tests ------------------------------------------------------------------


class TestInit:
    """Tests for `GEE.__init__` and the captured attributes."""

    def test_constructs_and_sets_attributes(self, make_gee):
        """A valid construction wires up the catalog, project, and config."""
        gee = make_gee()
        assert gee.catalog.get_dataset("USGS/SRTMGL1_003").ee_type == "image"
        assert gee.project == "fake-project"
        assert gee.scale == 90.0 and gee.crs == "EPSG:4326"
        assert isinstance(gee.space, SpatialExtent) and isinstance(gee.time, TemporalExtent)
        assert gee.client is backend_module.ee

    def test_bad_export_via_rejected(self, make_gee):
        """An unknown `export_via` raises `ValueError` at construction."""
        with pytest.raises(ValueError, match="export_via must be"):
            make_gee(export_via="ftp")

    def test_bad_export_via_fails_before_catalog_load(self, monkeypatch, tmp_path):
        """A typo'd `export_via` raises before paying for the catalog parse (M3)."""
        from earthlens.gee import backend as backend_module

        loads = 0

        class _ExplodingCatalog:
            def __init__(self, *_a, **_k):
                nonlocal loads
                loads += 1

        monkeypatch.setattr(backend_module, "Catalog", _ExplodingCatalog)
        with pytest.raises(ValueError, match="export_via must be"):
            GEE(
                start="2000-02-11", end="2000-02-12",
                variables={"USGS/SRTMGL1_003": ["elevation"]},
                lat_lim=[29.9, 30.0], lon_lim=[31.2, 31.3], path=str(tmp_path),
                export_via="ftp",
            )
        assert loads == 0, "Catalog() should not be constructed when export_via is invalid"

    def test_initialize_without_credentials_raises(self, fake_ee, tmp_path):
        """No service account and no `project` → `AuthenticationError`."""
        from earthlens.gee.backend import AuthenticationError

        with pytest.raises(AuthenticationError, match="needs either service_account"):
            GEE(
                start="2000-02-11", end="2000-02-12",
                variables={"USGS/SRTMGL1_003": ["elevation"]},
                lat_lim=[29.9, 30.0], lon_lim=[31.2, 31.3], path=str(tmp_path),
            )


class TestCheckInputDates:
    """Tests for `GEE._check_input_dates`."""

    def test_raw_single_bucket(self, make_gee):
        """`temporal_resolution="raw"` → one date (the start)."""
        gee = make_gee(start="2020-01-01", end="2020-01-31", temporal_resolution="raw")
        assert len(gee.time.dates) == 1
        assert gee.time.dates[0] == pd.Timestamp("2020-01-01")
        assert gee.time.resolution == "raw"

    @pytest.mark.parametrize(
        "resolution, start, end, expected_n",
        [
            ("daily", "2020-01-01", "2020-01-05", 5),
            ("monthly", "2020-01-01", "2020-03-15", 3),
            ("yearly", "2018-06-01", "2021-06-01", 3),
        ],
    )
    def test_periodic_buckets(self, make_gee, resolution, start, end, expected_n):
        """daily / monthly / yearly produce the expected number of buckets."""
        gee = make_gee(start=start, end=end, temporal_resolution=resolution)
        assert len(gee.time.dates) == expected_n
        assert gee.time.resolution == resolution

    def test_unknown_resolution_raises(self, make_gee):
        """An unknown `temporal_resolution` raises `ValueError`."""
        with pytest.raises(ValueError, match="must be 'raw', 'daily', 'monthly', or"):
            make_gee(temporal_resolution="hourly")

    def test_start_after_end_raises(self, make_gee):
        """`start` later than `end` raises `ValueError`."""
        with pytest.raises(ValueError):
            make_gee(start="2020-06-01", end="2020-01-01")


class TestCreateGrid:
    """Tests for `GEE._create_grid`."""

    def test_returns_spatial_extent_without_resolution(self, make_gee):
        """The bbox is captured as a `SpatialExtent` with no `resolution`."""
        gee = make_gee(lat_lim=[10.0, 20.0], lon_lim=[-5.0, 5.0])
        assert gee.space.latitude_min == 10.0 and gee.space.latitude_max == 20.0
        assert gee.space.longitude_min == -5.0 and gee.space.longitude_max == 5.0
        assert gee.space.resolution is None


class TestClampWindowToExtent:
    """Tests for `GEE._clamp_window_to_extent`."""

    def test_overlap_clamps_to_dataset_extent(self, make_gee):
        """The window is clamped to the dataset's published extent."""
        gee = make_gee(start="1999-01-01", end="2010-01-01")
        ds = gee.catalog.get_dataset("USGS/SRTMGL1_003")
        start, end_excl = gee._clamp_window_to_extent(ds)
        assert start == dt.datetime(2000, 2, 11)
        assert end_excl == dt.datetime(2000, 2, 23)

    def test_no_overlap_returns_none(self, make_gee):
        """A window entirely after the dataset's extent yields `(None, None)`."""
        gee = make_gee(start="2020-01-01", end="2020-01-02")
        ds = gee.catalog.get_dataset("USGS/SRTMGL1_003")
        assert gee._clamp_window_to_extent(ds) == (None, None)

    def test_open_ended_dataset_clamps_to_now(self, make_gee, monkeypatch):
        """For a dataset with `end_date: null`, the upper bound is "now + 1 day"."""
        fixed_now = dt.datetime(2026, 5, 13)

        class _FixedDatetime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_now

        monkeypatch.setattr(backend_module.dt, "datetime", _FixedDatetime)
        gee = make_gee(start="2020-01-01", end="2099-01-01",
                       variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]},
                       scale=5566.0)
        ds = gee.catalog.get_dataset("UCSB-CHG/CHIRPS/DAILY")
        start, end_excl = gee._clamp_window_to_extent(ds)
        assert start == dt.datetime(2020, 1, 1)
        assert end_excl == fixed_now + dt.timedelta(days=1)


class TestDownloadRejections:
    """Tests for invalid / not-yet-supported configurations."""

    def test_aggregate_rejected(self, make_gee):
        """Passing `aggregate=` raises `NotImplementedError`."""
        with pytest.raises(NotImplementedError, match="aggregate="):
            make_gee().download(aggregate=object(), progress_bar=False)

    def test_drive_without_folder_rejected_at_construction(self, make_gee):
        """`export_via="drive"` requires `drive_folder` at construction."""
        with pytest.raises(ValueError, match="export_via='drive' requires drive_folder"):
            make_gee(export_via="drive")

    def test_gcs_without_bucket_rejected_at_construction(self, make_gee):
        """`export_via="gcs"` requires `gcs_bucket` at construction."""
        with pytest.raises(ValueError, match="export_via='gcs' requires gcs_bucket"):
            make_gee(export_via="gcs")


class TestExportViaBatch:
    """Tests for the asynchronous `export_via="drive"` / `"gcs"` paths."""

    def test_drive_export_queues_polls_and_returns_destination(self, make_gee):
        """A Drive export queues a `toDrive` task, polls it, and returns `drive://...`."""
        gee = make_gee(export_via="drive", drive_folder="ee_out")
        results = gee.download(progress_bar=False)
        assert results == ["drive://ee_out/USGS_SRTMGL1_003_elevation_20000211"]
        (method, kwargs), = gee.client.export_image.calls
        assert method == "toDrive"
        assert kwargs["folder"] == "ee_out" and kwargs["scale"] == 90.0
        assert kwargs["crs"] == "EPSG:4326" and kwargs["maxPixels"] == 1e13
        assert kwargs["fileNamePrefix"] == "USGS_SRTMGL1_003_elevation_20000211"

    def test_gcs_export_uses_to_cloud_storage(self, make_gee):
        """A GCS export queues a `toCloudStorage` task and returns `gs://...`."""
        gee = make_gee(export_via="gcs", gcs_bucket="my-bucket")
        results = gee.download(progress_bar=False)
        assert results == ["gs://my-bucket/USGS_SRTMGL1_003_elevation_20000211"]
        (method, kwargs), = gee.client.export_image.calls
        assert method == "toCloudStorage" and kwargs["bucket"] == "my-bucket"

    def test_failed_export_task_raises(self, make_gee):
        """A `FAILED` export task surfaces as a `RuntimeError` with the message."""
        gee = make_gee(export_via="drive", drive_folder="ee_out")
        gee.client.export_image.next_task_states = ["FAILED"]
        gee.client.export_image.next_task_error = "out of quota"
        with pytest.raises(RuntimeError, match="ended FAILED: out of quota"):
            gee.download(progress_bar=False)

    def test_task_is_started_and_polled(self, make_gee):
        """`wait_for_task` calls `task.start()` and then polls `task.status()`."""
        gee = make_gee(export_via="drive", drive_folder="ee_out")
        gee.download(progress_bar=False)
        task = gee.client.export_image.tasks[0]
        assert task.started is True and task.poll_count >= 1


class TestExportViaAsset:
    """Tests for the `export_via="asset"` path (M1)."""

    def test_missing_asset_id_rejected_at_construction(self, make_gee):
        """`export_via="asset"` without an `asset_id=` raises a clear `ValueError`."""
        with pytest.raises(ValueError, match="export_via='asset' requires asset_id"):
            make_gee(export_via="asset")

    def test_unknown_export_via_lists_asset_too(self, make_gee):
        """The updated ValueError message advertises `'asset'` as a valid sink."""
        with pytest.raises(ValueError, match="'url', 'drive', 'gcs', or 'asset'"):
            make_gee(export_via="ftp")

    def test_asset_export_queues_to_asset_and_returns_ee_uri(self, make_gee):
        """An Asset export queues a `toAsset` task and returns `ee://<asset>/<prefix>`."""
        gee = make_gee(
            export_via="asset", asset_id="projects/p/assets/my-folder"
        )
        results = gee.download(progress_bar=False)
        assert results == ["ee://projects/p/assets/my-folder/USGS_SRTMGL1_003_elevation_20000211"]
        (method, kwargs), = gee.client.export_image.calls
        assert method == "toAsset"
        assert kwargs["assetId"] == "projects/p/assets/my-folder/USGS_SRTMGL1_003_elevation_20000211"
        assert "fileNamePrefix" not in kwargs  # `toAsset` doesn't use it
        assert kwargs["scale"] == 90.0 and kwargs["maxPixels"] == 1e13

    def test_trailing_slash_on_asset_id_is_tolerated(self, make_gee):
        """A trailing `/` on `asset_id` is stripped to avoid a double slash."""
        gee = make_gee(
            export_via="asset", asset_id="projects/p/assets/my-folder/"
        )
        gee.download(progress_bar=False)
        (_, kwargs), = gee.client.export_image.calls
        assert "//" not in kwargs["assetId"].split(":", 1)[-1]
        assert kwargs["assetId"].startswith("projects/p/assets/my-folder/")
        assert "//" not in kwargs["assetId"]


class TestBuildCollection:
    """Tests for `GEE._build_collection`."""

    def test_image_collection_chain(self, make_gee):
        """A collection dataset is filtered by date, bounds, then bands."""
        gee = make_gee(variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]}, scale=5566.0)
        ds = gee.catalog.get_dataset("UCSB-CHG/CHIRPS/DAILY")
        col = gee._build_collection(ds, ["precipitation"], dt.datetime(2020, 6, 1), dt.datetime(2020, 6, 2))
        assert col.method_names() == ["filterDate", "filterBounds", "select"]

    def test_static_image_skips_filter_date(self, make_gee):
        """A static `image` dataset is *not* date-filtered."""
        gee = make_gee()
        ds = gee.catalog.get_dataset("USGS/SRTMGL1_003")
        col = gee._build_collection(ds, ["elevation"], dt.datetime(2000, 2, 11), dt.datetime(2000, 2, 13))
        assert col.method_names() == ["filterBounds", "select"]
        assert gee.client.image_log == ["USGS/SRTMGL1_003"]


class TestComposite:
    """Tests for `GEE._composite`."""

    def test_raw_yields_single_bucket(self, make_gee):
        """`temporal_resolution="raw"` yields one `(start, image)` bucket."""
        gee = make_gee(start="2020-06-01", end="2020-06-30", temporal_resolution="raw",
                       variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]}, scale=5566.0)
        ds = gee.catalog.get_dataset("UCSB-CHG/CHIRPS/DAILY")
        col = gee._build_collection(ds, ["precipitation"], dt.datetime(2020, 6, 1), dt.datetime(2020, 7, 1))
        buckets = list(gee._composite(col, ds, dt.datetime(2020, 6, 1), dt.datetime(2020, 7, 1)))
        assert len(buckets) == 1
        when, image = buckets[0]
        assert when == dt.datetime(2020, 6, 1)
        assert image.reducer == "mean"

    def test_monthly_yields_one_bucket_per_month(self, make_gee):
        """Monthly resolution splits the window into per-month buckets."""
        gee = make_gee(start="2020-06-01", end="2020-07-31", temporal_resolution="monthly",
                       variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]}, scale=5566.0)
        ds = gee.catalog.get_dataset("UCSB-CHG/CHIRPS/DAILY")
        col = gee._build_collection(ds, ["precipitation"], dt.datetime(2020, 6, 1), dt.datetime(2020, 8, 1))
        buckets = list(gee._composite(col, ds, dt.datetime(2020, 6, 1), dt.datetime(2020, 8, 1)))
        assert [w for w, _ in buckets] == [dt.datetime(2020, 6, 1), dt.datetime(2020, 7, 1)]
        assert all(img.reducer == "mean" for _, img in buckets)

    def test_static_image_one_bucket_regardless_of_resolution(self, make_gee):
        """A static `image` dataset always yields a single bucket."""
        gee = make_gee(temporal_resolution="monthly")
        ds = gee.catalog.get_dataset("USGS/SRTMGL1_003")
        col = gee._build_collection(ds, ["elevation"], dt.datetime(2000, 2, 11), dt.datetime(2000, 2, 13))
        buckets = list(gee._composite(col, ds, dt.datetime(2000, 2, 11), dt.datetime(2000, 2, 13)))
        assert len(buckets) == 1

    def test_reducer_override(self, make_gee):
        """The constructor `reducer` overrides the dataset's `default_reducer`."""
        gee = make_gee(reducer="median", variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]},
                       start="2020-06-01", end="2020-06-02", scale=5566.0)
        ds = gee.catalog.get_dataset("UCSB-CHG/CHIRPS/DAILY")
        col = gee._build_collection(ds, ["precipitation"], dt.datetime(2020, 6, 1), dt.datetime(2020, 6, 3))
        (_, image), = gee._composite(col, ds, dt.datetime(2020, 6, 1), dt.datetime(2020, 6, 3))
        assert image.reducer == "median"


class TestApi:
    """Tests for `GEE._api`."""

    def test_size_guard_rejects_oversized_request(self, make_gee):
        """A bbox×scale exceeding 32768 px per axis raises a clear `ValueError`."""
        gee = make_gee(start="2000-02-11", end="2000-02-12", lat_lim=[0.0, 40.0],
                       lon_lim=[0.0, 40.0], scale=30.0)
        with pytest.raises(ValueError, match="32768-px"):
            gee.download(progress_bar=False)

    def test_missing_scale_raises(self, make_gee):
        """`_api` raises when there is no `scale` and no dataset `spatial_resolution`."""
        gee = make_gee(scale=None)
        gee.scale = None
        bare = Dataset(id="DEMO/IMG", title="x", ee_type="image",
                       extent=Extent(start_date="2000-01-01"), spatial_resolution=None)
        with pytest.raises(ValueError, match="no output scale"):
            gee._api(_FakeImage(), bare, ["b"], dt.datetime(2000, 1, 1))

    def test_successful_download_writes_geotiff(self, make_gee, tmp_path):
        """A within-limits request writes a `.tif` and returns its path."""
        gee = make_gee()
        paths = gee.download(progress_bar=False)
        assert len(paths) == 1
        target = paths[0]
        assert target.name == "USGS_SRTMGL1_003_elevation_20000211.tif"
        assert target.parent == tmp_path
        assert target.read_bytes() == _FAKE_TIFF_BYTES
        assert not zipfile.is_zipfile(target)

    def test_zip_response_unpacked_via_pyramids(self, make_gee, monkeypatch, tmp_path):
        """A multi-band zip response is routed through `Dataset.from_archive`.

        Earth Engine returns a zip-of-tifs when the request asks for several
        bands; the backend writes the body to `<prefix>.zip` and unpacks it
        into the target via pyramids, then deletes the zip.
        """
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("inner.tif", b"data")
        monkeypatch.setattr(
            backend_module, "requests",
            SimpleNamespace(get=lambda url, timeout=None: _FakeHTTPResponse(buf.getvalue())),
        )
        paths = make_gee().download(progress_bar=False)
        target = paths[0]
        assert target.exists()
        assert target.read_bytes() == b"unpacked-from-archive"
        assert not (tmp_path / f"{target.stem}.zip").exists()  # cleaned up
        assert len(_FakePyramidsDataset.from_archive_calls) == 1
        call = _FakePyramidsDataset.from_archive_calls[0]
        assert call["kind"] == "zip"
        assert call["member_glob"] == "*.tif"
        assert call["path"] == str(target)

    def test_http_timeout_passthrough(self, make_gee, monkeypatch):
        """`http_timeout=` is forwarded verbatim to `requests.get`."""
        captured: dict = {}

        def _capture_get(url, timeout=None):
            captured["timeout"] = timeout
            return _FakeHTTPResponse(_FAKE_TIFF_BYTES)

        monkeypatch.setattr(backend_module, "requests", SimpleNamespace(get=_capture_get))
        make_gee(http_timeout=42.5).download(progress_bar=False)
        assert captured["timeout"] == 42.5

    def test_download_passes_geotiff_format_and_scale(self, make_gee):
        """The `getDownloadURL` request uses `format="GEO_TIFF"`, the scale, and the CRS."""
        gee = make_gee(scale=120.0, crs="EPSG:3857")
        # Reach the image the pipeline produced by re-running the build/composite:
        ds = gee.catalog.get_dataset("USGS/SRTMGL1_003")
        col = gee._build_collection(ds, ["elevation"], dt.datetime(2000, 2, 11), dt.datetime(2000, 2, 13))
        (_, image), = gee._composite(col, ds, dt.datetime(2000, 2, 11), dt.datetime(2000, 2, 13))
        gee._api(image, ds, ["elevation"], dt.datetime(2000, 2, 11))
        assert image.download_params["format"] == "GEO_TIFF"
        assert image.download_params["scale"] == 120.0
        assert image.download_params["crs"] == "EPSG:3857"


class TestAutoSplit:
    """Tests for `auto_split=True` (H2 — auto-split oversized URL downloads)."""

    def test_default_keeps_existing_value_error(self, make_gee):
        """`auto_split=False` (the default) preserves the historical guard."""
        gee = make_gee(lat_lim=[0.0, 40.0], lon_lim=[0.0, 40.0], scale=30.0)
        with pytest.raises(ValueError, match="auto_split=True"):
            gee.download(progress_bar=False)

    def test_ctor_arg_persists(self, make_gee):
        """`auto_split=True` is captured on the instance."""
        assert make_gee(auto_split=True).auto_split is True
        assert make_gee().auto_split is False

    def test_oversized_aoi_is_tiled_and_merged(self, make_gee, monkeypatch, tmp_path):
        """An oversized AOI with `auto_split=True` downloads N tiles + mosaics."""
        merge_calls: list[dict] = []

        def _fake_merge(src, dst, **kwargs):
            from pathlib import Path as _Path
            merge_calls.append({"src": list(src), "dst": str(dst), "kwargs": kwargs})
            _Path(dst).write_bytes(b"merged")

        monkeypatch.setattr(backend_module, "merge_rasters", _fake_merge)

        gee = make_gee(lat_lim=[0.0, 40.0], lon_lim=[0.0, 40.0], scale=30.0, auto_split=True)
        paths = gee.download(progress_bar=False)

        assert len(paths) == 1
        target = paths[0]
        assert target.parent == tmp_path
        assert target.name == "USGS_SRTMGL1_003_elevation_20000211.tif"
        assert target.read_bytes() == b"merged"

        assert len(merge_calls) == 1
        assert merge_calls[0]["dst"] == str(target)
        assert len(merge_calls[0]["src"]) > 1
        for tile_path in merge_calls[0]["src"]:
            assert tile_path.endswith(".tif")
            assert "_tile_" in tile_path
            assert not Path(tile_path).exists()  # tile files cleaned up post-merge

    def test_each_tile_request_is_within_the_synchronous_cap(
        self, make_gee, monkeypatch
    ):
        """Every `getDownloadURL` call's `region` covers <= EE_MAX_DIMENSION px."""
        from earthlens.gee._helpers import EE_MAX_DIMENSION as _CAP

        monkeypatch.setattr(
            backend_module, "merge_rasters",
            lambda src, dst, **k: Path(dst).write_bytes(b"merged"),
        )

        gee = make_gee(lat_lim=[0.0, 40.0], lon_lim=[0.0, 40.0], scale=30.0, auto_split=True)
        gee.download(progress_bar=False)

        # The fake `ee.Image` records every `getDownloadURL` call's params.
        # The fake `ee.Geometry.Rectangle` returns a `_FakeGeometry` with
        # `.coords` set to `[west, south, east, north]`. The tile is at-cap
        # when its width-in-degrees ≤ EE_MAX_DIMENSION * (scale / METRES_PER_DEGREE).
        from earthlens.base.spatial import METRES_PER_DEGREE

        deg_per_px = 30.0 / METRES_PER_DEGREE
        cap_deg = _CAP * deg_per_px
        # Replay the image's recorded download_params_list:
        ds = gee.catalog.get_dataset("USGS/SRTMGL1_003")
        col = gee._build_collection(
            ds, ["elevation"], dt.datetime(2000, 2, 11), dt.datetime(2000, 2, 13),
        )
        (_, image), = gee._composite(
            col, ds, dt.datetime(2000, 2, 11), dt.datetime(2000, 2, 13),
        )
        gee._api(image, ds, ["elevation"], dt.datetime(2000, 2, 11))

        assert len(image.download_params_list) > 1
        for call in image.download_params_list:
            west, south, east, north = call["region"].coords
            assert (east - west) <= cap_deg + 1e-9
            assert (north - south) <= cap_deg + 1e-9


class TestEeRegion:
    """Tests for `GEE._ee_region`."""

    def test_bbox_rectangle_when_no_region(self, make_gee):
        """With no `region` GeoDataFrame, the clip geometry is an `ee.Geometry.Rectangle`."""
        gee = make_gee(lat_lim=[10.0, 20.0], lon_lim=[-5.0, 5.0])
        region = gee._ee_region()
        assert isinstance(region, _FakeGeometry)
        assert region.coords == [-5.0, 10.0, 5.0, 20.0]
        assert gee._ee_region() is region

    def test_geodataframe_region_uses_create_feature(self, make_gee):
        """A `region` GeoDataFrame is routed through `features.create_feature`."""
        sentinel_gdf = object()
        gee = make_gee(region=sentinel_gdf)
        region = gee._ee_region()
        assert isinstance(region, _FakeGeometry) and region.coords == "from-gdf"


class TestDownloadEndToEnd:
    """An end-to-end `download()` over the fakes."""

    def test_multi_bucket_collection_download(self, make_gee, tmp_path):
        """A monthly CHIRPS request writes one GeoTIFF per month."""
        gee = make_gee(start="2020-06-01", end="2020-07-31", temporal_resolution="monthly",
                       variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]}, scale=5566.0)
        paths = gee.download(progress_bar=False)
        names = sorted(p.name for p in paths)
        assert names == [
            "UCSB-CHG_CHIRPS_DAILY_precipitation_20200601.tif",
            "UCSB-CHG_CHIRPS_DAILY_precipitation_20200701.tif",
        ]
        assert all(p.parent == tmp_path for p in paths)

    def test_non_overlapping_window_writes_nothing(self, make_gee):
        """A request window outside a dataset's extent yields no files."""
        gee = make_gee(start="2020-01-01", end="2020-01-02")
        assert gee.download(progress_bar=False) == []
