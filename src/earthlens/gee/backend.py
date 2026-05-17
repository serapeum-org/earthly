"""Google Earth Engine backend — :class:`GEE`, an :class:`AbstractDataSource`.

Downloads imagery from Google Earth Engine. A request is `{asset_id:
[band, ...], ...}` (the addressable units of an EE dataset are *bands*,
and one image carries many at once), plus a date range, a bbox (or a
`GeoDataFrame` region), a temporal-compositing resolution
(`"raw"`/`"daily"`/`"monthly"`/`"yearly"`), and an output pixel `scale`
in metres. The asset ids and band metadata are resolved through
:class:`earthlens.gee.Catalog` (loaded from the per-category YAMLs
under `src/earthlens/gee/catalog/`).

Per `(asset, band-set, time-bucket)` the pipeline is:

* :meth:`_build_collection` — `ee.ImageCollection(asset_id)` (or the
  single `ee.Image` wrapped in one), `.filterDate(...)`,
  `.filterBounds(region)`, `.select(bands)`. Pure: no I/O.
* :meth:`_composite` — split the request window into buckets at the
  requested cadence and collapse each with the dataset's
  `default_reducer` (or the constructor `reducer` override) — `mean`
  for continuous fields / rates, `median` for cloud-screened optical
  scenes, `mosaic` for tiled / annual static maps. Yields one
  `ee.Image` per bucket.
* :meth:`_api` — export the bucket image via the configured
  `export_via`: `"url"` (the default) computes the request's pixel
  dimensions and refuses if either axis exceeds Earth Engine's 32768-px
  synchronous limit (a clear, actionable `ValueError`), else
  `image.getDownloadURL({..., "format": "GEO_TIFF"})` → `requests.get`
  → a GeoTIFF under the output directory; multi-band responses (which
  Earth Engine returns as a zip of per-band tifs) are unpacked through
  `pyramids.dataset.Dataset.from_archive` into a single multi-band tif.
  `"drive"` / `"gcs"` queue an asynchronous
  `ee.batch.Export.image.to{Drive,CloudStorage}` task (`maxPixels` only,
  no 32768-px cap), poll it to completion, and return a `"drive://…"` /
  `"gs://…"` destination string (the file is left in the Drive folder /
  GCS bucket for the caller to pull).

Authentication is a one-time `ee.Initialize` against a *registered*
Cloud project, performed by :meth:`_initialize` via
:class:`earthlens.gee.auth.EarthEngineAuth` (service-account key) or, if
no key is given, an interactive `ee.Authenticate()` against an explicit
`project`. Credential / registration failures surface as
:class:`AuthenticationError`.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Literal

import ee
import pandas as pd
import requests
from loguru import logger
from pyramids.dataset import Dataset as PyramidsDataset
from pyramids.dataset.merge import merge_rasters
from tqdm import tqdm

from earthlens.base import AbstractDataSource, SpatialExtent, TemporalExtent
from earthlens.gee._helpers import (
    EE_MAX_DIMENSION,
    reduce_collection,
    slug_asset_id,
    split_aoi_for_url,
    wait_for_task,
)
from earthlens.gee.auth import AuthenticationError, EarthEngineAuth
from earthlens.gee.catalog import Catalog, Dataset
from earthlens.gee.features import create_feature
from earthlens.gee.jobs import TaskInfo, _op_to_taskinfo

if TYPE_CHECKING:  # pragma: no cover - typing only
    from geopandas import GeoDataFrame

__all__ = ["GEE", "AuthenticationError"]

# `temporal_resolution` → pandas frequency alias for the per-bucket
# date range. `"raw"` is special-cased (one bucket spanning the whole
# request window).
_RESOLUTION_FREQ: dict[str, str] = {"daily": "D", "monthly": "MS", "yearly": "YS"}

_DEFAULT_HTTP_TIMEOUT_S: float = 300.0
_ZIP_MAGIC: bytes = b"PK\x03\x04"


def _is_interactive_environment() -> bool:
    """Return `True` when this process can plausibly run an interactive auth flow.

    `ee.Authenticate()` opens a browser and waits for the user to paste
    a token; on a headless box (CI, Docker, remote shell without
    `DISPLAY`) it hangs indefinitely or fails with an unhelpful "no
    browser" error. The check returns `True` when both `stdin` is a
    TTY and a display is available (or we're on Windows / macOS, where
    `DISPLAY` is not the relevant signal).

    Tests can force the non-interactive branch via the
    `EARTHLENS_FORCE_HEADLESS=1` environment variable.
    """
    if os.environ.get("EARTHLENS_FORCE_HEADLESS") == "1":
        return False
    try:
        if not sys.stdin.isatty():
            return False
    except (AttributeError, ValueError):  # pragma: no cover - closed-stdin edge
        return False
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return False
    return True


class GEE(AbstractDataSource):
    """Google Earth Engine data source.

    Args:
        start: Inclusive start date string (parsed with `fmt`).
        end: Inclusive end date string.
        variables: Mapping `{asset_id: [band, ...]}` — each `asset_id`
            must be a key of :attr:`Catalog.datasets` and each band a
            band of that dataset (see `src/earthlens/gee/catalog/`).
        lat_lim: `[lat_min, lat_max]` in degrees.
        lon_lim: `[lon_min, lon_max]` in degrees.
        temporal_resolution: How to composite over time — `"raw"` (one
            image: reduce the whole window), `"daily"`, `"monthly"`, or
            `"yearly"`. Defaults to `"raw"`.
        path: Output directory (created if absent). Defaults to the cwd.
        fmt: `strptime` format for `start` / `end`. Defaults to `"%Y-%m-%d"`.
        service_account: Service-account email for authentication. If
            given, `service_key` is required.
        service_key: Path to the service-account JSON key file, or the
            JSON content as a string.
        project: Cloud project id to scope Earth Engine calls to. If
            omitted, read from the service-account key's `project_id`;
            required when no service account is given.
        scale: Output pixel size in metres. If omitted, each dataset's
            nominal `spatial_resolution` is used.
        crs: Output CRS (EPSG code string). Defaults to `"EPSG:4326"`.
        reducer: Override the per-dataset `default_reducer` for the
            temporal composite (`mean` / `median` / `min` / `max` /
            `mode` / `mosaic` / `sum`). `None` (the default) uses each
            dataset's own `default_reducer`.
        export_via: How to get pixels out — `"url"` (synchronous
            `getDownloadURL`, capped at 32768 px per axis; the default),
            `"drive"` (asynchronous `ee.batch.Export.image.toDrive`;
            requires `drive_folder`), `"gcs"` (asynchronous
            `ee.batch.Export.image.toCloudStorage`; requires `gcs_bucket`),
            or `"asset"` (asynchronous `ee.batch.Export.image.toAsset`;
            requires `asset_id`).
        drive_folder: Google Drive folder name for `export_via="drive"`.
        gcs_bucket: Cloud Storage bucket name for `export_via="gcs"` (the
            service account needs `roles/storage.objectAdmin` on it).
        asset_id: Parent folder asset id for `export_via="asset"` (e.g.
            `"projects/my-project/assets/my-folder"`). Each export's asset
            is created at `<asset_id>/<prefix>`.
        region: Optional `GeoDataFrame` to clip to precisely; when given
            it supersedes the lat/lon bbox for the actual clip (the bbox
            is still used for the `"url"` size estimate).
        http_timeout: Timeout in seconds for the synchronous
            `getDownloadURL` HTTP request (`export_via="url"`). Defaults
            to 300 s.
        auto_split: For `export_via="url"`, when the estimated request
            exceeds Earth Engine's 32768-px per-axis cap, automatically
            split the AOI into tiles each within the cap, download each
            tile separately, and mosaic them back into a single GeoTIFF
            via `pyramids.dataset.merge.merge_rasters`. Defaults to
            `False` — the previous behaviour, which raises `ValueError`
            with an actionable message.
        discover_extent: When the catalog entry's `extent.end_date`
            (and/or `start_date`) is missing, fall back to an EE-side
            `reduceColumns(minMax)` over `system:time_start` to discover
            the collection's actual extent and clamp the request window
            to it. The discovered extent is cached per asset for the
            lifetime of the `GEE` instance. Defaults to `False` — the
            previous behaviour, which uses `now() + 1 day` as the upper
            bound for open-ended catalog entries.
        wait_for_export: For asynchronous sinks (`export_via="drive"` /
            `"gcs"` / `"asset"`), whether `download()` blocks until
            each task reaches a terminal state. Defaults to `True`
            (the historical behaviour — returns the destination
            string). When `False`, each task is started and
            `download()` returns a list of :class:`TaskInfo` objects
            so the caller can track them asynchronously via
            :mod:`earthlens.gee.jobs`. Ignored for `export_via="url"`,
            which is always synchronous.

    Raises:
        AuthenticationError: If Earth Engine cannot be initialised
            (missing/invalid key, unregistered project, missing IAM role).
        ValueError: At construction for a bad `export_via` (or `"drive"`
            without `drive_folder` / `"gcs"` without `gcs_bucket` /
            `"asset"` without `asset_id`); from the parent on a bad date
            range; from :meth:`_check_input_dates` on an unknown
            `temporal_resolution`; from :meth:`_api` on a missing scale
            or an oversized `"url"` request (unless `auto_split=True`);
            from :meth:`_download_dataset` on an unknown asset id or band.
        NotImplementedError: From :meth:`download` when `aggregate=` is
            passed (not yet supported).
        RuntimeError: From :meth:`_api` if a `"drive"` / `"gcs"` export
            task does not complete.

    Examples:
        - Construct against a service account and download SRTM over a small bbox:
            ```python
            >>> from earthlens.gee import GEE  # doctest: +SKIP
            >>> gee = GEE(  # doctest: +SKIP
            ...     start="2000-02-11", end="2000-02-12",
            ...     variables={"USGS/SRTMGL1_003": ["elevation"]},
            ...     lat_lim=[29.9, 30.0], lon_lim=[31.2, 31.3],
            ...     path="data/gee",
            ...     service_account="sa@my-project.iam.gserviceaccount.com",
            ...     service_key="/path/to/key.json",
            ... )
            >>> paths = gee.download()  # doctest: +SKIP
            ```
    """

    def __init__(
        self,
        start: str,
        end: str,
        variables: dict[str, list[str]],
        lat_lim: list[float],
        lon_lim: list[float],
        temporal_resolution: str = "raw",
        path: Path | str = "",
        fmt: str = "%Y-%m-%d",
        *,
        service_account: str | None = None,
        service_key: str | None = None,
        project: str | None = None,
        scale: float | None = None,
        crs: str = "EPSG:4326",
        reducer: str | None = None,
        export_via: Literal["url", "drive", "gcs", "asset"] = "url",
        drive_folder: str | None = None,
        gcs_bucket: str | None = None,
        asset_id: str | None = None,
        region: GeoDataFrame | None = None,
        http_timeout: float | None = None,
        auto_split: bool = False,
        discover_extent: bool = False,
        wait_for_export: bool = True,
    ):
        # Validate the pure (no-I/O) config first so a bad `export_via`
        # fails fast, before paying for the ~3.3 s cold-cache catalog
        # parse (M3 in pr-diff-review).
        if export_via not in {"url", "drive", "gcs", "asset"}:
            raise ValueError(
                f"export_via must be 'url', 'drive', 'gcs', or 'asset', "
                f"got {export_via!r}"
            )
        if export_via == "drive" and not drive_folder:
            raise ValueError("export_via='drive' requires drive_folder=")
        if export_via == "gcs" and not gcs_bucket:
            raise ValueError("export_via='gcs' requires gcs_bucket=")
        if export_via == "asset" and not asset_id:
            raise ValueError(
                "export_via='asset' requires asset_id= (the parent folder "
                "asset, e.g. 'projects/my-project/assets/my-folder')"
            )

        # These must be set before `super().__init__` runs, because the
        # parent constructor immediately calls `self._initialize()` (and
        # `_create_grid` / `_check_input_dates`), which read them.
        self.catalog = Catalog()
        self._service_account = service_account
        self._service_key = service_key
        self._project = project
        self.project: str | None = None
        self.scale = scale
        self.crs = crs
        self.reducer = reducer
        self.export_via = export_via
        self.drive_folder = drive_folder
        self.gcs_bucket = gcs_bucket
        self.asset_id = asset_id
        self.region = region
        self.http_timeout = (
            float(http_timeout) if http_timeout is not None else _DEFAULT_HTTP_TIMEOUT_S
        )
        self.auto_split = bool(auto_split)
        self.discover_extent = bool(discover_extent)
        self.wait_for_export = bool(wait_for_export)
        self._extent_cache: dict[str, tuple[dt.datetime | None, dt.datetime | None]] = {}
        self._ee_geometry = None  # lazily built in `_ee_region`

        super().__init__(
            start=start,
            end=end,
            variables=variables,
            temporal_resolution=temporal_resolution,
            lat_lim=lat_lim,
            lon_lim=lon_lim,
            fmt=fmt,
            path=path,
        )

    def _initialize(self) -> Any:
        """Authenticate and initialise the Earth Engine connection.

        Uses a service-account key when `service_account` + `service_key`
        were given (via :class:`EarthEngineAuth`); otherwise runs
        `ee.Authenticate()` and `ee.Initialize(project=...)` against the
        explicit `project`. The `ee.Authenticate()` flow is interactive
        (opens a browser, waits for the user to paste a token), so the
        `project`-only path fast-fails with `AuthenticationError` when
        the current process has no TTY or no `DISPLAY` (CI, headless
        Docker, remote shell). Use service-account auth for
        non-interactive use. The resolved project id is stored on
        :attr:`project`.

        Returns:
            The `ee` module (truthy, so the parent stores it as
            `self.client`).

        Raises:
            AuthenticationError: If credentials are missing/invalid, no
                project can be resolved, the current process is
                non-interactive but only a `project=` was given, the
                project is not registered for Earth Engine, or the
                service account lacks the required IAM role on it.
        """
        if self._service_account and self._service_key:
            self.project = EarthEngineAuth.initialize(
                self._service_account, self._service_key, self._project
            )
            return ee
        if not self._project:
            raise AuthenticationError(
                "the GEE backend needs either service_account + service_key, "
                "or an explicit project= (with cached/ADC credentials). See "
                "https://developers.google.com/earth-engine/guides/service_account."
            )
        if not _is_interactive_environment():
            raise AuthenticationError(
                f"cannot run interactive ee.Authenticate() for project "
                f"{self._project!r} in a non-interactive environment "
                "(no TTY / no DISPLAY). Use service-account auth instead: "
                "pass service_account= + service_key= to GEE(...). See "
                "https://developers.google.com/earth-engine/guides/service_account."
            )
        try:
            ee.Authenticate()
            ee.Initialize(project=self._project)
        except ee.EEException as exc:
            message = str(exc)
            if "not registered to use Earth Engine" in message:
                raise AuthenticationError(
                    f"Cloud project {self._project!r} is not registered to use "
                    "Earth Engine. Register it at "
                    "https://code.earthengine.google.com/register, then retry."
                ) from exc
            raise AuthenticationError(
                f"Earth Engine initialisation failed for project "
                f"{self._project!r}: {message}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - re-raised as AuthenticationError
            raise AuthenticationError(
                f"Earth Engine initialisation failed for project "
                f"{self._project!r}: {exc}"
            ) from exc
        self.project = self._project
        return ee

    def _create_grid(self, lat_lim: list[float], lon_lim: list[float]) -> SpatialExtent:
        """Build the request bounding box.

        Earth Engine has no fixed native grid like ERA5's 0.125°, so the
        spatial cell size is the user's `scale` (metres), kept on
        :attr:`scale` rather than on :attr:`SpatialExtent.resolution`
        (which is a *degrees* field).

        Args:
            lat_lim: `[lat_min, lat_max]` in degrees.
            lon_lim: `[lon_min, lon_max]` in degrees.

        Returns:
            SpatialExtent: The bbox (no `resolution`).
        """
        return SpatialExtent.from_pairs(lat_lim=lat_lim, lon_lim=lon_lim)

    def _check_input_dates(
        self, start: str, end: str, temporal_resolution: str, fmt: str
    ) -> TemporalExtent:
        """Parse the date range and produce the per-bucket date index.

        Args:
            start: Inclusive start date string.
            end: Inclusive end date string.
            temporal_resolution: `"raw"` (one bucket spanning the whole
                window), `"daily"` (`freq="D"`), `"monthly"` (`"MS"`),
                or `"yearly"` (`"YS"`).
            fmt: `strptime` format applied to `start` / `end`.

        Returns:
            TemporalExtent: `start_date`, `end_date`, `resolution` (the
            string passed in), and `dates` — a :class:`pandas.DatetimeIndex`
            with one entry per time bucket (a single entry for `"raw"`).

        Raises:
            ValueError: If `temporal_resolution` is not one of `"raw"`,
                `"daily"`, `"monthly"`, `"yearly"`, or if `start > end`.
        """
        start_dt = dt.datetime.strptime(start, fmt)
        end_dt = dt.datetime.strptime(end, fmt)
        if temporal_resolution == "raw":
            dates = pd.DatetimeIndex([start_dt])
        elif temporal_resolution in _RESOLUTION_FREQ:
            dates = pd.date_range(start_dt, end_dt, freq=_RESOLUTION_FREQ[temporal_resolution])
        else:
            raise ValueError(
                "temporal_resolution must be 'raw', 'daily', 'monthly', or "
                f"'yearly', got {temporal_resolution!r}"
            )
        return TemporalExtent(
            start_date=start_dt,
            end_date=end_dt,
            resolution=temporal_resolution,
            dates=dates,
        )

    def download(
        self, progress_bar: bool = True, aggregate: Any = None
    ) -> list[Path | str | TaskInfo]:
        """Download every requested band-set of every requested dataset.

        Args:
            progress_bar: Show a per-bucket `tqdm` bar. Defaults to `True`.
            aggregate: Accepted for signature parity with the ECMWF
                backend; not yet supported — passing a non-`None` value
                raises `NotImplementedError` (see plan task `M3`).

        Returns:
            One entry per `(dataset, band-set, time-bucket)`. The
            shape depends on the sink:

            * `export_via="url"` — :class:`pathlib.Path` to the
              written GeoTIFF (always synchronous).
            * `export_via="drive"` / `"gcs"` / `"asset"` with the
              default `wait_for_export=True` — destination string
              (`"drive://<folder>/<prefix>"` / `"gs://<bucket>/<prefix>"` /
              `"ee://<asset_id>/<prefix>"`), populated only once
              the task reaches `COMPLETED`.
            * `export_via="drive"` / `"gcs"` / `"asset"` with
              `wait_for_export=False` — :class:`TaskInfo` captured
              at submission time; follow up via
              :mod:`earthlens.gee.jobs` (`get_task_status`,
              `wait_for_task_id`, etc.).

        Raises:
            NotImplementedError: If `aggregate` is not `None`.
            ValueError: On an unknown asset id, an unknown band, or an
                oversized `"url"` request (see :meth:`_api`).
            RuntimeError: If a `"drive"` / `"gcs"` / `"asset"` export
                task fails. Only raised when `wait_for_export=True`;
                in the non-blocking mode the caller handles failures
                themselves via `wait_for_task_id`.

        Examples:
            - Download one band, one image (needs network + credentials):
                ```python
                >>> gee = GEE(  # doctest: +SKIP
                ...     start="2020-06-01", end="2020-06-30",
                ...     temporal_resolution="monthly",
                ...     variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]},
                ...     lat_lim=[29.0, 30.0], lon_lim=[31.0, 32.0],
                ...     path="data/gee", scale=5566,
                ...     service_account="sa@p.iam.gserviceaccount.com",
                ...     service_key="/path/to/key.json",
                ... )
                >>> paths = gee.download()  # doctest: +SKIP
                >>> [p.name for p in paths]  # doctest: +SKIP
                ['UCSB-CHG_CHIRPS_DAILY_precipitation_20200601.tif']

                ```
            - `aggregate=` is not yet supported and is rejected up front:
                ```python
                >>> gee = GEE(  # doctest: +SKIP
                ...     start="2020-06-01", end="2020-06-01",
                ...     variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]},
                ...     lat_lim=[29.0, 30.0], lon_lim=[31.0, 32.0],
                ...     scale=5566, project="my-project",
                ... )
                >>> gee.download(aggregate=object())  # doctest: +SKIP
                Traceback (most recent call last):
                    ...
                NotImplementedError: aggregate= is not yet supported ...

                ```

        See Also:
            earthlens.gee.Catalog: Resolves the `{asset_id: [band, ...]}`
                request against `src/earthlens/gee/catalog/`.
            earthlens.gee.auth.EarthEngineAuth: Performs the one-time
                `ee.Initialize` used by :meth:`_initialize`.
        """
        if aggregate is not None:
            raise NotImplementedError(
                "aggregate= is not yet supported by the GEE backend "
                "(planned — see the GEE plan task M3)."
            )
        outputs: list[Path | str | TaskInfo] = []
        for asset_id, bands in self.vars.items():
            outputs.extend(self._download_dataset(asset_id, list(bands), progress_bar))
        return outputs

    def _download_dataset(
        self, asset_id: str, bands: list[str], progress_bar: bool = True
    ) -> list[Path | str | TaskInfo]:
        """Download one dataset's requested bands across the time buckets.

        Validates `asset_id` and every band against the catalog, clamps
        the request window to the dataset's published extent, builds the
        filtered collection, composites it per time bucket, and writes
        each bucket via :meth:`_api`.

        Args:
            asset_id: An Earth Engine asset id present in the catalog.
            bands: Band ids of that dataset to download.
            progress_bar: Show a `tqdm` bar over the time buckets.

        Returns:
            The list of GeoTIFF paths written for this dataset (possibly
            empty if the request window does not overlap the dataset's
            extent).

        Raises:
            ValueError: If `asset_id` or any band is not in the catalog,
                or if a write fails the size guard (see :meth:`_api`).
        """
        var_info = self.catalog.get_dataset(asset_id)
        for band in bands:
            var_info.get_band(band)  # raises ValueError with a suggestion

        start, end = self._clamp_window_to_extent(var_info)
        if start is None:
            logger.warning(
                f"{asset_id}: request window does not overlap the dataset's "
                f"extent ({var_info.extent.start_date}..{var_info.extent.end_date}); "
                "skipping."
            )
            return []

        collection = self._build_collection(var_info, bands, start, end)
        buckets = list(self._composite(collection, var_info, start, end))
        iterator: Iterable = buckets
        if progress_bar:
            iterator = tqdm(buckets, desc=f"{asset_id} [{','.join(bands)}]", unit="img")
        return [self._api(image, var_info, bands, when) for when, image in iterator]

    def _build_collection(
        self, var_info: Dataset, bands: list[str], start: dt.datetime, end: dt.datetime
    ):
        """Build the filtered, band-selected `ee.ImageCollection`.

        For an `ee_type="image"` dataset the single `ee.Image` is wrapped
        in a one-element collection so the rest of the pipeline is
        uniform. `filterDate` uses a half-open `[start, end]` window
        (Earth Engine convention); the `end` passed here is already
        bumped by one day by :meth:`_clamp_window_to_extent` so the
        user's inclusive end date is covered.

        Args:
            var_info: The catalog entry.
            bands: Band ids to `.select(...)`.
            start: Inclusive window start (clamped).
            end: Exclusive window end (clamped, already +1 day).

        Returns:
            The `ee.ImageCollection`.
        """
        if var_info.is_image_collection:
            collection = ee.ImageCollection(var_info.id).filterDate(
                start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
            )
        else:
            # A static image: no temporal filtering (the asset may not
            # carry a `system:time_start` inside the request window).
            collection = ee.ImageCollection([ee.Image(var_info.id)])
        return collection.filterBounds(self._ee_region()).select(list(bands))

    def _composite(
        self, collection, var_info: Dataset, start: dt.datetime, end: dt.datetime
    ):
        """Yield one `ee.Image` per time bucket.

        For `temporal_resolution="raw"` (and for static `ee_type="image"`
        datasets) there is a single bucket spanning the whole clamped
        window. Otherwise the window is split into daily / monthly /
        yearly buckets and each is collapsed with the dataset's
        `default_reducer` (or the constructor `reducer` override).

        Args:
            collection: The filtered `ee.ImageCollection` from
                :meth:`_build_collection`.
            var_info: The catalog entry (its `default_reducer`).
            start: Inclusive window start (clamped).
            end: Exclusive window end (clamped, already +1 day).

        Yields:
            `(timestamp, ee.Image)` pairs — `timestamp` is the bucket
            start (a :class:`datetime.datetime`), used in the filename.
        """
        reducer = self.reducer or var_info.default_reducer
        if self.temporal_resolution == "raw" or not var_info.is_image_collection:
            yield start, reduce_collection(collection, reducer)
            return
        freq = _RESOLUTION_FREQ[self.temporal_resolution]
        bucket_starts = pd.date_range(start, end, freq=freq, inclusive="left")
        for i, bucket_start in enumerate(bucket_starts):
            bucket_end = bucket_starts[i + 1] if i + 1 < len(bucket_starts) else pd.Timestamp(end)
            window = collection.filterDate(
                bucket_start.strftime("%Y-%m-%d"), bucket_end.strftime("%Y-%m-%d")
            )
            yield bucket_start.to_pydatetime(), reduce_collection(window, reducer)

    def _api(
        self, image, var_info: Dataset, bands: list[str], when: dt.datetime
    ) -> Path | str | TaskInfo:
        """Export one composited `ee.Image` via the configured `export_via`.

        For `export_via="url"`: estimate the request's pixel dimensions
        from the bbox and `scale`; if either axis exceeds Earth Engine's
        32768-px synchronous limit, either auto-split + mosaic via
        pyramids (when `auto_split=True`) or raise a `ValueError`
        pointing the user at a coarser `scale`, a smaller bbox,
        `export_via="drive"`, or `auto_split=True`. Otherwise request a
        GeoTIFF via `getDownloadURL` and stream it to disk as
        `<asset-slug>_<bands>_<YYYYMMDD>.tif`. For `export_via="drive"` /
        `"gcs"` / `"asset"`: queue an
        `ee.batch.Export.image.to{Drive,CloudStorage,Asset}` task, poll
        it to completion (no synchronous size cap, just `maxPixels`),
        and return a destination string — for Drive / GCS the file is
        left in the destination for the caller to pull; for `"asset"`
        a new EE asset is created at `<asset_id>/<prefix>`.

        Args:
            image: The `ee.Image` to export.
            var_info: The catalog entry (for the asset slug and the
                fallback `spatial_resolution`).
            bands: The band ids in `image` (used in the filename / prefix).
            when: The bucket timestamp (used in the filename / prefix).

        Returns:
            For `"url"`: the :class:`pathlib.Path` of the written GeoTIFF.
            For `"drive"` / `"gcs"` / `"asset"`: a destination string
            (`"drive://<folder>/<prefix>"` / `"gs://<bucket>/<prefix>"` /
            `"ee://<asset_id>/<prefix>"`).

        Raises:
            ValueError: If no output scale can be resolved, or (for
                `"url"` with `auto_split=False`) the estimated request
                exceeds the 32768-px limit.
            RuntimeError: If Earth Engine returns a zip instead of a
                GeoTIFF (`"url"`), or a `"drive"` / `"gcs"` / `"asset"`
                export task does not complete.
        """
        scale = self.scale or var_info.spatial_resolution
        if scale is None:
            raise ValueError(
                f"no output scale for {var_info.id}: pass scale= (metres) to "
                "GEE(...) — the catalog has no nominal spatial_resolution for it."
            )
        prefix = f"{slug_asset_id(var_info.id)}_{'-'.join(bands)}_{when:%Y%m%d}"
        region = self._ee_region()
        if self.export_via == "url":
            return self._export_via_url(image, var_info, float(scale), region, prefix)
        return self._export_via_batch(image, float(scale), region, prefix)

    def _export_via_url(self, image, var_info: Dataset, scale: float, region, prefix: str) -> Path:
        """Fetch a GeoTIFF from `image.getDownloadURL`; enforce the 32768-px cap.

        Earth Engine returns a single GeoTIFF when one band is exported and
        a zip archive of per-band GeoTIFFs when several are. Both shapes
        are routed through pyramids: single tifs via :meth:`Dataset.from_bytes`
        (writes the in-memory body to a `/vsimem/` path then materialises it
        on disk), zips via :meth:`Dataset.from_archive` (chained `/vsizip/`,
        merging members into one multi-band tif).

        Oversized AOIs (either axis above :data:`EE_MAX_DIMENSION` px at
        `scale`) take one of two paths: when `auto_split=True` was passed
        to the constructor, the bbox is tiled, each tile is downloaded
        individually, and the tiles are mosaicked into one GeoTIFF via
        :func:`pyramids.dataset.merge.merge_rasters`; otherwise a
        `ValueError` is raised with a coarser-scale / smaller-bbox /
        `export_via="drive"` hint.
        """
        width_px, height_px = self.space.estimate_pixel_dims(scale)
        if max(width_px, height_px) > EE_MAX_DIMENSION:
            if self.auto_split:
                return self._auto_split_and_download(image, var_info, scale, prefix)
            raise ValueError(
                f"{var_info.id}: the requested AOI at scale={scale} m is about "
                f"{width_px}x{height_px} px, over Earth Engine's "
                f"{EE_MAX_DIMENSION}-px per-axis limit for synchronous downloads. "
                "Use a coarser scale, a smaller bbox, export_via='drive', or "
                "auto_split=True."
            )
        return self._download_one_url_tile(image, region, scale, prefix)

    def _download_one_url_tile(
        self, image, region, scale: float, prefix: str
    ) -> Path:
        """Issue one `getDownloadURL` request → tif at `<prefix>.tif`.

        Single-tile worker shared by the small-AOI path and the
        auto-split loop. Stripped of size-checking — callers are
        expected to have already verified that the request fits the
        Earth Engine synchronous limit.
        """
        url = image.getDownloadURL(
            {"scale": scale, "crs": self.crs, "region": region, "format": "GEO_TIFF"}
        )
        target = self.root_dir / f"{prefix}.tif"
        response = requests.get(url, timeout=self.http_timeout)
        response.raise_for_status()
        body = response.content
        if body[:4] == _ZIP_MAGIC:
            zip_path = self.root_dir / f"{prefix}.zip"
            zip_path.write_bytes(body)
            try:
                PyramidsDataset.from_archive(
                    zip_path,
                    kind="zip",
                    member_glob="*.tif",
                    path=str(target),
                )
            finally:
                zip_path.unlink(missing_ok=True)
        else:
            ds = PyramidsDataset.from_bytes(body, suffix=".tif")
            ds.to_file(str(target))
        logger.info(f"Wrote {target} ({len(body)} bytes)")
        return target

    def _auto_split_and_download(
        self, image, var_info: Dataset, scale: float, prefix: str
    ) -> Path:
        """Tile an oversized AOI, download each tile, mosaic into one GeoTIFF.

        Only reachable when `auto_split=True` was passed to the
        constructor and the full AOI exceeds :data:`EE_MAX_DIMENSION` px
        per axis. The bbox is split with :func:`split_aoi_for_url`, each
        sub-extent is downloaded via :meth:`_download_one_url_tile`, and
        the per-tile tifs are mosaicked into `<prefix>.tif` with
        :func:`pyramids.dataset.merge.merge_rasters`. Per-tile tifs are
        deleted on success.
        """
        sub_extents = split_aoi_for_url(self.space, scale)
        logger.info(
            f"{var_info.id}: AOI exceeds {EE_MAX_DIMENSION}-px per-axis cap at "
            f"scale={scale} m; auto-splitting into {len(sub_extents)} tile(s)."
        )
        tile_paths: list[Path] = []
        for k, sub in enumerate(sub_extents):
            sub_region = ee.Geometry.Rectangle(
                [sub.west, sub.south, sub.east, sub.north]
            )
            sub_prefix = f"{prefix}_tile_{k:04d}"
            tile_paths.append(
                self._download_one_url_tile(image, sub_region, scale, sub_prefix)
            )
        target = self.root_dir / f"{prefix}.tif"
        merge_rasters([str(p) for p in tile_paths], str(target))
        for p in tile_paths:
            p.unlink(missing_ok=True)
        logger.info(
            f"Stitched {len(tile_paths)} tile(s) into {target} via pyramids."
        )
        return target

    def _export_via_batch(self, image, scale: float, region, prefix: str) -> str | TaskInfo:
        """Queue an `ee.batch.Export.image.to{Drive,CloudStorage,Asset}` task.

        When `wait_for_export=True` (the default) blocks until the task
        reaches a terminal state via `wait_for_task` and returns the
        destination URL (`drive://...` / `gs://...` / `ee://...`).
        When `wait_for_export=False` returns a :class:`TaskInfo`
        immediately so the caller can track the task asynchronously via
        :mod:`earthlens.gee.jobs`.
        """
        common = {
            "image": image,
            "description": prefix[:100],
            "region": region,
            "scale": scale,
            "crs": self.crs,
            "maxPixels": 1e13,
        }
        if self.export_via == "drive":
            task = ee.batch.Export.image.toDrive(
                folder=self.drive_folder, fileNamePrefix=prefix, **common
            )
            destination = f"drive://{self.drive_folder}/{prefix}"
        elif self.export_via == "gcs":
            task = ee.batch.Export.image.toCloudStorage(
                bucket=self.gcs_bucket, fileNamePrefix=prefix, **common
            )
            destination = f"gs://{self.gcs_bucket}/{prefix}"
        else:
            # The asset sink uses `assetId` instead of `fileNamePrefix` —
            # each export creates one asset at `<self.asset_id>/<prefix>`.
            target_asset = f"{self.asset_id.rstrip('/')}/{prefix}"
            task = ee.batch.Export.image.toAsset(assetId=target_asset, **common)
            destination = f"ee://{target_asset}"
        if not self.wait_for_export:
            task.start()
            info = _op_to_taskinfo(task.status())
            logger.info(
                f"Submitted {self.export_via} export {info.id} "
                f"({info.description}); track via earthlens.gee.jobs."
            )
            return info
        wait_for_task(task, progress_bar=True)
        logger.info(f"Exported {destination} (pull it from the {self.export_via} destination)")
        return destination

    def _ee_region(self):
        """Return the `ee.Geometry` to clip / filter requests to.

        Uses the constructor `region` `GeoDataFrame` (converted via
        :func:`earthlens.gee.features.create_feature`) when given,
        otherwise an `ee.Geometry.Rectangle` built from the lat/lon
        bbox. Computed once and cached.

        Returns:
            The `ee.Geometry`.
        """
        if self._ee_geometry is None:
            if self.region is not None:
                self._ee_geometry = create_feature(self.region).geometry()
            else:
                self._ee_geometry = ee.Geometry.Rectangle(
                    [
                        self.space.longitude_min,
                        self.space.latitude_min,
                        self.space.longitude_max,
                        self.space.latitude_max,
                    ]
                )
        return self._ee_geometry

    def _clamp_window_to_extent(
        self, var_info: Dataset
    ) -> tuple[dt.datetime | None, dt.datetime | None]:
        """Clamp the request window to a dataset's published extent.

        Args:
            var_info: The catalog entry (its :class:`Extent`).

        Returns:
            `(start, end_exclusive)` — `start` is the later of the
            request start and the dataset start; `end_exclusive` is the
            earlier of (request end + 1 day) and (dataset end + 1 day, or
            "now" + 1 day for open-ended datasets). Returns
            `(None, None)` if the windows do not overlap.

            When `discover_extent=True` was passed at construction
            and the catalog's `end_date` (or `start_date`) is missing,
            the gap is filled by an EE-side
            `reduceColumns(minMax)` over `system:time_start` via
            :meth:`_discover_ee_extent` (cached per asset for the
            lifetime of the instance).
        """
        req_start = self.time.start_date
        req_end_excl = self.time.end_date + dt.timedelta(days=1)

        ds_start, ds_end_excl = self._effective_extent(var_info)

        start = max(req_start, ds_start)
        end_excl = min(req_end_excl, ds_end_excl)
        if start >= end_excl:
            return None, None
        return start, end_excl

    def _effective_extent(
        self, var_info: Dataset
    ) -> tuple[dt.datetime, dt.datetime]:
        """Resolve a dataset's effective `(start, end_exclusive)` extent.

        The catalog's `start_date` is always a curated string (the
        `Extent` pydantic field is required); the upper bound comes
        from the curated `end_date` if present, else — when
        `discover_extent=True` — an EE-side `reduceColumns(minMax)`
        query (cached per asset), falling back to `now() + 1 day` if
        the query fails or the catalog has no `end_date` and
        discovery is disabled.

        Args:
            var_info: The catalog entry.

        Returns:
            `(start, end_exclusive)` as naive UTC datetimes.
        """
        ds_start = dt.datetime.strptime(var_info.extent.start_date, "%Y-%m-%d")
        catalog_end_str = var_info.extent.end_date

        if catalog_end_str is not None:
            ds_end_excl = (
                dt.datetime.strptime(catalog_end_str, "%Y-%m-%d")
                + dt.timedelta(days=1)
            )
            return ds_start, ds_end_excl

        _, ee_end = self._maybe_discover_ee_extent(var_info)
        if ee_end is not None:
            return ds_start, ee_end + dt.timedelta(days=1)

        # `now()` would be local-naive; the rest of the path is naive
        # UTC, so use a naive UTC value.
        ds_end_excl = (
            dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            + dt.timedelta(days=1)
        )
        return ds_start, ds_end_excl

    def _maybe_discover_ee_extent(
        self, var_info: Dataset
    ) -> tuple[dt.datetime | None, dt.datetime | None]:
        """Cached entry point for `_discover_ee_extent` (L3)."""
        if not self.discover_extent:
            return None, None
        if var_info.id in self._extent_cache:
            return self._extent_cache[var_info.id]
        discovered = self._discover_ee_extent(var_info)
        self._extent_cache[var_info.id] = discovered
        return discovered

    def _discover_ee_extent(
        self, var_info: Dataset
    ) -> tuple[dt.datetime | None, dt.datetime | None]:
        """Query a collection's actual `system:time_start` min/max via EE.

        Issues one `reduceColumns(ee.Reducer.minMax(), ["system:time_start"])
        .getInfo()` round-trip per asset (callers cache via
        :meth:`_maybe_discover_ee_extent`). On any EE-side failure
        (network, missing property, image-typed asset) returns
        `(None, None)` and logs a warning — the caller falls back to
        the catalog values or `now()`.

        Args:
            var_info: The catalog entry. Only `var_info.id` is used.

        Returns:
            `(min_dt, max_dt)` as naive UTC datetimes, or `(None,
            None)` if the query failed or the collection has no
            time-stamped images.
        """
        try:
            collection = ee.ImageCollection(var_info.id)
            result = collection.reduceColumns(
                ee.Reducer.minMax(), ["system:time_start"]
            ).getInfo() or {}
        except Exception as exc:  # noqa: BLE001 - downgrade EE errors to a warning
            logger.warning(
                f"discover_extent: reduceColumns(minMax) failed for "
                f"{var_info.id}: {type(exc).__name__}: {exc}; "
                "falling back to catalog / now()."
            )
            return None, None

        min_ms = result.get("min")
        max_ms = result.get("max")
        if min_ms is None or max_ms is None:
            return None, None
        return (
            dt.datetime.fromtimestamp(min_ms / 1000.0, tz=dt.timezone.utc).replace(
                tzinfo=None
            ),
            dt.datetime.fromtimestamp(max_ms / 1000.0, tz=dt.timezone.utc).replace(
                tzinfo=None
            ),
        )
