"""Google Earth Engine backend — :class:`GEE`, an :class:`AbstractDataSource`.

Downloads imagery from Google Earth Engine. A request is `{asset_id:
[band, ...], ...}` (the addressable units of an EE dataset are *bands*,
and one image carries many at once), plus a date range, a bbox (or a
`GeoDataFrame` region), a temporal-compositing resolution
(`"raw"`/`"daily"`/`"monthly"`/`"yearly"`), and an output pixel `scale`
in metres. The asset ids and band metadata are resolved through
:class:`earthlens.gee.Catalog` (loaded from `gee_data_catalog.yaml`).

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
  → a GeoTIFF under the output directory; `"drive"` / `"gcs"` queue an
  asynchronous `ee.batch.Export.image.to{Drive,CloudStorage}` task
  (`maxPixels` only, no 32768-px cap), poll it to completion, and
  return a `"drive://…"` / `"gs://…"` destination string (the file is
  left in the Drive folder / GCS bucket for the caller to pull).

Authentication is a one-time `ee.Initialize` against a *registered*
Cloud project, performed by :meth:`_initialize` via
:class:`earthlens.gee.auth.EarthEngineAuth` (service-account key) or, if
no key is given, an interactive `ee.Authenticate()` against an explicit
`project`. Credential / registration failures surface as
:class:`AuthenticationError`.
"""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import ee
import pandas as pd
import requests
from loguru import logger
from tqdm import tqdm

from earthlens.base import AbstractDataSource, SpatialExtent, TemporalExtent
from earthlens.gee._helpers import (
    EE_MAX_DIMENSION,
    estimate_pixel_dims,
    reduce_collection,
    slug_asset_id,
    wait_for_task,
)
from earthlens.gee.auth import AuthenticationError, EarthEngineAuth
from earthlens.gee.catalog import Catalog, Dataset
from earthlens.gee.features import createFeature

if TYPE_CHECKING:  # pragma: no cover - typing only
    from geopandas import GeoDataFrame

__all__ = ["GEE", "AuthenticationError"]

# `temporal_resolution` → pandas frequency alias for the per-bucket
# date range. `"raw"` is special-cased (one bucket spanning the whole
# request window).
_RESOLUTION_FREQ: dict[str, str] = {"daily": "D", "monthly": "MS", "yearly": "YS"}

_DOWNLOAD_CHUNK_BYTES: int = 1 << 20  # 1 MiB streaming chunk for the HTTP download


class GEE(AbstractDataSource):
    """Google Earth Engine data source.

    Args:
        start: Inclusive start date string (parsed with `fmt`).
        end: Inclusive end date string.
        variables: Mapping `{asset_id: [band, ...]}` — each `asset_id`
            must be a key of :attr:`Catalog.datasets` and each band a
            band of that dataset (see `gee_data_catalog.yaml`).
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
            requires `drive_folder`), or `"gcs"` (asynchronous
            `ee.batch.Export.image.toCloudStorage`; requires `gcs_bucket`).
        drive_folder: Google Drive folder name for `export_via="drive"`.
        gcs_bucket: Cloud Storage bucket name for `export_via="gcs"` (the
            service account needs `roles/storage.objectAdmin` on it).
        region: Optional `GeoDataFrame` to clip to precisely; when given
            it supersedes the lat/lon bbox for the actual clip (the bbox
            is still used for the `"url"` size estimate).

    Raises:
        AuthenticationError: If Earth Engine cannot be initialised
            (missing/invalid key, unregistered project, missing IAM role).
        ValueError: At construction for a bad `export_via` (or `"drive"`
            without `drive_folder` / `"gcs"` without `gcs_bucket`); from
            the parent on a bad date range; from :meth:`_check_input_dates`
            on an unknown `temporal_resolution`; from :meth:`_api` on a
            missing scale or an oversized `"url"` request; from
            :meth:`_download_dataset` on an unknown asset id or band.
        NotImplementedError: From :meth:`download` when `aggregate=` is
            passed (not yet supported).
        RuntimeError: From :meth:`_api` if a `"drive"` / `"gcs"` export
            task does not complete, or if a `"url"` response is a zip.

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
        export_via: str = "url",
        drive_folder: str | None = None,
        gcs_bucket: str | None = None,
        region: GeoDataFrame | None = None,
    ):
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
        if export_via not in {"url", "drive", "gcs"}:
            raise ValueError(
                f"export_via must be 'url', 'drive', or 'gcs', got {export_via!r}"
            )
        if export_via == "drive" and not drive_folder:
            raise ValueError("export_via='drive' requires drive_folder=")
        if export_via == "gcs" and not gcs_bucket:
            raise ValueError("export_via='gcs' requires gcs_bucket=")
        self.export_via = export_via
        self.drive_folder = drive_folder
        self.gcs_bucket = gcs_bucket
        self.region = region
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

    # ------------------------------------------------------------------ hooks

    def _initialize(self) -> Any:
        """Authenticate and initialise the Earth Engine connection.

        Uses a service-account key when `service_account` + `service_key`
        were given (via :class:`EarthEngineAuth`); otherwise runs
        `ee.Authenticate()` and `ee.Initialize(project=...)` against the
        explicit `project`. The resolved project id is stored on
        :attr:`project`.

        Returns:
            The `ee` module (truthy, so the parent stores it as
            `self.client`).

        Raises:
            AuthenticationError: If credentials are missing/invalid, no
                project can be resolved, the project is not registered
                for Earth Engine, or the service account lacks the
                required IAM role on it.
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

    # ------------------------------------------------------------------ download

    def download(
        self, progress_bar: bool = True, aggregate: Any = None
    ) -> list[Path | str]:
        """Download every requested band-set of every requested dataset.

        Args:
            progress_bar: Show a per-bucket `tqdm` bar. Defaults to `True`.
            aggregate: Accepted for signature parity with the ECMWF
                backend; not yet supported — passing a non-`None` value
                raises `NotImplementedError` (see plan task `M3`).

        Returns:
            One entry per `(dataset, band-set, time-bucket)`: a
            :class:`pathlib.Path` to the written GeoTIFF for
            `export_via="url"`, or a destination string
            (`"drive://<folder>/<prefix>"` / `"gs://<bucket>/<prefix>"`)
            for the asynchronous `"drive"` / `"gcs"` exports.

        Raises:
            NotImplementedError: If `aggregate` is not `None`.
            ValueError: On an unknown asset id, an unknown band, or an
                oversized `"url"` request (see :meth:`_api`).
            RuntimeError: If a `"drive"` / `"gcs"` export task fails.

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
                request against `gee_data_catalog.yaml`.
            earthlens.gee.auth.EarthEngineAuth: Performs the one-time
                `ee.Initialize` used by :meth:`_initialize`.
        """
        if aggregate is not None:
            raise NotImplementedError(
                "aggregate= is not yet supported by the GEE backend "
                "(planned — see the GEE plan task M3)."
            )
        outputs: list[Path | str] = []
        for asset_id, bands in self.vars.items():
            outputs.extend(self._download_dataset(asset_id, list(bands), progress_bar))
        return outputs

    def _download_dataset(
        self, asset_id: str, bands: list[str], progress_bar: bool = True
    ) -> list[Path | str]:
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

    # ------------------------------------------------------------------ EE pipeline

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
            ``(timestamp, ee.Image)`` pairs — `timestamp` is the bucket
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
    ) -> Path | str:
        """Export one composited `ee.Image` via the configured `export_via`.

        For `export_via="url"`: estimate the request's pixel dimensions
        from the bbox and `scale`; if either axis exceeds Earth Engine's
        32768-px synchronous limit, raise a `ValueError` pointing the
        user at a coarser `scale` or `export_via="drive"`. Otherwise
        request a GeoTIFF via `getDownloadURL` and stream it to disk as
        `<asset-slug>_<bands>_<YYYYMMDD>.tif`. For `export_via="drive"` /
        `"gcs"`: queue an `ee.batch.Export.image.to{Drive,CloudStorage}`
        task, poll it to completion (no synchronous size cap, just
        `maxPixels`), and return a destination string — the file is left
        in the Drive folder / GCS bucket for the caller to pull.

        Args:
            image: The `ee.Image` to export.
            var_info: The catalog entry (for the asset slug and the
                fallback `spatial_resolution`).
            bands: The band ids in `image` (used in the filename / prefix).
            when: The bucket timestamp (used in the filename / prefix).

        Returns:
            For `"url"`: the :class:`pathlib.Path` of the written GeoTIFF.
            For `"drive"` / `"gcs"`: a destination string
            (`"drive://<folder>/<prefix>"` / `"gs://<bucket>/<prefix>"`).

        Raises:
            ValueError: If no output scale can be resolved, or (for
                `"url"`) the estimated request exceeds the 32768-px limit.
            RuntimeError: If Earth Engine returns a zip instead of a
                GeoTIFF (`"url"`), or a `"drive"` / `"gcs"` export task
                does not complete.
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
        """Stream a GeoTIFF from `image.getDownloadURL`; enforce the 32768-px cap."""
        width_px, height_px = estimate_pixel_dims(self.space, scale)
        if max(width_px, height_px) > EE_MAX_DIMENSION:
            raise ValueError(
                f"{var_info.id}: the requested AOI at scale={scale} m is about "
                f"{width_px}x{height_px} px, over Earth Engine's "
                f"{EE_MAX_DIMENSION}-px per-axis limit for synchronous downloads. "
                "Use a coarser scale, a smaller bbox, or export_via='drive'."
            )
        url = image.getDownloadURL(
            {"scale": scale, "crs": self.crs, "region": region, "format": "GEO_TIFF"}
        )
        target = self.root_dir / f"{prefix}.tif"
        with requests.get(url, stream=True, timeout=300) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_BYTES):
                    handle.write(chunk)
        if zipfile.is_zipfile(target):
            raise RuntimeError(
                f"Earth Engine returned a zip archive at {target}; this path "
                "requests format='GEO_TIFF' and does not unpack zip-of-tifs "
                "responses (see plan task PY-2)."
            )
        logger.info(f"Wrote {target} ({target.stat().st_size} bytes)")
        return target

    def _export_via_batch(self, image, scale: float, region, prefix: str) -> str:
        """Queue an `ee.batch.Export.image.to{Drive,CloudStorage}` task and wait for it."""
        common = {
            "image": image,
            "description": prefix[:100],
            "fileNamePrefix": prefix,
            "region": region,
            "scale": scale,
            "crs": self.crs,
            "maxPixels": 1e13,
        }
        if self.export_via == "drive":
            task = ee.batch.Export.image.toDrive(folder=self.drive_folder, **common)
            destination = f"drive://{self.drive_folder}/{prefix}"
        else:
            task = ee.batch.Export.image.toCloudStorage(bucket=self.gcs_bucket, **common)
            destination = f"gs://{self.gcs_bucket}/{prefix}"
        wait_for_task(task, progress_bar=True)
        logger.info(f"Exported {destination} (pull it from the {self.export_via} destination)")
        return destination

    # ------------------------------------------------------------------ helpers

    def _ee_region(self):
        """Return the `ee.Geometry` to clip / filter requests to.

        Uses the constructor `region` `GeoDataFrame` (converted via
        :func:`earthlens.gee.features.createFeature`) when given,
        otherwise an `ee.Geometry.Rectangle` built from the lat/lon
        bbox. Computed once and cached.

        Returns:
            The `ee.Geometry`.
        """
        if self._ee_geometry is None:
            if self.region is not None:
                self._ee_geometry = createFeature(self.region).geometry()
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
            ``(start, end_exclusive)`` — `start` is the later of the
            request start and the dataset start; `end_exclusive` is the
            earlier of (request end + 1 day) and (dataset end + 1 day, or
            "now" + 1 day for open-ended datasets), so the half-open EE
            window covers the inclusive user end date. Returns
            ``(None, None)`` if the windows do not overlap.
        """
        req_start = self.time.start_date
        req_end_excl = self.time.end_date + dt.timedelta(days=1)
        ds_start = dt.datetime.strptime(var_info.extent.start_date, "%Y-%m-%d")
        if var_info.extent.end_date is None:
            # `now()` would be local-naive; everything else here is naive UTC
            # (STAC dates and `strptime`d user dates), so use a naive UTC value.
            ds_end_excl = (
                dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                + dt.timedelta(days=1)
            )
        else:
            ds_end_excl = dt.datetime.strptime(var_info.extent.end_date, "%Y-%m-%d") + dt.timedelta(days=1)
        start = max(req_start, ds_start)
        end_excl = min(req_end_excl, ds_end_excl)
        if start >= end_excl:
            return None, None
        return start, end_excl
