"""CHIRPS / CHIRP / CHIRTS / CHIRPS-GEFS / SPI / SPEI / WBGT / CHPclim FTP backend.

Downloads raster products from the Climate Hazards Center FTP server
(`data.chc.ucsb.edu`) over anonymous FTP. Every dataset's FTP layout,
spatial / temporal extent, file pattern, available formats, and per-
variable metadata is sourced from
:class:`~earthlens.chc.Catalog`, which loads the bundled
the per-family `catalog/*.yaml` files. No FTP path or filename is
hardcoded here.

The download pipeline (per `(dataset, variable, date)` triple) is:

1. :meth:`_api` — resolve the remote directory + filename from
   `Dataset.ftp_bases` / `Dataset.file_patterns` after substituting the
   per-date placeholders (`{year}`, `{month}`, `{day}`, `{dekad}`,
   `{pentad}`, `{hour}`, `{doy}`), fetch the file via FTP, and clip it
   to the user's bbox.
2. :meth:`_post_process` — ungzip (when the format is `.gz`), read the
   raster with `pyramids.Dataset`, clip to the bbox using the
   dataset's own geo-affine (no hardcoded 0.05° grid assumption), and
   write the canonical clipped GeoTIFF.

The `variables` constructor argument accepts two shapes:

* `list[str]` — legacy CHIRPS-2.0 list-of-variables. The dataset key
  is derived from `temporal_resolution` via
  :data:`_LEGACY_DATASET_KEY` (`"daily"` → `"global-daily"`,
  `"monthly"` → `"global-monthly"`).
* `dict[str, list[str]]` — mapping of CHIRPS dataset key to a list of
  variable codes, e.g.
  `{"africa-pentad": ["precipitation"]}`. This is the
  ECMWF-style shape and unlocks the full ~100-dataset catalog.
"""

from __future__ import annotations

import datetime as dt
from contextlib import closing
from ftplib import FTP  # nosec B402  # noqa: S402
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from loguru import logger
from pyramids._io import extract_from_gz
from pyramids.dataset import Dataset
from tqdm import tqdm

from earthlens.base import AbstractDataSource, SpatialExtent, TemporalExtent
from earthlens.chc.catalog import Catalog
from earthlens.chc.catalog import Dataset as ChcDataset
from earthlens.chc.catalog import Variable

__all__ = ["CHIRPS"]


# Map the legacy `temporal_resolution` shorthand to a CHIRPS dataset key
# so list-shape calls keep working without a breaking change.
# Pre-catalog the only supported shapes were "daily" and "monthly" over
# CHIRPS-2.0 global ±50°.
_LEGACY_DATASET_KEY: dict[str, str] = {
    "daily": "global-daily",
    "monthly": "global-monthly",
}


def _reject_unsigned_for_nodata_sentinel(dtype: np.dtype) -> None:
    """Bail out if the input raster's dtype can't carry a `-9999` no-data sentinel.

    The post-processing path normalises every negative pixel to -9999 and
    then casts back to the input dtype. For unsigned integer dtypes
    (`uint8`, `uint16`, `uint32`, `uint64`) the cast wraps -9999 into a
    positive value (`55537` for `uint16`, etc.), and the output band's
    declared `no_data_value=-9999` then matches *no* pixel. Refuse to
    proceed so the failure is loud, not silent.

    Every CHC raster shipped through the catalog today is float32; this
    guard is a defence in depth for future products. The per-date
    failure handling in `_download_dataset` catches the `TypeError` and
    skips the date.

    Args:
        dtype: The numpy dtype of the input raster array.

    Raises:
        TypeError: If `dtype` is any unsigned-integer numpy dtype.
    """
    if np.issubdtype(dtype, np.unsignedinteger):
        raise TypeError(
            f"CHC no-data normalisation cannot use a -9999 sentinel "
            f"on unsigned dtype {dtype}: the cast would wrap -9999 to "
            "a positive value and the declared no_data_value would "
            "match no pixel. Add an explicit no-data handling path "
            "for unsigned products before extending the catalog with "
            "one."
        )


class CHIRPS(AbstractDataSource):
    """CHIRPS catalog-driven FTP backend.

    Public surface: construct with a date range, a bbox, and
    `variables` (either a flat `list[str]` for the legacy
    CHIRPS-2.0 global path, or `dict[str, list[str]]` mapping a
    catalog dataset key to variable codes), then call
    :meth:`download`.

    Attributes:
        api_url: FTP hostname. Anonymous login; no credentials.
        catalog: :class:`~earthlens.chc.Catalog` instance loaded
            once at construction; resolves dataset keys to metadata.
    """

    api_url: str = "data.chc.ucsb.edu"

    def __init__(
        self,
        variables: dict[str, list[str]] | list[str] | None = None,
        lat_lim: list[float] | None = None,
        lon_lim: list[float] | None = None,
        temporal_resolution: str = "daily",
        start: str | None = None,
        end: str | None = None,
        path: Path | str = "",
        fmt: str = "%Y-%m-%d",
    ):
        """Initialize a CHIRPS backend.

        Args:
            variables: Either a `list[str]` of variable codes for the
                legacy CHIRPS-2.0 global path (the dataset key is
                derived from `temporal_resolution`), or a
                `dict[str, list[str]]` mapping a catalog dataset key
                (e.g. `"africa-monthly"`) to a list of variable codes
                (e.g. `["precipitation"]`). Defaults to
                `["precipitation"]`.
            lat_lim: `[lat_min, lat_max]` in degrees. Defaults to
                `[-50, 50]` (the CHIRPS-2.0 global extent).
            lon_lim: `[lon_min, lon_max]` in degrees. Defaults to
                `[-180, 180]`.
            temporal_resolution: Only consulted when `variables` is a
                `list[str]`. Must be one of `"daily"` or `"monthly"`
                in that case (the only legacy values). Ignored when
                `variables` is already a dict. Defaults to `"daily"`.
            start: Inclusive start date as a string (parsed with
                `fmt`). `None` defaults to the earliest `start_date`
                across the requested datasets.
            end: Inclusive end date as a string. `None` defaults to
                today.
            path: Output directory. Created if it does not exist.
                Defaults to the current working directory.
            fmt: `strptime` format for `start` / `end`. Defaults to
                `"%Y-%m-%d"`.

        Raises:
            KeyError: If a requested dataset key is not in the
                catalog, or a variable code is not declared under
                that dataset, or `temporal_resolution` is outside
                `{"daily", "monthly"}` with a list-shape `variables`.
        """
        if lat_lim is None:
            lat_lim = [-50.0, 50.0]
        if lon_lim is None:
            lon_lim = [-180.0, 180.0]

        catalog = Catalog()
        normalized = self._normalize_variables(variables, temporal_resolution)
        self._validate_keys(catalog, normalized)

        if start is None:
            start = min(catalog.datasets[k].start_date for k in normalized)
        if end is None:
            end = str(pd.Timestamp.now().date())

        self.catalog = catalog

        super().__init__(
            start=start,
            end=end,
            variables=normalized,
            temporal_resolution=temporal_resolution,
            lat_lim=lat_lim,
            lon_lim=lon_lim,
            fmt=fmt,
            path=path,
        )

    @staticmethod
    def _normalize_variables(
        variables: dict[str, list[str]] | list[str] | None,
        temporal_resolution: str,
    ) -> dict[str, list[str]]:
        """Coerce the user's `variables` to the catalog-keyed dict shape.

        Raises:
            KeyError: If a list-shape `variables` is paired with a
                `temporal_resolution` outside `{"daily", "monthly"}`.
        """
        if variables is None:
            variables = ["precipitation"]
        if isinstance(variables, dict):
            return {k: list(v) for k, v in variables.items()}
        if temporal_resolution not in _LEGACY_DATASET_KEY:
            raise KeyError(
                f"temporal_resolution {temporal_resolution!r} is not "
                "supported by the list-shape `variables` API. Either "
                "pass a dict like `variables={'<dataset-key>': [...]}` "
                "or use one of "
                f"{sorted(_LEGACY_DATASET_KEY)}."
            )
        return {_LEGACY_DATASET_KEY[temporal_resolution]: list(variables)}

    @staticmethod
    def _validate_keys(
        catalog: Catalog, variables: dict[str, list[str]]
    ) -> None:
        """Reject unknown dataset keys / variable names before download."""
        for ds_key, var_names in variables.items():
            if ds_key not in catalog.datasets:
                raise KeyError(
                    f"{ds_key!r} is not a curated CHIRPS dataset. "
                    "See `Catalog().list_datasets()` for available keys."
                )
            available = catalog.datasets[ds_key].variables
            for var_name in var_names:
                if var_name not in available:
                    raise KeyError(
                        f"variable {var_name!r} is not declared under "
                        f"{ds_key!r}. Available: {sorted(available)}."
                    )

    def _initialize(self) -> None:
        """No persistent client — anonymous FTP opens a connection per fetch."""
        return None

    def _check_input_dates(
        self, start: str, end: str, temporal_resolution: str, fmt: str
    ) -> TemporalExtent:
        """Parse the user's `[start, end]` window.

        Per-dataset date ranges are derived in
        :meth:`_download_dataset` from each dataset's
        `pandas_freq`; this method only stores the outer window so
        consumers (and the abstract base's `self.time`) can see it.

        Args:
            start: Inclusive start date as a string.
            end: Inclusive end date as a string.
            temporal_resolution: Accepted for API symmetry; ignored
                here because the real frequency comes from the
                catalog per dataset.
            fmt: `strptime` format applied to `start` and `end`.

        Returns:
            TemporalExtent: Frozen outer window. Only `start_date` /
            `end_date` carry meaning — `resolution` is a daily
            placeholder and `dates` is an empty
            :class:`pandas.DatetimeIndex` because CHIRPS download
            cadence is per-dataset (`pandas_freq` lives on
            :class:`~earthlens.chc.Dataset`, not on the bbox-level
            outer window). A consumer iterating
            `self.time.dates` would otherwise get a misleading daily
            index for a `monthly` or `6-hourly` dataset.
        """
        start_dt = dt.datetime.strptime(start, fmt)
        end_dt = dt.datetime.strptime(end, fmt)
        return TemporalExtent(
            start_date=start_dt,
            end_date=end_dt,
            resolution="D",
            dates=pd.DatetimeIndex([]),
        )

    def _create_grid(
        self, lat_lim: list[float], lon_lim: list[float]
    ) -> SpatialExtent:
        """Return a `SpatialExtent` for the user's bbox.

        Returns:
            SpatialExtent: Frozen bbox with `resolution=0.05`,
            CHIRPS's primary native cell size. (Datasets at coarser
            pixels, such as WBGT 1° or africa-6-hourly 0.10°, are
            still clipped correctly because :meth:`_clip_to_bbox`
            reads the actual pixel size from the downloaded raster.)
        """
        return SpatialExtent.from_pairs(
            lat_lim=lat_lim, lon_lim=lon_lim, resolution=0.05
        )

    def download(
        self,
        progress_bar: bool = True,
        cores: int | None = None,
        **_kwargs: object,
    ) -> None:
        """Download every `(dataset, variable)` pair in `self.vars`.

        Args:
            progress_bar: Whether to show a per-dataset tqdm progress
                bar. Defaults to `True`.
            cores: Number of joblib workers for parallel per-date
                retrieval. `None` (or `0`) runs sequentially.
            **_kwargs: Reserved; the facade may pass `aggregate=` (a
                no-op for CHIRPS, which has no aggregator wiring).

        Returns:
            None. Per-date GeoTIFFs land at
            `<self.root_dir>/<ds_key>_<var_name>_<date>.tif`.
            Per-variable failures are logged but do not abort the
            rest of the loop.

        Examples:
            - Legacy shape (CHIRPS-2.0 global daily):

                ```python
                >>> from earthlens.chc import CHIRPS  # doctest: +SKIP
                >>> CHIRPS(  # doctest: +SKIP
                ...     variables=["precipitation"],
                ...     temporal_resolution="daily",
                ...     start="2009-01-01", end="2009-01-02",
                ...     lat_lim=[4.0, 5.0], lon_lim=[-75.0, -74.0],
                ...     path="out/",
                ... ).download()

                ```
            - Catalog shape (pulls Africa pentadal precipitation):

                ```python
                >>> from earthlens.chc import CHIRPS  # doctest: +SKIP
                >>> CHIRPS(  # doctest: +SKIP
                ...     variables={"africa-pentad": ["precipitation"]},
                ...     start="2020-01-01", end="2020-02-01",
                ...     lat_lim=[-5.0, 5.0], lon_lim=[30.0, 40.0],
                ...     path="out/",
                ... ).download()

                ```
        """
        succeeded: list[tuple[str, str]] = []
        failed: list[tuple[tuple[str, str], BaseException]] = []

        for ds_key, var_names in self.vars.items():
            dataset = self.catalog.datasets[ds_key]
            for var_name in var_names:
                var = dataset.variables[var_name]
                logger.info(
                    f"Download CHIRPS {ds_key}/{var_name} from "
                    f"{self.time.start_date.date()} to "
                    f"{self.time.end_date.date()}"
                )
                try:
                    self._download_dataset(
                        ds_key,
                        dataset,
                        var,
                        progress_bar=progress_bar,
                        cores=cores,
                    )
                except Exception as exc:  # noqa: BLE001 - log + continue so one bad variable doesn't kill the batch
                    logger.error(
                        f"CHIRPS download for {ds_key}/{var_name} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    failed.append(((ds_key, var_name), exc))
                    continue
                succeeded.append((ds_key, var_name))

        if failed:
            failed_summary = ", ".join(
                f"{ds}/{v} ({type(exc).__name__})"
                for (ds, v), exc in failed
            )
            logger.warning(
                f"CHIRPS download summary: {len(succeeded)} succeeded "
                f"({succeeded}), {len(failed)} failed ({failed_summary})"
            )
        else:
            logger.info(
                f"CHIRPS download summary: all {len(succeeded)} variables "
                f"succeeded ({succeeded})"
            )

    def _download_dataset(
        self,
        ds_key: str,
        dataset: ChcDataset,
        var: Variable,
        progress_bar: bool = True,
        cores: int | None = None,
    ) -> None:
        """Iterate the per-dataset date range and dispatch :meth:`_api`.

        Branches on `dataset.is_discrete`: datasets that publish a fixed
        set of multi-year archive files (`discrete_files`, e.g. CenTrends)
        are routed through :meth:`_download_discrete`, which fetches each
        listed filename once instead of doing date substitution.
        """
        if dataset.is_discrete:
            self._download_discrete(
                ds_key, dataset, var, progress_bar=progress_bar
            )
            return

        ds_start = pd.Timestamp(dataset.start_date)
        ds_end = pd.Timestamp(dataset.end_date) if dataset.end_date else None

        window_start = max(self.time.start_date, ds_start)
        window_end = self.time.end_date if ds_end is None else min(
            self.time.end_date, ds_end
        )
        if window_start > window_end:
            logger.warning(
                f"{ds_key}: requested window "
                f"[{self.time.start_date.date()}, "
                f"{self.time.end_date.date()}] does not overlap dataset "
                f"window [{ds_start.date()}, "
                f"{ds_end.date() if ds_end else 'now'}]; skipping"
            )
            return

        dates = pd.date_range(
            window_start, window_end, freq=dataset.pandas_freq
        )
        if not cores:
            for date in tqdm(
                dates, desc=f"CHIRPS {ds_key}", disable=not progress_bar
            ):
                self._api(ds_key, dataset, var, date)
        else:
            Parallel(n_jobs=cores)(
                delayed(self._api)(ds_key, dataset, var, date)
                for date in dates
            )

    def _download_discrete(
        self,
        ds_key: str,
        dataset: ChcDataset,
        var: Variable,
        progress_bar: bool = True,
    ) -> None:
        """Fetch each entry in `dataset.discrete_files` once.

        For datasets that publish a fixed set of archive files
        (CenTrends, CHPclim v2, similar), date iteration is meaningless
        — each file is the whole product, not a per-date partition.
        Files are saved as `<ds_key>_<source_filename>` in
        `self.root_dir`.

        Post-processing branches on the catalog's `default_format`:

        * 2-D raster formats (`tif`, `cog`, `bil`) are clipped to the
          user's bbox in place via :meth:`_clip_raster_in_place`, just
          like the per-date `_post_process` path. CHPclim v2 monthly
          climatology TIFs flow through here.
        * Multi-dim / opaque formats (`netcdf`, `bin`) are passed
          through unmodified — CenTrends multi-year monthly NetCDFs
          carry a `time` axis that the 2-D clip math cannot handle
          correctly, so time-and-region subsetting is left to the
          caller (read with xarray and use
          `.sel(time=..., lat=..., lon=...)`).
        """
        fmt_key = dataset.default_format
        ftp_base = dataset.ftp_bases[fmt_key]
        filenames = dataset.discrete_files[fmt_key]
        is_2d_raster = fmt_key in {"tif", "cog", "bil"}
        iterable = tqdm(
            filenames, desc=f"CHC {ds_key}", disable=not progress_bar
        )
        for filename in iterable:
            local_path = self.root_dir / f"{ds_key}_{filename}"
            self._fetch_ftp(ftp_base, filename, local_path)
            if is_2d_raster:
                self._clip_raster_in_place(local_path)

    def _clip_raster_in_place(self, path: Path) -> None:
        """Read a 2-D raster at `path`, clip to `self.space`, write back.

        Used by :meth:`_download_discrete` for `tif` / `cog` / `bil`
        outputs. Negative pixels are normalised to -9999 (CHC's
        documented no-data sentinel) and -9999 is declared as the
        output band's no-data value, mirroring the per-date
        :meth:`_post_process` behaviour.
        """
        raster = Dataset.read_file(str(path))
        data = raster.read_array()
        _reject_unsigned_for_nodata_sentinel(data.dtype)
        clipped, new_geo = self._clip_to_bbox(data, raster.geotransform)
        nodata_sentinel: float = -9999.0
        clipped = np.where(clipped < 0, nodata_sentinel, clipped).astype(
            data.dtype, copy=False
        )
        new_raster = Dataset.create_from_array(
            clipped,
            geo=new_geo,
            epsg=raster.epsg,
            no_data_value=nodata_sentinel,
        )
        new_raster.to_file(str(path))

    def _api(
        self,
        ds_key: str,
        dataset: ChcDataset,
        var: Variable,
        date: pd.Timestamp,
    ) -> Path | None:
        """Resolve the FTP URL for one date, fetch, and post-process.

        Returns:
            Path: Output GeoTIFF on success.
            None: When the dataset's file pattern uses a placeholder
                this backend does not yet expand (tracked as M5 in the
                planning doc). A warning is logged and the date is
                skipped.
        """
        fmt_key = dataset.default_format
        ftp_base = dataset.ftp_bases[fmt_key]
        pattern = dataset.file_patterns[fmt_key]

        try:
            relative = pattern.format(**self._placeholders(date))
        except KeyError as missing:
            logger.warning(
                f"{ds_key}: file pattern {pattern!r} requires "
                f"placeholder {missing} which is not yet expanded by "
                "the backend (see planning issue M5); skipping "
                f"{date.date()}"
            )
            return None

        if "/" in relative:
            subdir, _, remote_filename = relative.rpartition("/")
            remote_dir = f"{ftp_base.rstrip('/')}/{subdir}/"
        else:
            remote_dir = ftp_base
            remote_filename = relative

        local_compressed = self.root_dir / remote_filename
        try:
            self._fetch_ftp(remote_dir, remote_filename, local_compressed)
        except Exception:  # noqa: BLE001 - clean up the partial download on any FTP-stack failure, then re-raise unchanged
            if local_compressed.exists():
                try:
                    local_compressed.unlink()
                except OSError:
                    pass
            raise

        return self._post_process(
            local_compressed, ds_key, dataset, var, date
        )

    @staticmethod
    def _placeholders(date: pd.Timestamp) -> dict[str, str]:
        """Build the format-string substitution dict for one date.

        Covers `{year}`, `{month}`, `{day}`, `{dekad}`, `{pentad}`,
        `{hour}`, `{doy}` — the placeholders used by the curated
        datasets. Entries using other placeholders
        (`{start_yyyymmdd}` / `{end_yyyymmdd}` for WBGT,
        `{month_pair}` for CHIRPS v3 2-monthly, `{res}` / `{scale}`)
        surface as a `KeyError` that :meth:`_api` catches and logs;
        adding them is M5.
        """
        day = date.day
        return {
            "year": f"{date.year}",
            "month": f"{date.month:02d}",
            "day": f"{day:02d}",
            "dekad": str(min(3, ((day - 1) // 10) + 1)),
            "pentad": str(min(6, ((day - 1) // 5) + 1)),
            "hour": f"{date.hour:02d}",
            "doy": f"{date.dayofyear:03d}",
        }

    @staticmethod
    def _fetch_ftp(
        remote_dir: str, remote_filename: str, local_path: Path
    ) -> None:
        """Download one file via anonymous FTP into `local_path`."""
        with closing(FTP(CHIRPS.api_url)) as ftp:  # nosec B321
            ftp.login()
            ftp.cwd(remote_dir)
            with open(local_path, "wb") as fp:
                ftp.retrbinary(f"RETR {remote_filename}", fp.write)

    def _post_process(
        self,
        compressed_path: Path,
        ds_key: str,
        dataset: ChcDataset,
        var: Variable,
        date: pd.Timestamp,
    ) -> Path:
        """Decompress (if `.gz`), clip to the user bbox, write a GeoTIFF."""
        local_path = compressed_path
        if str(compressed_path).endswith(".gz"):
            extracted = compressed_path.with_suffix("")
            extract_from_gz(
                str(compressed_path), str(extracted), delete=True
            )
            local_path = extracted

        raster = Dataset.read_file(str(local_path))
        data = raster.read_array()
        _reject_unsigned_for_nodata_sentinel(data.dtype)
        clipped, new_geo = self._clip_to_bbox(data, raster.geotransform)

        # CHIRPS encodes "missing" with -9999; some rasters do not
        # declare a no-data value at all (`raster.no_data_value[0]`
        # is `None`). Normalise: every negative pixel becomes -9999,
        # and the output band carries -9999 as its declared no-data.
        nodata_sentinel: float = -9999.0
        clipped = np.where(clipped < 0, nodata_sentinel, clipped).astype(
            data.dtype, copy=False
        )

        out_path = self.root_dir / self._output_filename(
            ds_key, dataset, var, date
        )
        new_raster = Dataset.create_from_array(
            clipped,
            geo=new_geo,
            epsg=raster.epsg,
            no_data_value=nodata_sentinel,
        )
        new_raster.to_file(str(out_path))

        try:
            local_path.unlink(missing_ok=True)
        except (PermissionError, OSError):
            logger.warning(
                f"could not delete intermediate {local_path}; safe to "
                "remove after the download finishes"
            )
        return out_path

    def _clip_to_bbox(
        self,
        data: np.ndarray,
        geo: tuple[float, ...] | list[float],
    ) -> tuple[np.ndarray, list[float]]:
        """Clip a raster array to `self.space` using its own geo-affine.

        Works for any pixel size and any extent — no hardcoded 0.05°
        or ±50° assumption. The returned geo-affine is updated so the
        output GeoTIFF has the correct origin.
        """
        origin_x = float(geo[0])
        pix_x = float(geo[1])
        origin_y = float(geo[3])
        pix_y = -float(geo[5])  # positive

        rows, cols = data.shape[-2:]
        col_left = max(
            0, int(np.floor((self.space.west - origin_x) / pix_x))
        )
        col_right = min(
            cols, int(np.ceil((self.space.east - origin_x) / pix_x))
        )
        row_top = max(
            0, int(np.floor((origin_y - self.space.north) / pix_y))
        )
        row_bot = min(
            rows, int(np.ceil((origin_y - self.space.south) / pix_y))
        )
        col_right = max(col_left, col_right)
        row_bot = max(row_top, row_bot)

        clipped = data[..., row_top:row_bot, col_left:col_right]
        new_origin_x = origin_x + col_left * pix_x
        new_origin_y = origin_y - row_top * pix_y
        new_geo = [new_origin_x, pix_x, 0.0, new_origin_y, 0.0, -pix_y]
        return clipped, new_geo

    @staticmethod
    def _output_filename(
        ds_key: str,
        dataset: ChcDataset,
        var: Variable,
        date: pd.Timestamp,
    ) -> str:
        """Build the canonical output filename for one clipped raster."""
        granularity = dataset.temporal_resolution
        if granularity == "annual":
            date_str = f"{date.year}"
        elif granularity in {
            "monthly", "monthly-climatology", "2-monthly", "3-monthly"
        }:
            date_str = f"{date.year}.{date.month:02d}"
        elif granularity == "6-hourly":
            date_str = (
                f"{date.year}.{date.month:02d}.{date.day:02d}."
                f"{date.hour:02d}"
            )
        else:
            date_str = f"{date.year}.{date.month:02d}.{date.day:02d}"
        return f"{ds_key}_{var.name}_{date_str}.tif"
