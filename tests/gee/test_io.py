"""Tests for `earthlens.gee.io` — FC → DataFrame / GeoDataFrame helpers (M2 + N2)."""

from __future__ import annotations

import io
import ssl

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from earthlens.gee import io as io_module
from earthlens.gee.io import (
    _DEFAULT_RETRIES,
    _TRANSIENT_NETWORK_EXCEPTIONS,
    _retry_on_transient_errors,
    feature_collection_to_dataframe,
    feature_collection_to_gdf,
    feature_collections_to_dataframe,
)


class _FakeFC:
    """Stand-in for an `ee.FeatureCollection`.

    Records `getDownloadURL` / `getInfo` calls and returns scripted payloads.
    """

    def __init__(self, url: str = "http://fake.test/data.csv", info: dict | None = None):
        self._url = url
        self._info = info or {"features": []}
        self.get_download_calls: list[dict] = []
        self.get_info_calls: int = 0
        self.fail_with: BaseException | None = None

    def getDownloadURL(self, filetype, selectors=None):  # noqa: N802
        self.get_download_calls.append({"filetype": filetype, "selectors": selectors})
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None
            raise exc
        return self._url

    def getInfo(self) -> dict:  # noqa: N802
        self.get_info_calls += 1
        return self._info


@pytest.fixture
def fake_read_csv(monkeypatch):
    """Replace `pd.read_csv` with a stub that returns a fixed frame regardless of URL."""

    def _stub(url, *args, **kwargs):
        return pd.DataFrame({"system:index": [0, 1], ".geo": ["a", "b"], "val": [10, 20]})

    monkeypatch.setattr(io_module.pd, "read_csv", _stub)


class TestRetryOnTransientErrors:
    """Tests for the small retry helper (N2)."""

    def test_returns_value_when_fn_succeeds_first_try(self):
        """A function that succeeds on first call is invoked exactly once."""
        calls: list[int] = []

        def _fn():
            calls.append(1)
            return "ok"

        wrapped = _retry_on_transient_errors(_fn, sleep=lambda s: None)
        assert wrapped() == "ok" and len(calls) == 1

    def test_retries_then_succeeds_under_budget(self):
        """A function that fails twice then succeeds returns on the third try."""
        calls: list[int] = []

        def _fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionResetError("flake")
            return "eventually"

        wrapped = _retry_on_transient_errors(_fn, tries=5, sleep=lambda s: None)
        assert wrapped() == "eventually" and len(calls) == 3

    def test_raises_after_budget_exhausted(self):
        """After `tries` failures the underlying exception is re-raised."""
        calls: list[int] = []

        def _fn():
            calls.append(1)
            raise ssl.SSLEOFError("never recovers")

        wrapped = _retry_on_transient_errors(_fn, tries=3, sleep=lambda s: None)
        with pytest.raises(ssl.SSLEOFError):
            wrapped()
        assert len(calls) == 3

    def test_non_transient_exception_propagates_immediately(self):
        """An out-of-whitelist exception is NOT retried."""
        calls: list[int] = []

        def _fn():
            calls.append(1)
            raise RuntimeError("permanent")

        wrapped = _retry_on_transient_errors(_fn, tries=5, sleep=lambda s: None)
        with pytest.raises(RuntimeError, match="permanent"):
            wrapped()
        assert len(calls) == 1

    def test_backoff_doubles_inter_attempt_delay(self):
        """Delay sequence is `initial_delay * backoff**(attempt-1)`."""
        delays: list[float] = []
        calls: list[int] = []

        def _fn():
            calls.append(1)
            raise ConnectionResetError("flake")

        wrapped = _retry_on_transient_errors(
            _fn, tries=4, backoff=2.0, initial_delay=1.0, sleep=delays.append,
        )
        with pytest.raises(ConnectionResetError):
            wrapped()
        assert delays == [1.0, 2.0, 4.0]  # 3 sleeps between the 4 attempts

    def test_default_budget_is_five_per_n2(self):
        """The module-level default `tries` is 5 (N2 trimmed it down from 100)."""
        assert _DEFAULT_RETRIES == 5

    @pytest.mark.parametrize("tries", [0, -1, -100])
    def test_rejects_non_positive_tries(self, tries):
        """`tries < 1` is rejected up-front so the wrapper can't fall through silently."""
        with pytest.raises(ValueError, match="tries must be >= 1"):
            _retry_on_transient_errors(lambda: None, tries=tries)

    def test_resolves_time_sleep_lazily(self, monkeypatch):
        """The wrapper resolves `time.sleep` at call time, not at wrap time."""
        sleeps: list[float] = []
        monkeypatch.setattr(io_module.time, "sleep", sleeps.append)
        calls: list[int] = []

        def _fn():
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionResetError("flake")
            return "ok"

        wrapped = _retry_on_transient_errors(_fn, tries=3)
        assert wrapped() == "ok"
        assert sleeps == [1.0]

    def test_transient_whitelist_includes_expected_classes(self):
        """The transient-error tuple covers SSL / URL / EE / reset classes."""
        for cls in (
            ssl.SSLEOFError,
            ConnectionResetError,
        ):
            assert cls in _TRANSIENT_NETWORK_EXCEPTIONS


class TestFeatureCollectionToDataframe:
    """Tests for `feature_collection_to_dataframe`."""

    def test_drops_system_index_and_geo_columns_when_no_selectors(self, fake_read_csv):
        """Without `selectors`, the synthetic `system:index` and `.geo` columns are stripped."""
        fc = _FakeFC()
        df = feature_collection_to_dataframe(fc)
        assert list(df.columns) == ["val"]
        assert df["val"].tolist() == [10, 20]

    def test_keeps_columns_when_selectors_provided(self, fake_read_csv):
        """With explicit `selectors`, no columns are dropped — caller chose."""
        fc = _FakeFC()
        df = feature_collection_to_dataframe(fc, selectors=["val"])
        # The stub returns its three columns regardless; the function preserves them.
        assert set(df.columns) == {"system:index", ".geo", "val"}
        assert fc.get_download_calls == [{"filetype": "CSV", "selectors": ["val"]}]

    def test_request_uses_uppercase_csv_filetype(self, fake_read_csv):
        """`getDownloadURL` is called with `filetype="CSV"` (the EE-accepted spelling)."""
        fc = _FakeFC()
        feature_collection_to_dataframe(fc)
        assert fc.get_download_calls[0]["filetype"] == "CSV"

    def test_explicit_empty_selectors_forwards_empty_list_and_keeps_columns(
        self, fake_read_csv,
    ):
        """`selectors=[]` is honoured verbatim — neither collapsed to None nor stripped (L3)."""
        fc = _FakeFC()
        df = feature_collection_to_dataframe(fc, selectors=[])
        assert fc.get_download_calls == [{"filetype": "CSV", "selectors": []}]
        # No synthetic-column stripping when selectors is explicitly provided.
        assert set(df.columns) == {"system:index", ".geo", "val"}

    def test_none_selectors_strips_synthetic_columns(self, fake_read_csv):
        """`selectors=None` (default) hits the request with `None` and strips the synthetic cols."""
        fc = _FakeFC()
        df = feature_collection_to_dataframe(fc, selectors=None)
        assert fc.get_download_calls == [{"filetype": "CSV", "selectors": None}]
        assert list(df.columns) == ["val"]


class TestFeatureCollectionsToDataframe:
    """Tests for `feature_collections_to_dataframe`."""

    def test_empty_iterable_returns_empty_frame(self):
        """Zero FCs in → empty DataFrame out (no pool spin-up)."""
        out = feature_collections_to_dataframe([])
        assert out.empty

    def test_concatenates_per_fc_frames(self, fake_read_csv):
        """N FCs → one frame column-concatenated from N per-FC frames."""
        out = feature_collections_to_dataframe([_FakeFC(), _FakeFC(), _FakeFC()])
        # The stub frame has one kept column ("val") per FC, all named identically:
        # `pd.concat(..., axis=1)` keeps every contribution side-by-side.
        assert out.shape[0] == 2  # two rows
        assert out.shape[1] == 3  # three columns (one per FC)

    def test_retries_on_transient_then_succeeds(self, monkeypatch, fake_read_csv):
        """A transient failure on the first call retries and the FC eventually succeeds."""
        sleeps: list[float] = []
        monkeypatch.setattr(io_module.time, "sleep", sleeps.append)

        fc = _FakeFC()
        fc.fail_with = ConnectionResetError("flake")
        out = feature_collections_to_dataframe([fc], pool_size=1, tries=3)
        assert not out.empty
        # First call raised → retried → succeeded; one sleep recorded.
        assert sleeps == [1.0]


class TestFeatureCollectionToGdf:
    """Tests for `feature_collection_to_gdf`."""

    def test_round_trips_a_minimal_point_payload(self):
        """A GeoJSON-shaped `getInfo()` payload → a `GeoDataFrame` with shapely geoms."""
        payload = {
            "features": [
                {
                    "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                    "properties": {"name": "a", "v": 10},
                },
                {
                    "geometry": {"type": "Point", "coordinates": [3.0, 4.0]},
                    "properties": {"name": "b", "v": 20},
                },
            ]
        }
        gdf = feature_collection_to_gdf(_FakeFC(info=payload))
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 2
        assert gdf.iloc[0].geometry.equals(Point(1.0, 2.0))
        assert list(gdf["name"]) == ["a", "b"]

    def test_sets_modern_crs_string(self):
        """`crs=4326` becomes `"EPSG:4326"`, not the legacy `{"init": ...}` shape."""
        payload = {"features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}]}
        gdf = feature_collection_to_gdf(_FakeFC(info=payload), crs=4326)
        assert str(gdf.crs).upper() == "EPSG:4326"

    def test_passes_through_string_crs(self):
        """A string `crs` is used verbatim — useful for non-EPSG specifications."""
        payload = {"features": [{"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}]}
        gdf = feature_collection_to_gdf(_FakeFC(info=payload), crs="EPSG:3857")
        assert str(gdf.crs).upper() == "EPSG:3857"

    def test_handles_empty_feature_list(self):
        """An empty features list returns an empty GeoDataFrame with the CRS set."""
        gdf = feature_collection_to_gdf(_FakeFC(info={"features": []}))
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 0
