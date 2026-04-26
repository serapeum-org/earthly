from __future__ import annotations

import calendar
import datetime as dt
import os
from typing import Any

import cdsapi
import numpy as np
import pandas as pd
from loguru import logger
from pydantic import BaseModel, ConfigDict, ValidationError
from pyramids.netcdf import NetCDF
from serapeum_utils.utils import print_progress_bar

from earth2observe.base import (
    AbstractDataSource,
    SpatialExtent,
    TemporalExtent,
)


ERA5_GRID_DEGREES: float = 0.125


class AuthenticationError(Exception):
    """Raised when cdsapi cannot authenticate against the Climate Data Store.

    The ECMWF backend uses :class:`cdsapi.Client` to talk to CDS. The
    client reads its credentials from ``~/.cdsapirc`` (or the
    ``CDSAPI_URL`` / ``CDSAPI_KEY`` environment variables). If the
    config is missing or malformed, :meth:`ECMWF.initialize` wraps the
    underlying error in this exception so callers can distinguish auth
    problems from generic CDS server errors.

    See Also:
        https://cds.climate.copernicus.eu/how-to-api: Official cdsapi
            setup guide, including PAT generation and the
            ``~/.cdsapirc`` format.
    """

    pass


class Variable(BaseModel):
    """Per-variable catalog entry consumed by :class:`ECMWF`.

    A frozen pydantic model carrying the metadata for one row in
    ``cds_data_catalog.yaml``. Loading the YAML through
    :meth:`from_dict` validates required fields up front so a typo
    in the file (e.g. ``factor_add`` vs ``factors_add``) surfaces at
    import time, not mid-download.

    Attributes:
        cds_dataset: CDS dataset short name used for daily / sub-daily
            requests, e.g. ``"reanalysis-era5-single-levels"``.
        cds_variable: CDS variable name passed in the retrieve()
            request, e.g. ``"2m_temperature"``.
        nc_variable: Short variable name inside the CDS NetCDF
            (e.g. ``"t2m"``); :meth:`ECMWF.post_download` uses it to
            index ``fh.variables[...]``.
        units: Output unit string after the conversion factors are
            applied (used in the output filename).
        factors_add: Optional additive offset applied during
            post-processing. Defaults to ``0.0`` (no offset) when
            absent from the YAML.
        factors_mul: Optional multiplicative scale applied during
            post-processing. Defaults to ``1.0`` (identity) when
            absent.
        cds_dataset_monthly: Optional CDS dataset short name used
            when ``temporal_resolution == "monthly"``. Falls back to
            ``cds_dataset`` when absent.
        cds_pressure_level: Optional list of pressure levels (as
            strings, e.g. ``["1000"]``) for pressure-level datasets.
        types: Optional ``"flux"`` or ``"state"`` marker. Flux values
            are accumulated per timestep on CDS so monthly
            aggregation multiplies by the number of days in the
            month; state values are instantaneous.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    cds_dataset: str
    cds_variable: str
    nc_variable: str
    units: str
    factors_add: float = 0.0
    factors_mul: float = 1.0
    cds_dataset_monthly: str | None = None
    cds_pressure_level: list[str] | None = None
    types: str | None = None

    @classmethod
    def from_dict(cls, code: str, data: dict[str, Any]) -> Variable:
        """Build a :class:`Variable` from a raw catalog entry.

        Wraps :class:`pydantic.ValidationError` so the message names
        the catalog row that failed.

        Args:
            code: Catalog key (e.g. ``"2m-temperature"``) — used only in the
                error message so the user can see which row is broken.
            data: The dict loaded from the YAML for ``code``.

        Returns:
            Variable: The validated, frozen instance.

        Raises:
            ValueError: If a required key is missing or an unknown
                key is present (catches typos like ``factor_add``
                vs ``factors_add``).
        """
        try:
            return cls(**data)
        except ValidationError as exc:
            raise ValueError(
                f"cds_data_catalog.yaml entry {code!r} failed "
                f"validation:\n{exc}"
            ) from exc

    def dataset_for(self, temporal_resolution: str) -> str:
        """Return the CDS dataset name to use for ``temporal_resolution``.

        Args:
            temporal_resolution: ``"daily"`` or ``"monthly"``.

        Returns:
            str: ``cds_dataset_monthly`` when
            ``temporal_resolution == "monthly"`` and the monthly
            variant is set; ``cds_dataset`` otherwise.
        """
        if temporal_resolution == "monthly" and self.cds_dataset_monthly:
            return self.cds_dataset_monthly
        return self.cds_dataset

    @property
    def is_flux(self) -> bool:
        """True if ``types == "flux"`` (drives monthly accumulation scaling)."""
        return self.types == "flux"


def _looks_like_missing_credentials(exc: BaseException) -> bool:
    """Heuristic: does this exception come from missing CDS credentials?

    cdsapi does not expose typed exception classes for auth failures —
    they surface as generic ``Exception`` with messages like "Missing/
    incomplete configuration file" or "key not found". We classify by
    presence of the dotfile and env vars first (no dotfile + no env
    vars → almost certainly missing credentials), then fall back to a
    keyword scan of the exception message.

    Args:
        exc: The exception raised by ``cdsapi.Client()``.

    Returns:
        True when the failure looks like a credential / config-file
        problem (so it is safe to wrap as :class:`AuthenticationError`),
        False for transport / network / library errors that should
        propagate untouched.
    """
    cdsapirc_present = os.path.isfile(
        os.path.expanduser("~/.cdsapirc")
    )
    env_present = bool(
        os.environ.get("CDSAPI_URL") and os.environ.get("CDSAPI_KEY")
    )
    if not cdsapirc_present and not env_present:
        return True
    message = str(exc).lower()
    auth_keywords = (
        "configuration",
        "credentials",
        "cdsapirc",
        "key not found",
        "missing url",
        "missing key",
    )
    return any(keyword in message for keyword in auth_keywords)


_TIME_VAR_CANDIDATES: tuple[str, ...] = ("valid_time", "time")


def _read_time_axis(fh: NetCDF) -> pd.DatetimeIndex:
    """Read the time coordinate from a CDS NetCDF as datetimes.

    CDS-Beta switched the time variable name from ``time`` (legacy)
    to ``valid_time`` (current) and the units from
    ``"hours since 1900-01-01"`` to ``"seconds since 1970-01-01"``.
    This helper hides both differences from the caller: it tries
    each candidate name in :data:`_TIME_VAR_CANDIDATES` and parses
    the raw integer values via the variable's ``unit`` attribute,
    returning a :class:`pandas.DatetimeIndex` regardless of which
    flavour of NetCDF the server emits.
    """
    metadata_vars = fh.meta_data.variables
    for name in _TIME_VAR_CANDIDATES:
        if name not in metadata_vars:
            continue
        units = metadata_vars[name].unit
        raw = fh._read_variable(name)
        if units is None or raw is None:
            continue
        # ``units`` is a CF string like "<unit> since <epoch>".
        # ``pd.to_datetime(raw, unit=<u>, origin=<o>)`` parses it
        # natively for the unit aliases pandas recognises (s, m, h, D).
        unit_word, _, origin = units.partition(" since ")
        unit_alias = {
            "seconds": "s",
            "minutes": "m",
            "hours": "h",
            "days": "D",
        }.get(unit_word.strip().lower())
        if unit_alias is None:
            raise ValueError(
                f"unsupported time unit {unit_word!r} on {name!r} "
                f"(full units string: {units!r})"
            )
        return pd.to_datetime(raw, unit=unit_alias, origin=origin.strip())
    raise KeyError(
        f"NetCDF at {fh.file_name!r} has no recognised time variable "
        f"(tried {list(_TIME_VAR_CANDIDATES)}; got "
        f"{sorted(metadata_vars)})"
    )


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
        exc: The exception raised by ``client.retrieve(...)``.

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
    short codes (e.g. ``"2m-temperature"``, ``"total-precipitation"``) are resolved through
    :class:`Catalog`, which loads the per-variable metadata from
    ``cds_data_catalog.yaml``.

    The two-step pipeline (per variable) is:

    1. :meth:`api` — build the cdsapi request dict (daily / monthly
       branch on ``temporal_resolution``) and submit it via
       ``client.retrieve(dataset, request, target)``. Returns the
       absolute path to the NetCDF that CDS wrote.
    2. :meth:`post_download` — open that NetCDF, slice it on the
       time axis, and apply the ``factors_add``/``factors_mul``
       conversion. Per-date GeoTIFF writing is currently stubbed
       (see ``planning/cdsapi/post-review-findings.md`` C1).

    Attributes:
        temporal_resolution: Class-level list of valid temporal
            resolutions accepted by the backend. The instance-level
            spatial cell size lives on :attr:`SpatialExtent.resolution`
            (populated by :meth:`create_grid`) and is sourced from
            :data:`ERA5_GRID_DEGREES`.
    """

    temporal_resolution = ["daily", "monthly"]

    def __init__(
        self,
        temporal_resolution: str = "daily",
        start: str = None,
        end: str = None,
        path: str = "",
        variables: list = None,
        lat_lim: list = None,
        lon_lim: list = None,
        fmt: str = "%Y-%m-%d",
    ):
        """Initialize an ECMWF backend instance.

        Forwards every argument to :class:`AbstractDataSource`,
        which captures the cdsapi client into ``self.client`` and
        the bbox/date dict into ``self.space``/``self.time``.

        Args:
            temporal_resolution: Either ``"daily"`` or ``"monthly"``.
                Defaults to ``"daily"``.
            start: Inclusive start date as a string (parsed with
                ``fmt``). Defaults to ``None``.
            end: Inclusive end date as a string. Defaults to ``None``.
            path: Output directory. Created by the parent if it does
                not exist. Defaults to the current working directory.
            variables: list of CDS catalog short codes (e.g.
                ``["2m-temperature", "total-precipitation"]``); see ``cds_data_catalog.yaml`` for
                the registered codes.
            lat_lim: ``[lat_min, lat_max]``.
            lon_lim: ``[lon_min, lon_max]``.
            fmt: ``strptime`` format for ``start`` / ``end``.
                Defaults to ``"%Y-%m-%d"``.
        """
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

    def check_input_dates(
        self, start: str, end: str, temporal_resolution: str, fmt: str
    ):
        """Parse the date range and produce the iteration index.

        Returned dict is captured by
        :meth:`AbstractDataSource.__init__` into ``self.time`` so
        :meth:`api` and :meth:`post_download` can access the parsed
        bounds and the per-date pandas range without re-parsing.

        Args:
            start: Inclusive start date as a string.
            end: Inclusive end date as a string.
            temporal_resolution: ``"daily"`` (uses ``freq="D"``) or
                ``"monthly"`` (uses ``freq="MS"``).
            fmt: ``strptime`` format applied to ``start`` and ``end``.

        Returns:
            TemporalExtent: Frozen pydantic model with ``start_date``,
            ``end_date``, ``resolution`` (pandas frequency alias —
            ``"D"`` for daily, ``"MS"`` for month-start), and
            ``dates`` (the :class:`pandas.DatetimeIndex` the
            download loop iterates).

        Raises:
            ValueError: If ``temporal_resolution`` is neither
                ``"daily"`` nor ``"monthly"``, or if the parsed
                ``start`` is later than the parsed ``end``.
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

    def initialize(self):
        """Construct the :class:`cdsapi.Client` for talking to CDS.

        Reads credentials from ``~/.cdsapirc`` (or the ``CDSAPI_URL`` /
        ``CDSAPI_KEY`` environment variables, which cdsapi falls back to
        when the dotfile is absent). If neither is configured, the
        underlying cdsapi exception is wrapped in
        :class:`AuthenticationError` with a message that tells the user
        exactly where to put their Personal Access Token.

        Returns:
            cdsapi.Client: Authenticated CDS client. Calls to
            ``client.retrieve(...)`` use this connection.

        Raises:
            AuthenticationError: If cdsapi cannot construct a Client —
                typically because ``~/.cdsapirc`` is missing,
                malformed, or contains an old-API-style ``email`` line.

        Examples:
            - Construct a client when credentials are properly
              configured. Marked ``# doctest: +SKIP`` because it
              requires a real ``~/.cdsapirc``:

                ```python
                >>> ecmwf = ECMWF(  # doctest: +SKIP
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables=["2m-temperature"],
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
                    "download. See "
                    "https://cds.climate.copernicus.eu/how-to-api for "
                    "the full setup guide."
                ) from exc
            raise

        return client

    def create_grid(self, lat_lim: list, lon_lim: list):
        """Create_grid.

            create grid from the lat/lon boundaries

        Parameters
        ----------
        lat_lim: []
            latitude boundaries
        lon_lim: []
            longitude boundaries
        """
        cell_size = ERA5_GRID_DEGREES
        # correct latitude and longitude limits
        lat_lim_floor = np.floor(lat_lim[0] / cell_size) * cell_size
        lat_lim_ceil = np.ceil(lat_lim[1] / cell_size) * cell_size
        lat_lim = [lat_lim_floor, lat_lim_ceil]

        # correct latitude and longitude limits
        lon_lim_floor = np.floor(lon_lim[0] / cell_size) * cell_size
        lon_lim_ceil = np.ceil(lon_lim[1] / cell_size) * cell_size
        lon_lim = [lon_lim_floor, lon_lim_ceil]
        return SpatialExtent.from_pairs(
            lat_lim=lat_lim, lon_lim=lon_lim, resolution=cell_size
        )

    def download(self, progress_bar: bool = True, *args, **kwargs):
        """Download every variable in ``self.vars`` from CDS.

        Iterates the user-supplied ``variables`` list and, for each
        short code, looks the variable up in the CDS :class:`Catalog`
        and delegates to :meth:`download_dataset`. The CDS dataset
        name is per-variable (``var_info["cds_dataset"]``); there is
        no global ``dataset`` parameter under the cdsapi flow.

        Args:
            progress_bar: Whether :meth:`post_download` should print
                a per-date progress bar inside each variable's
                post-processing loop. Defaults to ``True``.
            *args: Reserved; ignored. Kept for forward-compatibility
                with backend-specific extras callers might pass via
                :meth:`Earth2Observe.download`.
            **kwargs: Reserved; ignored. Same rationale as ``*args``.

        Returns:
            None. Per-variable NetCDFs land at
            ``<self.root_dir>/<cds_variable>_<cds_dataset>.nc`` and
            the post-processed per-date GeoTIFFs alongside.

        Raises:
            KeyError: If any ``var`` in ``self.vars`` is not in the
                CDS catalog.
            Exception: Any error :meth:`api` propagates from
                :meth:`cdsapi.Client.retrieve`.

        Examples:
            - End-to-end download via the user-facing
              :class:`Earth2Observe` facade. Marked
              ``# doctest: +SKIP`` because it requires a configured
              ``~/.cdsapirc`` and several minutes of CDS queue time:

                ```python
                >>> from earth2observe.earth2observe import Earth2Observe
                >>> e2o = Earth2Observe(  # doctest: +SKIP
                ...     data_source="ecmwf",
                ...     temporal_resolution="daily",
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables=["2m-temperature", "total-precipitation"],
                ...     lat_lim=[4.0, 5.0],
                ...     lon_lim=[-75.0, -74.0],
                ...     path="examples/data/era5",
                ... )
                >>> e2o.download()  # doctest: +SKIP

                ```

        See Also:
            :meth:`download_dataset`: Per-variable download +
                post-processing.
            :meth:`api`: Builds and submits the cdsapi request.
            :class:`Catalog`: Resolves short codes to per-variable
                metadata.
        """
        # Lazy import to avoid a circular dependency: ``catalog.py``
        # imports ``Variable`` from this module, so a top-level
        # import of ``Catalog`` would be cyclic.
        from earth2observe.ecmwf.catalog import Catalog

        catalog = Catalog()
        succeeded: list[str] = []
        failed: list[tuple[str, BaseException]] = []

        for var in self.vars:
            start = self.time.start_date
            end = self.time.end_date
            logger.info(
                f"Download ECMWF {var} data for period {start} till {end}"
            )
            try:
                var_info = catalog.get_dataset(var)
                self.download_dataset(var_info, progress_bar=progress_bar)
            except Exception as exc:
                logger.error(
                    f"ECMWF download for {var!r} failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                failed.append((var, exc))
            else:
                succeeded.append(var)

        if failed:
            failed_summary = ", ".join(
                f"{var} ({type(exc).__name__})" for var, exc in failed
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

    def download_dataset(
        self,
        var_info: dict[str, str],
        progress_bar: bool = True,
    ):
        """Download a single variable from CDS and post-process the NetCDF.

        Two-step pipeline:

        1. :meth:`api` builds the cdsapi request, submits it, and
           returns the absolute :class:`pathlib.Path` to the NetCDF
           that CDS wrote.
        2. :meth:`post_download` opens that exact file, applies the
           catalog's unit-conversion factors, and slices it into
           per-date outputs under ``self.root_dir``.

        Threading the path returned by :meth:`api` into
        :meth:`post_download` (rather than reconstructing a
        ``data_<dataset>.nc`` filename) is what made the H1 fix:
        ``api()`` writes ``<cds_variable>_<cds_dataset>.nc`` while
        the legacy code was looking for ``data_<dataset>.nc``, so
        the two never agreed.

        Args:
            var_info: Variable metadata pulled from
                ``cds_data_catalog.yaml`` via :class:`Catalog`. See
                :meth:`api` for the keys :meth:`api` requires and
                :meth:`post_download` for the additional keys
                (``nc_variable``, optional ``types``) that the
                post-processing step reads.
            progress_bar: Whether :meth:`post_download` should print
                a progress bar during the per-date loop. Defaults to
                ``True``.

        Returns:
            None.

        Raises:
            KeyError: If ``var_info`` is missing one of the keys
                required by :meth:`api` (``cds_dataset``,
                ``cds_variable``) or by :meth:`post_download`
                (``nc_variable``, ``units``, ``factors_add``,
                ``factors_mul``).

        Examples:
            - The catalog ships ``var_info`` dicts ready for this
              method; inspect the keys this two-step pipeline reads:

                ```python
                >>> var_info = {
                ...     "cds_dataset": "reanalysis-era5-single-levels",
                ...     "cds_variable": "2m_temperature",
                ...     "nc_variable": "t2m",
                ...     "types": "state",
                ...     "units": "C",
                ...     "factors_add": -273.15,
                ...     "factors_mul": 1,
                ... }
                >>> var_info["cds_variable"], var_info["nc_variable"]
                ('2m_temperature', 't2m')
                >>> f"{var_info['cds_variable']}_{var_info['cds_dataset']}.nc"
                '2m_temperature_reanalysis-era5-single-levels.nc'

                ```
            - Download via the user-facing :class:`Earth2Observe`
              facade (recommended). Marked ``# doctest: +SKIP``
              because it requires a configured ``~/.cdsapirc`` and
              several minutes of CDS queue time:

                ```python
                >>> from earth2observe.earth2observe import Earth2Observe  # doctest: +SKIP
                >>> e2o = Earth2Observe(  # doctest: +SKIP
                ...     data_source="ecmwf",
                ...     temporal_resolution="daily",
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables=["2m-temperature"],
                ...     lat_lim=[4.0, 5.0],
                ...     lon_lim=[-75.0, -74.0],
                ...     path="examples/data/era5",
                ... )
                >>> e2o.download()  # doctest: +SKIP

                ```

        See Also:
            :meth:`api`: Builds and submits the CDS request, returns
                the path to the NetCDF.
            :meth:`post_download`: Reads the NetCDF at the path
                produced by :meth:`api`, applies unit conversions,
                slices into per-date outputs.
            :class:`Catalog`: Loads ``var_info`` dicts from
                ``cds_data_catalog.yaml``.
        """
        nc_path = self.api(var_info)
        self.post_download(var_info, nc_path, progress_bar)

    def api(self, var_info: dict[str, str]):
        """Build a CDS request and submit it via :class:`cdsapi.Client`.

        Constructs the request dictionary expected by
        :meth:`cdsapi.Client.retrieve` from the catalog metadata for a
        single variable, then submits it. The retrieve call blocks until
        CDS has served the request and the NetCDF file has been written
        to disk — typically minutes due to CDS queue times.

        The request shape branches on ``self.temporal_resolution``:

        * ``"daily"`` — submits to ``var_info['cds_dataset']`` with
          ``product_type=['reanalysis']`` and four six-hourly time
          slots (``00:00/06:00/12:00/18:00``).
        * ``"monthly"`` — submits to ``var_info['cds_dataset_monthly']``
          (falling back to ``cds_dataset`` when the monthly key is
          absent) with ``product_type=['monthly_averaged_reanalysis']``
          and no ``time`` key. ``-monthly-means`` datasets reject the
          daily-style ``time`` list.

        Both branches use ``data_format='netcdf'`` so
        :class:`pyramids.netcdf.NetCDF` can read the result in
        ``post_download``.

        Args:
            var_info: Variable metadata pulled from
                ``cds_data_catalog.yaml`` via :class:`Catalog`. Required
                keys:

                * ``cds_dataset`` — CDS dataset short name, e.g.
                  ``"reanalysis-era5-single-levels"``.
                * ``cds_variable`` — CDS variable name, e.g.
                  ``"2m_temperature"``. Also used as the output
                  filename stem.

                Optional keys:

                * ``cds_pressure_level`` — Forwarded to the request as
                  ``pressure_level`` for pressure-level datasets.

        Returns:
            pathlib.Path: Absolute path to the downloaded NetCDF file,
            written to
            ``<self.root_dir>/<cds_variable>_<cds_dataset>.nc``.

        Raises:
            KeyError: If ``var_info`` is missing one of the required
                keys (``cds_dataset`` or ``cds_variable``).
            Exception: Any error raised by
                :meth:`cdsapi.Client.retrieve`, including authentication
                failures (no ``~/.cdsapirc``), licence-not-accepted
                errors, or transient CDS server errors.

        Examples:
            - Inspect a single-level :class:`Variable` and the
              file name it produces:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> spec = Catalog().get_dataset("2m-temperature")
                >>> spec.cds_dataset
                'reanalysis-era5-single-levels'
                >>> f"{spec.cds_variable}_{spec.cds_dataset}.nc"
                '2m_temperature_reanalysis-era5-single-levels.nc'

                ```
            - Pressure-level variables expose ``cds_pressure_level``;
              :meth:`api` forwards it to the request:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> spec = Catalog().get_dataset("temperature")
                >>> spec.cds_pressure_level
                ['1000']

                ```
            - Submit the request through the user-facing
              :class:`Earth2Observe` facade. Marked
              ``# doctest: +SKIP`` because it requires a configured
              ``~/.cdsapirc`` and several minutes of CDS queue time:

                ```python
                >>> from earth2observe.earth2observe import Earth2Observe  # doctest: +SKIP
                >>> e2o = Earth2Observe(  # doctest: +SKIP
                ...     data_source="ecmwf",
                ...     temporal_resolution="daily",
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables=["2m-temperature"],
                ...     lat_lim=[4.0, 5.0],
                ...     lon_lim=[-75.0, -74.0],
                ...     path="examples/data/era5",
                ... )
                >>> e2o.download()  # doctest: +SKIP

                ```

        See Also:
            :class:`earth2observe.earth2observe.Earth2Observe`: The
                user-facing facade that wires this method into the
                ``download()`` flow.
            :meth:`download_dataset`: The single-variable wrapper that
                calls this method and then post-processes the NetCDF.
            :class:`Catalog`: Loads ``var_info`` dicts from
                ``cds_data_catalog.yaml``.
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

        dataset = var_info.dataset_for(self.temporal_resolution)
        if self.temporal_resolution == "monthly":
            # ``-monthly-means`` datasets reject ``day`` (the
            # aggregate is over a whole month) and require a
            # single ``time`` slot for ``monthly_averaged_reanalysis``.
            # CDS-Beta enforces this with HTTP 400; the legacy CDS
            # tolerated extra ``day`` entries.
            request["product_type"] = ["monthly_averaged_reanalysis"]
            request["time"] = ["00:00"]
        else:
            request["product_type"] = ["reanalysis"]
            request["day"] = sorted({f"{d.day:02d}" for d in dates})
            request["time"] = ["00:00", "06:00", "12:00", "18:00"]

        if var_info.cds_pressure_level is not None:
            request["pressure_level"] = var_info.cds_pressure_level

        target = self.root_dir / f"{var_info.cds_variable}_{dataset}.nc"
        logger.info(
            f"Requesting {dataset} from CDS; this may take several minutes"
        )
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

    def API(self, *args, **kwargs):  # noqa: N802 — name dictated by the abstract base
        """Compatibility shim satisfying :meth:`AbstractDataSource.API`.

        The abstract base class declares ``API`` (uppercase) as
        abstract. The ECMWF backend works at variable granularity and
        exposes its real hook as :meth:`api` (lowercase) accepting a
        ``var_info`` dict — a different signature than the per-date
        callable shape of CHIRPS / S3. This stub exists only so the
        abstract contract is satisfied and :class:`ECMWF` can be
        instantiated; callers should always use :meth:`api`.

        Raises:
            NotImplementedError: Always. ECMWF requests are built
                and submitted from :meth:`api`, not from this method.
        """
        raise NotImplementedError(
            "ECMWF uses the lowercase api(var_info) — see ECMWF.api"
        )

    def post_download(
        self, var_info: dict[str, str], nc_path, progress_bar: bool = True
    ):
        """Slice the downloaded NetCDF into per-date GeoTIFFs.

        Reads the NetCDF written by :meth:`api`, applies the
        unit-conversion factors from the catalog, and produces one
        per-date output under ``self.root_dir``.

        Args:
            var_info: Catalog metadata for the variable. Required keys:

                * ``nc_variable`` — short variable name inside the
                  CDS NetCDF (e.g. ``"t2m"`` for 2-metre temperature),
                  used to index ``fh.variables[...]``. Differs from
                  ``cds_variable`` (which is the request name and
                  also the output filename stem).
                * ``units`` — output unit string used in the output
                  file name.
                * ``factors_add`` / ``factors_mul`` — additive offset
                  and multiplicative scale applied to each cell.

                Optional keys:

                * ``types`` — ``"flux"`` or ``"state"``. Flux values
                  are accumulated per timestep on CDS, so monthly
                  aggregation multiplies by the number of days in the
                  month. Defaults to ``"state"`` when absent.

            nc_path: Path to the NetCDF written by :meth:`api`. Either
                a :class:`pathlib.Path` or a string is accepted.
            progress_bar: Whether to print a per-date progress bar.
                Defaults to ``True``.

        Returns:
            None. Per-date TIF writing via :mod:`pyramids` is currently
            stubbed (commented out below the unit-conversion); the
            method runs the read / slice / convert pipeline so callers
            can verify the request shape end-to-end and is wired up to
            output once the GeoTIFF integration is restored.
        """
        logger.warning(
            "ECMWF.post_download: GeoTIFF output is currently disabled "
            "(pyramids write integration pending). The NetCDF at %s "
            "is the only artefact produced; no per-date .tif files "
            "will be created.",
            nc_path,
        )

        nc_variable = var_info.nc_variable
        unit_label = var_info.units
        factors_add = var_info.factors_add
        factors_mul = var_info.factors_mul
        is_flux = var_info.is_flux
        per_date_outputs: list[tuple[Any, Any, str]] = []

        with NetCDF.read_file(str(nc_path), read_only=True) as fh:
            Data = fh.read_array(variable=nc_variable)
            Data_time = _read_time_axis(fh)
            lons = fh.lon
            lats = fh.lat

            geo_four = np.nanmax(lats)
            geo_one = np.nanmin(lons)
            cell_size = self.space.resolution
            geo = tuple(
                [
                    geo_one,
                    cell_size,
                    0.0,
                    geo_four,
                    0.0,
                    -1 * cell_size,
                ]
            )

            if progress_bar:
                total_amount = len(self.time.dates)
                amount = 0
                print_progress_bar(
                    amount, total_amount, prefix="Progress:", suffix="Complete", length=50
                )

            for date in self.time.dates:

                year = date.year
                month = date.month
                day = date.day

                if self.temporal_resolution == "daily":
                    days_later = 1
                elif self.temporal_resolution == "monthly":
                    days_later = calendar.monthrange(year, month)[1]

                window_start = pd.Timestamp(year=year, month=month, day=day)
                window_end = window_start + pd.Timedelta(days=days_later)

                in_window = (Data_time >= window_start) & (Data_time < window_end)

                Data_one = Data[in_window, :, :]

                Data_end = factors_mul * np.nanmean(Data_one, 0) + factors_add

                if is_flux:
                    Data_end = Data_end * days_later

                name_out = os.path.join(
                    self.root_dir,
                    f"{var_info.cds_variable}_ECMWF_ERA5_{unit_label}_{self.temporal_resolution}_{year}.{month}.{day}.tif",
                )
                per_date_outputs.append((date, Data_end, name_out))

                # Create Tiff files
                # Raster.Save_as_tiff(name_out, Data_end, geo, "WGS84")
                # Raster.createRaster(path=name_out, arr=Data_end, geo=geo, epsg="WGS84")

                if progress_bar:
                    amount = amount + 1
                    print_progress_bar(
                        amount,
                        total_amount,
                        prefix="Progress:",
                        suffix="Complete",
                        length=50,
                    )

        return per_date_outputs
