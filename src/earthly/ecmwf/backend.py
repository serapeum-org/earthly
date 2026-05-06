from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import cdsapi
import numpy as np
import pandas as pd
from loguru import logger

from earthly.aggregate import AggregationConfig, aggregate_netcdf
from earthly.base import (
    AbstractDataSource,
    SpatialExtent,
    TemporalExtent,
)
from earthly.ecmwf.catalog import Catalog, Variable
from earthly.ecmwf.constraints import RequestValidator

__all__ = ["AuthenticationError", "ECMWF", "ERA5_GRID_DEGREES"]


ERA5_GRID_DEGREES: float = 0.125

# Per-request-kind keys to drop from the request dict before the
# retrieve call. The keys here name the *template defaults* (built
# unconditionally by :meth:`ECMWF._api`) that are invalid for the
# named request kind. Per-row `extras` are still merged on top, so
# users can supply alternative values for any stripped key.
# `product_type` is catalog-driven (see `Variable.product_type`) and
# no longer appears in any strip list.
REQUEST_KIND_STRIPS: dict[str, tuple[str, ...]] = {
    "form": (),
    # ORAS5 (and any monthly ocean dataset that mirrors NEMO's
    # request shape): no `day` / `time` selectors, no `area`
    # bbox cropping.
    "oceanic_monthly": ("day", "time", "area"),
    # CARRA-means and similar aggregate datasets: drop `time`
    # because the aggregate is over the window indicated by
    # `time_aggregation`.
    "carra_means": ("time",),
}


class AuthenticationError(Exception):
    """Raised when cdsapi cannot authenticate against the Climate Data Store.

    The ECMWF backend uses :class:`cdsapi.Client` to talk to CDS. The
    client reads its credentials from `~/.cdsapirc` (or the
    `CDSAPI_URL` / `CDSAPI_KEY` environment variables). If the
    config is missing or malformed, :meth:`ECMWF._initialize` wraps the
    underlying error in this exception so callers can distinguish auth
    problems from generic CDS server errors.

    See Also:
        https://cds.climate.copernicus.eu/how-to-api: Official cdsapi
            setup guide, including PAT generation and the
            `~/.cdsapirc` format.
    """

    pass


def _looks_like_missing_credentials(exc: BaseException) -> bool:
    """Heuristic: does this exception come from missing CDS credentials?

    cdsapi does not expose typed exception classes for auth failures —
    they surface as generic `Exception` with messages like "Missing/
    incomplete configuration file" or "key not found". We classify by
    presence of the dotfile and env vars first (no dotfile + no env
    vars → almost certainly missing credentials), then fall back to a
    keyword scan of the exception message.

    Args:
        exc: The exception raised by `cdsapi.Client()`.

    Returns:
        True when the failure looks like a credential / config-file
        problem (so it is safe to wrap as :class:`AuthenticationError`),
        False for transport / network / library errors that should
        propagate untouched.
    """
    cdsapirc_present = (Path.home() / ".cdsapirc").is_file()
    env_present = bool(os.environ.get("CDSAPI_URL") and os.environ.get("CDSAPI_KEY"))
    auth_keywords = (
        "configuration",
        "credentials",
        "cdsapirc",
        "key not found",
        "missing url",
        "missing key",
    )
    message = str(exc).lower()
    no_credentials = not cdsapirc_present and not env_present
    message_indicates_auth = any(keyword in message for keyword in auth_keywords)
    return no_credentials or message_indicates_auth


def _looks_like_licence_not_accepted(exc: BaseException) -> bool:
    """Heuristic: does this exception come from an unaccepted CDS licence?

    CDS returns HTTP 403 with a body that mentions "Required licences
    not accepted" (or "licence" depending on locale) when the user has
    a valid Personal Access Token but has not ticked the licence on
    the dataset's download page. cdsapi raises this through to the
    caller as a generic exception; we detect it by message scan so we
    can rewrite into a :class:`PermissionError` that names the
    dataset URL.

    Args:
        exc: The exception raised by `client.retrieve(...)`.

    Returns:
        True if the message looks like a licence-acceptance failure;
        False otherwise.
    """
    message = str(exc).lower()
    return (
        "licence" in message
        or "license" in message
        or "403" in message
        and ("accept" in message or "term" in message)
    )


class ECMWF(AbstractDataSource):
    """ECMWF / Copernicus Climate Data Store backend.

    Downloads ERA5 reanalysis (and ERA5-Land where the catalog
    indicates) via :class:`cdsapi.Client`. The user-friendly variable
    short codes (e.g. `"2m-temperature"`, `"total-precipitation"`) are resolved through
    :class:`Catalog`, which loads the per-variable metadata from
    `cds_data_catalog.yaml`.

    The download pipeline (per variable) is a single step:

    * :meth:`_api` — build the cdsapi request dict (daily / monthly
      branch on `temporal_resolution`) and submit it via
      `client.retrieve(dataset, request, target)`. Returns the
      absolute path to the NetCDF that CDS wrote.

    Per-date GeoTIFF post-processing (time-window mean, flux
    scaling, raster output) is intentionally not part of the
    package — see `examples/post_process_ecmwf_netcdf.py` for a
    runnable script that consumes the NetCDF this method writes.

    The valid `temporal_resolution` values are `"daily"` and
    `"monthly"`. `_check_input_dates` raises `ValueError` for
    anything else; that is the authoritative gate. Spatial cell
    size lives on :attr:`SpatialExtent.resolution` (populated by
    :meth:`_create_grid`) and is sourced from
    :data:`ERA5_GRID_DEGREES`.
    """

    def __init__(
        self,
        start: str,
        end: str,
        variables: dict[str, list[str]],
        lat_lim: list[float],
        lon_lim: list[float],
        temporal_resolution: str = "daily",
        path: Path | str = "",
        fmt: str = "%Y-%m-%d",
        skip_constraints: bool = False,
    ):
        """Initialize an ECMWF backend instance.

        Forwards every argument to :class:`AbstractDataSource`,
        which captures the cdsapi client into `self.client` and
        the bbox/date dict into `self.space`/`self.time`.

        Args:
            start: Inclusive start date as a string (parsed with
                `fmt`). Required.
            end: Inclusive end date as a string. Required.
            variables: Mapping from CDS dataset short name to a list
                of variable codes drawn from that dataset, e.g.
                `{"reanalysis-era5-single-levels": ["2m-temperature",
                "total-precipitation"]}`. The dataset name must be a
                key of :attr:`Catalog.datasets`; each variable name
                must appear under that dataset's `variables:` block.
                See `cds_data_catalog.yaml` for the registered keys.
                Required.
            lat_lim: `[lat_min, lat_max]`. Required.
            lon_lim: `[lon_min, lon_max]`. Required.
            temporal_resolution: Either `"daily"` or `"monthly"`.
                Defaults to `"daily"`.
            path: Output directory. Created by the parent if it does
                not exist. Defaults to `""` (the current working
                directory).
            fmt: `strptime` format for `start` / `end`.
                Defaults to `"%Y-%m-%d"`.
            skip_constraints: When `True`, every CDS pre-flight
                validation phase (date / area sanity, variable typo
                check, required-fields check, combinatorial cover
                check) is bypassed and the request is sent to CDS
                unchecked. Useful when CDS's published
                `constraints.json` is stale or wrong for the
                dataset, or when running offline. Defaults to `False`.
        """
        self.skip_constraints = skip_constraints
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

    def _check_input_dates(
        self, start: str, end: str, temporal_resolution: str, fmt: str
    ):
        """Parse the date range and produce the iteration index.

        Returned dict is captured by
        :meth:`AbstractDataSource.__init__` into `self.time` so
        :meth:`_api` can access the parsed bounds and the per-date
        pandas range without re-parsing.

        Args:
            start: Inclusive start date as a string.
            end: Inclusive end date as a string.
            temporal_resolution: `"daily"` (uses `freq="D"`) or
                `"monthly"` (uses `freq="MS"`).
            fmt: `strptime` format applied to `start` and `end`.

        Returns:
            TemporalExtent: Frozen pydantic model with `start_date`,
            `end_date`, `resolution` (pandas frequency alias —
            `"D"` for daily, `"MS"` for month-start), and
            `dates` (the :class:`pandas.DatetimeIndex` the
            download loop iterates).

        Raises:
            ValueError: If `temporal_resolution` is neither
                `"daily"` nor `"monthly"`, or if the parsed
                `start` is later than the parsed `end`.
        """
        start = dt.datetime.strptime(start, fmt)
        end = dt.datetime.strptime(end, fmt)

        if temporal_resolution == "daily":
            dates = pd.date_range(start, end, freq="D")
            resolution = "D"
        elif temporal_resolution == "monthly":
            dates = pd.date_range(start, end, freq="MS")
            resolution = "MS"
        else:
            raise ValueError(
                "temporal_resolution should be either 'daily' or 'monthly'"
            )

        return TemporalExtent(
            start_date=start,
            end_date=end,
            resolution=resolution,
            dates=dates,
        )

    def _initialize(self):
        """Construct the :class:`cdsapi.Client` for talking to CDS.

        Reads credentials from `~/.cdsapirc` (or the `CDSAPI_URL` /
        `CDSAPI_KEY` environment variables, which cdsapi falls back to
        when the dotfile is absent). If neither is configured, the
        underlying cdsapi exception is wrapped in
        :class:`AuthenticationError` with a message that tells the user
        exactly where to put their Personal Access Token.

        Returns:
            cdsapi.Client: Authenticated CDS client. Calls to
            `client.retrieve(...)` use this connection.

        Raises:
            AuthenticationError: If cdsapi cannot construct a Client —
                typically because `~/.cdsapirc` is missing,
                malformed, or contains an old-API-style `email` line.

        Examples:
            - Construct a client when credentials are properly
              configured. Marked `# doctest: +SKIP` because it
              requires a real `~/.cdsapirc`:

                ```python
                >>> ecmwf = ECMWF(  # doctest: +SKIP
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables={
                ...         "reanalysis-era5-single-levels": ["2m-temperature"],
                ...     },
                ...     lat_lim=[4.0, 5.0],
                ...     lon_lim=[-75.0, -74.0],
                ...     path="examples/data/era5",
                ... )

                ```
        """
        try:
            client = cdsapi.Client()
        except Exception as exc:
            if _looks_like_missing_credentials(exc):
                raise AuthenticationError(
                    "cdsapi could not authenticate against the Climate "
                    "Data Store. Create ~/.cdsapirc (Windows: "
                    "C:\\Users\\<USER>\\.cdsapirc) with:\n"
                    "    url: https://cds.climate.copernicus.eu/api\n"
                    "    key: <YOUR-PERSONAL-ACCESS-TOKEN>\n"
                    "Generate a Personal Access Token at "
                    "https://cds.climate.copernicus.eu/profile and "
                    "accept the licence for each dataset you intend to "
                    "download. See https://cds.climate.copernicus.eu/how-to-api for "
                    "the full setup guide."
                ) from exc
            raise

        return client

    def _create_grid(self, lat_lim: list, lon_lim: list):
        """Snap a lat/lon bounding box to ERA5 grid edges.

        Floors the south/west limits and ceils the north/east limits to
        the nearest multiple of :data:`ERA5_GRID_DEGREES` (0.125°), so
        every CDS retrieve aligns with the ERA5 native grid and no
        cell straddles the requested area boundary.

        Args:
            lat_lim: `[lat_min, lat_max]` in degrees north.
            lon_lim: `[lon_min, lon_max]` in degrees east.

        Returns:
            SpatialExtent: Grid-aligned bounding box with
            `resolution` set to :data:`ERA5_GRID_DEGREES`.

        Examples:
            - Snap a 1° box to the ERA5 grid:

                ```python
                >>> ecmwf = ECMWF.__new__(ECMWF)
                >>> extent = ecmwf._create_grid([4.19, 4.64], [-75.65, -74.73])
                >>> round(extent.resolution, 3)
                0.125
                >>> round(extent.latitude_min, 3), round(extent.latitude_max, 3)
                (4.125, 4.75)

                ```
            - The bbox always grows out to grid edges:

                ```python
                >>> ecmwf = ECMWF.__new__(ECMWF)
                >>> extent = ecmwf._create_grid([0.05, 0.95], [0.05, 0.95])
                >>> round(extent.latitude_min, 3), round(extent.latitude_max, 3)
                (0.0, 1.0)
                >>> round(extent.longitude_min, 3), round(extent.longitude_max, 3)
                (0.0, 1.0)

                ```
        """
        cell_size = ERA5_GRID_DEGREES
        lat_lim_floor = np.floor(lat_lim[0] / cell_size) * cell_size
        lat_lim_ceil = np.ceil(lat_lim[1] / cell_size) * cell_size
        lat_lim = [lat_lim_floor, lat_lim_ceil]

        lon_lim_floor = np.floor(lon_lim[0] / cell_size) * cell_size
        lon_lim_ceil = np.ceil(lon_lim[1] / cell_size) * cell_size
        lon_lim = [lon_lim_floor, lon_lim_ceil]
        return SpatialExtent.from_pairs(
            lat_lim=lat_lim, lon_lim=lon_lim, resolution=cell_size
        )

    def download(
        self,
        progress_bar: bool = True,
        aggregate: AggregationConfig | None = None,
        *args,
        **kwargs,
    ):
        """Download every `(dataset, variable)` pair in `self.vars` from CDS.

        Iterates the user-supplied `variables` mapping (CDS dataset
        short name → list of variable codes) and, for each pair,
        looks the variable up in the CDS :class:`Catalog` and
        delegates to :meth:`_download_dataset`.

        Args:
            progress_bar: Reserved; currently unused since the
                slicing pipeline that previously consumed it has
                been moved out of the package. Defaults to `True`
                so existing callers keep working.
            aggregate: Optional :class:`earthly.aggregate.AggregationConfig`.
                When provided, every retrieved NetCDF is fed through
                :func:`earthly.aggregate.aggregate_netcdf` immediately
                after `_api()` returns. When the config's `out_dir`
                is `None`, it is defaulted to
                `<self.root_dir>/aggregated/`. Aggregation failures
                surface in the per-variable failure summary alongside
                retrieve failures, so a single bad variable does not
                abort the rest of the loop.

                **`op="auto"` semantics.** When the config's `op` is
                left at its default `"auto"`, the reducer is picked
                per-variable from the catalog row's `types` field
                (`Variable.is_flux`):

                * **State** (`types` unset or `"state"` — e.g.
                  `2m-temperature`, `surface-pressure`,
                  `relative-humidity`). Each NetCDF sample is the
                  instantaneous value at that timestamp. `auto` →
                  `"mean"`. The window mean is the natural daily /
                  monthly summary.
                * **Flux** (`types: flux` — e.g.
                  `total-precipitation`, `evaporation`,
                  `surface-runoff`, radiation accumulations). Each
                  NetCDF sample is the accumulation since the
                  previous post-processing step (a 6-hour
                  accumulation in legacy daily ERA5, 1-hour in
                  CDS-Beta). `auto` → `"sum"`. The per-slot
                  accumulations are summed inside the window to
                  recover the actual window total.

                Worked example — daily `evaporation` for one pixel
                with the four 6-hourly slots
                `[0.001, 0.002, 0.005, 0.004]` m of water
                equivalent. `op="auto"` resolves to `"sum"` and
                writes `0.012 m` (the day's total evaporation) to
                the GeoTIFF. A plain `op="mean"` would write
                `0.003 m` (the average 6-hour accumulation, **not**
                a daily total).

                Pass an explicit `op="mean"` / `"sum"` / `"min"` /
                `"max"` / `"std"` to bypass auto-routing — for
                example, on pre-aggregated CDS datasets like
                `derived-era5-single-levels-daily-statistics` where
                each NetCDF sample is already a daily aggregate and
                summing four of them would multiply by 4. See
                `docs/reference/aggregation.md` for the full
                walkthrough.
            *args: Reserved; ignored. Kept for forward-compatibility
                with backend-specific extras callers might pass via
                :meth:`Earthly.download`.
            **kwargs: Reserved; ignored. Same rationale as `*args`.

        Returns:
            None. Per-variable NetCDFs land at
            `<self.root_dir>/<cds_variable>_<cds_dataset>.nc`. When
            `aggregate` is set, per-window GeoTIFFs land at
            `<aggregate.out_dir or self.root_dir/aggregated>/<cds_variable>_<freq>_<window>.tif`.

        Raises:
            KeyError: If any dataset key in `self.vars` is not a
                curated CDS dataset, or if a listed variable is not
                declared under that dataset.
            Exception: Any error :meth:`_api` propagates from
                :meth:`cdsapi.Client.retrieve`.

        Examples:
            - End-to-end download via the user-facing
              :class:`Earthly` facade. Marked
              `# doctest: +SKIP` because it requires a configured
              `~/.cdsapirc` and several minutes of CDS queue time:

                ```python
                >>> from earthly.earthly import Earthly
                >>> earthly = Earthly(  # doctest: +SKIP
                ...     data_source="ecmwf",
                ...     temporal_resolution="daily",
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables={
                ...         "reanalysis-era5-single-levels": [
                ...             "2m-temperature", "total-precipitation"
                ...         ],
                ...     },
                ...     lat_lim=[4.0, 5.0],
                ...     lon_lim=[-75.0, -74.0],
                ...     path="examples/data/era5",
                ... )
                >>> earthly.download()  # doctest: +SKIP

                ```

        See Also:
            :meth:`_download_dataset`: Per-variable download +
                post-processing.
            :meth:`_api`: Builds and submits the cdsapi request.
            :class:`Catalog`: Resolves `(dataset, code)` pairs to
                per-variable metadata.
        """
        catalog = Catalog()
        succeeded: list[tuple[str, str]] = []
        failed: list[tuple[tuple[str, str], BaseException]] = []

        effective_aggregate: AggregationConfig | None = None
        if aggregate is not None:
            if aggregate.out_dir is None:
                effective_aggregate = aggregate.model_copy(
                    update={"out_dir": self.root_dir / "aggregated"}
                )
            else:
                effective_aggregate = aggregate

        for dataset_name, var_codes in self.vars.items():
            for var in var_codes:
                start = self.time.start_date
                end = self.time.end_date
                logger.info(
                    f"Download ECMWF {dataset_name}/{var} data for "
                    f"period {start} till {end}"
                )
                try:
                    var_info = catalog.get_variable(dataset_name, var)
                    nc_path = self._download_dataset(
                        var_info, progress_bar=progress_bar
                    )
                except Exception as exc:
                    logger.error(
                        f"ECMWF download for {dataset_name}/{var} failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    failed.append(((dataset_name, var), exc))
                    continue

                if effective_aggregate is not None:
                    try:
                        aggregate_netcdf(nc_path, var_info, effective_aggregate)
                    except Exception as exc:
                        logger.error(
                            f"ECMWF aggregate for {dataset_name}/{var} failed: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        failed.append(((dataset_name, var), exc))
                        continue

                succeeded.append((dataset_name, var))

        if failed:
            failed_summary = ", ".join(
                f"{ds}/{var} ({type(exc).__name__})" for (ds, var), exc in failed
            )
            logger.warning(
                f"ECMWF download summary: {len(succeeded)} succeeded "
                f"({succeeded}), {len(failed)} failed ({failed_summary})"
            )
        else:
            logger.info(
                f"ECMWF download summary: all {len(succeeded)} "
                f"variables succeeded ({succeeded})"
            )

    def _download_dataset(
        self,
        var_info: Variable,
        progress_bar: bool = True,
    ):
        """Download a single variable from CDS.

        Thin wrapper around :meth:`_api` — builds the cdsapi request,
        submits it, and returns the absolute :class:`pathlib.Path`
        to the NetCDF that CDS wrote.

        Per-date GeoTIFF slicing is **not** done here. Users who
        want per-date `.tif` outputs can run
        `examples/post_process_ecmwf_netcdf.py` against the
        returned NetCDF.

        Args:
            var_info: Catalog row for the variable. See :meth:`_api`
                for the attributes consumed.
            progress_bar: Reserved; currently unused since the
                slicing pipeline that previously consumed it has
                been moved out of the package. Defaults to `True`
                so existing callers keep working.

        Returns:
            pathlib.Path: Absolute path to the downloaded NetCDF.

        See Also:
            :meth:`_api`: Builds and submits the CDS request, returns
                the path to the NetCDF.
            :class:`Catalog`: Loads `Variable` instances from
                `cds_data_catalog.yaml`.
        """
        return self._api(var_info)

    def _api(self, var_info: Variable):
        """Build a CDS request and submit it via :class:`cdsapi.Client`.

        Constructs the request dictionary expected by
        :meth:`cdsapi.Client.retrieve` from the catalog metadata for a
        single variable, then submits it. The retrieve call blocks until
        CDS has served the request and the NetCDF file has been written
        to disk — typically minutes due to CDS queue times.

        The request always submits to `var_info.cds_dataset` — the
        dataset name the user named in the `variables` dict. The
        `temporal_resolution` parameter only shapes the request body,
        not the dataset choice:

        * `"daily"` — `product_type=['reanalysis']` and four
          six-hourly time slots (`00:00/06:00/12:00/18:00`).
        * `"monthly"` — `product_type=['monthly_averaged_reanalysis']`
          with a single `time=['00:00']` slot and no `day` key
          (CDS's `-monthly-means` datasets reject those). Pair this
          mode with a monthly-aggregated CDS dataset name in
          `variables` (e.g.
          `"reanalysis-era5-single-levels-monthly-means"`).

        Both branches use `data_format='netcdf'` so the resulting
        file can be opened with :class:`pyramids.netcdf.NetCDF`,
        `xarray`, or any standard NetCDF reader for downstream
        post-processing.

        Args:
            var_info: Variable metadata pulled from
                `cds_data_catalog.yaml` via :class:`Catalog`. Required
                keys:

                * `cds_dataset` — CDS dataset short name, e.g.
                  `"reanalysis-era5-single-levels"`.
                * `cds_variable` — CDS variable name, e.g.
                  `"2m_temperature"`. Also used as the output
                  filename stem.

                Optional keys:

                * `cds_pressure_level` — Forwarded to the request as
                  `pressure_level` for pressure-level datasets.
                * `extras` — Free-form mapping of additional CDS
                  request parameters merged into the request dict
                  immediately before submission. Row-level keys win
                  over the template defaults built from
                  `temporal_resolution` and `area`. This is the
                  escape hatch for non-ERA5 dataset families that
                  carry their own selectors (e.g. `domain` /
                  `leadtime_hour` for CARRA, `experiment` /
                  `model` for CMIP6, `vertical_resolution` for
                  ORAS5).

        Returns:
            pathlib.Path: Absolute path to the downloaded NetCDF file,
            written to
            `<self.root_dir>/<cds_variable>_<cds_dataset>.nc`.

        Raises:
            KeyError: If `var_info` is missing one of the required
                keys (`cds_dataset` or `cds_variable`).
            Exception: Any error raised by
                :meth:`cdsapi.Client.retrieve`, including authentication
                failures (no `~/.cdsapirc`), licence-not-accepted
                errors, or transient CDS server errors.

        Examples:
            - Inspect a single-level :class:`Variable` and the
              file name it produces:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> spec = Catalog().get_variable(
                ...     "reanalysis-era5-single-levels", "2m-temperature"
                ... )
                >>> spec.cds_dataset
                'reanalysis-era5-single-levels'
                >>> f"{spec.cds_variable}_{spec.cds_dataset}.nc"
                '2m_temperature_reanalysis-era5-single-levels.nc'

                ```
            - Pressure-level variables expose `cds_pressure_level`;
              :meth:`_api` forwards it to the request:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> spec = Catalog().get_variable(
                ...     "reanalysis-era5-pressure-levels", "temperature"
                ... )
                >>> spec.cds_pressure_level
                ['1000']

                ```
            - Submit the request through the user-facing
              :class:`Earthly` facade. Marked
              `# doctest: +SKIP` because it requires a configured
              `~/.cdsapirc` and several minutes of CDS queue time:

                ```python
                >>> from earthly.earthly import Earthly  # doctest: +SKIP
                >>> earthly = Earthly(  # doctest: +SKIP
                ...     data_source="ecmwf",
                ...     temporal_resolution="daily",
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables={
                ...         "reanalysis-era5-single-levels": ["2m-temperature"],
                ...     },
                ...     lat_lim=[4.0, 5.0],
                ...     lon_lim=[-75.0, -74.0],
                ...     path="examples/data/era5",
                ... )
                >>> earthly.download()  # doctest: +SKIP

                ```

        See Also:
            :class:`earthly.earthly.Earthly`: The
                user-facing facade that wires this method into the
                `download()` flow.
            :meth:`_download_dataset`: The single-variable wrapper that
                calls this method and then post-processes the NetCDF.
            :class:`Catalog`: Loads `var_info` dicts from
                `cds_data_catalog.yaml`.
        """
        dates = self.time.dates
        request = {
            "variable": [var_info.cds_variable],
            "year": sorted({str(d.year) for d in dates}),
            "month": sorted({f"{d.month:02d}" for d in dates}),
            "data_format": "netcdf",
            "area": [
                self.space.north,
                self.space.west,
                self.space.south,
                self.space.east,
            ],
        }

        # The dataset comes straight from the user's `variables` dict
        # (propagated through `Variable.cds_dataset` by the catalog
        # loader). `temporal_resolution` only shapes the request body;
        # it does not pick a different dataset and it does not pick
        # the `product_type` (that's catalog-driven now — see
        # `Variable.product_type`).
        dataset = var_info.cds_dataset
        request["product_type"] = var_info.product_type
        if self.temporal_resolution == "monthly":
            # Monthly-means CDS datasets reject `day` and want a
            # single `time` slot. CDS-Beta enforces this with HTTP
            # 400; the legacy CDS tolerated extra `day` entries.
            request["time"] = ["00:00"]
        else:
            request["day"] = sorted({f"{d.day:02d}" for d in dates})
            request["time"] = ["00:00", "06:00", "12:00", "18:00"]

        if var_info.cds_pressure_level is not None:
            request["pressure_level"] = var_info.cds_pressure_level

        # Merge per-variable extras last so a row-level field (e.g. a
        # CMIP6 `experiment` / `model` selector or a CARRA `domain`)
        # wins over any same-named template default. This is the escape
        # hatch the catalog uses to address non-ERA5 datasets.
        request.update(var_info.extras)

        # Strip template defaults that the dataset's request_kind
        # forbids (e.g. ORAS5 rejects `day`/`time`/`area`).
        # Done after the extras merge so a user can re-introduce a
        # stripped key by setting it explicitly in extras.
        for stripped in REQUEST_KIND_STRIPS.get(var_info.request_kind, ()):
            if stripped not in var_info.extras:
                request.pop(stripped, None)

        # Per-variable opt-out: any extras key explicitly set to
        # `None` in the YAML row is dropped from the request. This
        # is the per-row escape hatch for datasets that reject the
        # default `area` bbox (Atlas / projections / rotated grids)
        # without forcing the user to declare a new `request_kind`.
        for key, value in list(var_info.extras.items()):
            if value is None:
                request.pop(key, None)

        # Pre-flight check the assembled request against the CDS
        # `constraints.json` for this dataset. Catches typos and
        # invalid extras combinations client-side before they
        # consume a CDS queue slot. Pass `skip_constraints=True`
        # to `ECMWF(...)` to bypass.
        RequestValidator(dataset, request, skip=self.skip_constraints).check()

        target = self.root_dir / f"{var_info.cds_variable}_{dataset}.nc"
        logger.info(f"Requesting {dataset} from CDS; this may take several minutes")
        try:
            self.client.retrieve(dataset, request, str(target))
        except Exception as exc:
            if _looks_like_licence_not_accepted(exc):
                raise PermissionError(
                    f"CDS rejected the request for {dataset!r}: licence "
                    "not accepted. Open the dataset page at "
                    f"https://cds.climate.copernicus.eu/datasets/{dataset} "
                    "and tick the licence at the bottom of the "
                    "'Download' tab. The acceptance is permanent and "
                    "tied to your CDS account."
                ) from exc
            raise
        return target
