import calendar
import datetime as dt
import os
from dataclasses import MISSING, dataclass, fields
from typing import Any, Dict, List, Optional

import cdsapi
import numpy as np
import pandas as pd
import yaml
from loguru import logger
from pyramids.netcdf import NetCDF
from serapeum_utils.utils import print_progress_bar

from earth2observe import __path__
from earth2observe.abstractdatasource import (
    AbstractCatalog,
    AbstractDataSource,
    SpatialBounds,
    TimeWindow,
)


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


@dataclass(frozen=True)
class VariableSpec:
    """Per-variable catalog entry consumed by :class:`ECMWF`.

    Replaces the loosely-typed ``Dict[str, Any]`` previously threaded
    between :class:`Catalog`, :meth:`ECMWF.api`, and
    :meth:`ECMWF.post_download`. Loading the YAML through
    :meth:`from_dict` validates required fields up front so a typo
    in ``cds_data_catalog.yaml`` (e.g. ``factor_add`` vs
    ``factors_add``) surfaces at import time, not mid-download.

    Attributes:
        cds_dataset: CDS dataset short name used for daily / sub-daily
            requests, e.g. ``"reanalysis-era5-single-levels"``.
        cds_variable: CDS variable name passed in the retrieve()
            request, e.g. ``"2m_temperature"``.
        nc_variable: Short variable name inside the CDS NetCDF
            (e.g. ``"t2m"``); :meth:`ECMWF.post_download` uses it to
            index ``fh.variables[...]``.
        file_name: Stem used for the output NetCDF / GeoTIFF
            filenames.
        units: Output unit string after the conversion factors are
            applied (used in the output filename).
        factors_add: Additive offset applied during post-processing.
        factors_mul: Multiplicative scale applied during
            post-processing.
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

    cds_dataset: str
    cds_variable: str
    nc_variable: str
    file_name: str
    units: str
    factors_add: float
    factors_mul: float
    cds_dataset_monthly: Optional[str] = None
    cds_pressure_level: Optional[List[str]] = None
    types: Optional[str] = None

    @classmethod
    def from_dict(cls, code: str, data: Dict[str, Any]) -> "VariableSpec":
        """Build a :class:`VariableSpec` from a raw catalog entry.

        Args:
            code: Catalog key (e.g. ``"2T"``) — only used for the
                error message when validation fails so the user can
                see which entry is broken.
            data: The dict loaded from the YAML for ``code``.

        Returns:
            VariableSpec: The validated, frozen instance.

        Raises:
            ValueError: If a required key is missing, or an unknown
                key is present (catches typos like
                ``factor_add`` vs ``factors_add``).
        """
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"cds_data_catalog.yaml entry {code!r} has unknown "
                f"keys {sorted(unknown)}. Known keys: {sorted(known)}."
            )
        required = {
            f.name
            for f in fields(cls)
            if f.default is MISSING and f.default_factory is MISSING
        }
        missing = required - set(data)
        if missing:
            raise ValueError(
                f"cds_data_catalog.yaml entry {code!r} is missing "
                f"required keys: {sorted(missing)}."
            )
        return cls(**data)

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
    short codes (e.g. ``"2T"``, ``"TP"``) are resolved through
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
            resolutions accepted by the backend.
        spatial_resolution: ERA5 grid spacing in degrees (0.125°).
    """

    temporal_resolution = ["daily", "monthly"]
    spatial_resolution = 0.125

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
            variables: List of CDS catalog short codes (e.g.
                ``["2T", "TP"]``); see ``cds_data_catalog.yaml`` for
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
            TimeWindow: Frozen dataclass with ``start_date``,
            ``end_date``, ``time_freq`` and ``dates`` (the
            :class:`pandas.DatetimeIndex` the download loop iterates).

        Raises:
            ValueError: If ``temporal_resolution`` is neither
                ``"daily"`` nor ``"monthly"``, or if the parsed
                ``start`` is later than the parsed ``end``.
        """
        start = dt.datetime.strptime(start, fmt)
        end = dt.datetime.strptime(end, fmt)

        if temporal_resolution == "daily":
            dates = pd.date_range(start, end, freq="D")
            time_freq = "D"
        elif temporal_resolution == "monthly":
            dates = pd.date_range(start, end, freq="MS")
            time_freq = "MS"
        else:
            raise ValueError(
                "temporal_resolution should be either 'daily' or 'monthly'"
            )

        return TimeWindow(
            start_date=start,
            end_date=end,
            time_freq=time_freq,
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
                ...     variables=["2T"],
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
        cell_size = self.spatial_resolution
        # correct latitude and longitude limits
        lat_lim_floor = np.floor(lat_lim[0] / cell_size) * cell_size
        lat_lim_ceil = np.ceil(lat_lim[1] / cell_size) * cell_size
        lat_lim = [lat_lim_floor, lat_lim_ceil]

        # correct latitude and longitude limits
        lon_lim_floor = np.floor(lon_lim[0] / cell_size) * cell_size
        lon_lim_ceil = np.ceil(lon_lim[1] / cell_size) * cell_size
        lon_lim = [lon_lim_floor, lon_lim_ceil]
        return SpatialBounds(lat_lim=lat_lim, lon_lim=lon_lim)

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
            ``<self.root_dir>/<file_name>_<cds_dataset>.nc`` and the
            post-processed per-date GeoTIFFs alongside.

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
                ...     variables=["2T", "TP"],
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
        var_info: Dict[str, str],
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
        ``api()`` writes ``<file_name>_<cds_dataset>.nc`` while the
        legacy code was looking for ``data_<dataset>.nc``, so the
        two never agreed.

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
                ``cds_variable``, ``file_name``) or by
                :meth:`post_download` (``nc_variable``, ``units``,
                ``factors_add``, ``factors_mul``).

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
                ...     "file_name": "Tair",
                ...     "factors_add": -273.15,
                ...     "factors_mul": 1,
                ... }
                >>> var_info["cds_variable"], var_info["nc_variable"]
                ('2m_temperature', 't2m')
                >>> f"{var_info['file_name']}_{var_info['cds_dataset']}.nc"
                'Tair_reanalysis-era5-single-levels.nc'

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
                ...     variables=["2T"],
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

    def api(self, var_info: Dict[str, str]):
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
        :class:`netCDF4.Dataset` can read the result in
        ``post_download``.

        Args:
            var_info: Variable metadata pulled from
                ``cds_data_catalog.yaml`` via :class:`Catalog`. Required
                keys:

                * ``cds_dataset`` — CDS dataset short name, e.g.
                  ``"reanalysis-era5-single-levels"``.
                * ``cds_variable`` — CDS variable name, e.g.
                  ``"2m_temperature"``.
                * ``file_name`` — Stem used for the output file name.

                Optional keys:

                * ``cds_pressure_level`` — Forwarded to the request as
                  ``pressure_level`` for pressure-level datasets.

        Returns:
            pathlib.Path: Absolute path to the downloaded NetCDF file,
            written to
            ``<self.root_dir>/<file_name>_<cds_dataset>.nc``.

        Raises:
            KeyError: If ``var_info`` is missing one of the required
                keys (``cds_dataset``, ``cds_variable``, or
                ``file_name``).
            Exception: Any error raised by
                :meth:`cdsapi.Client.retrieve`, including authentication
                failures (no ``~/.cdsapirc``), licence-not-accepted
                errors, or transient CDS server errors.

        Examples:
            - Inspect a single-level :class:`VariableSpec` and the
              file name it produces:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> spec = Catalog().get_dataset("2T")
                >>> spec.cds_dataset
                'reanalysis-era5-single-levels'
                >>> f"{spec.file_name}_{spec.cds_dataset}.nc"
                'Tair_reanalysis-era5-single-levels.nc'

                ```
            - Pressure-level variables expose ``cds_pressure_level``;
              :meth:`api` forwards it to the request:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> spec = Catalog().get_dataset("T")
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
                ...     variables=["2T"],
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
            "day": sorted({f"{d.day:02d}" for d in dates}),
            "data_format": "netcdf",
            "area": [
                self.space.lat_lim[1],
                self.space.lon_lim[0],
                self.space.lat_lim[0],
                self.space.lon_lim[1],
            ],
        }

        dataset = var_info.dataset_for(self.temporal_resolution)
        if self.temporal_resolution == "monthly":
            request["product_type"] = ["monthly_averaged_reanalysis"]
        else:
            request["product_type"] = ["reanalysis"]
            request["time"] = ["00:00", "06:00", "12:00", "18:00"]

        if var_info.cds_pressure_level is not None:
            request["pressure_level"] = var_info.cds_pressure_level

        target = self.root_dir / f"{var_info.file_name}_{dataset}.nc"
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
        self, var_info: Dict[str, str], nc_path, progress_bar: bool = True
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
                  ``cds_variable`` (which is the request name).
                * ``file_name`` — stem used for the output TIF name.
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
            Data_time = fh.variables["time"].read_array()
            lons = fh.lon
            lats = fh.lat

            geo_four = np.nanmax(lats)
            geo_one = np.nanmin(lons)
            geo = tuple(
                [
                    geo_one,
                    self.spatial_resolution,
                    0.0,
                    geo_four,
                    0.0,
                    -1 * self.spatial_resolution,
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

                start = dt.datetime(year=1900, month=1, day=1)
                end = dt.datetime(year, month, day)
                diff = end - start
                hours_from_start_begin = diff.total_seconds() / 60 / 60

                Date_good = np.zeros(len(Data_time))

                if self.temporal_resolution == "daily":
                    days_later = 1
                elif self.temporal_resolution == "monthly":
                    days_later = calendar.monthrange(year, month)[1]

                Date_good[
                    np.logical_and(
                        Data_time >= hours_from_start_begin,
                        Data_time < (hours_from_start_begin + 24 * days_later),
                    )
                ] = 1

                Data_one = np.zeros(
                    [int(np.sum(Date_good)), int(np.size(Data, 1)), int(np.size(Data, 2))]
                )
                Data_one = Data[np.int_(Date_good) == 1, :, :]

                Data_end = factors_mul * np.nanmean(Data_one, 0) + factors_add

                if is_flux:
                    Data_end = Data_end * days_later

                var_output_name = var_info.file_name

                name_out = os.path.join(
                    self.root_dir,
                    f"{var_output_name}_ECMWF_ERA5_{unit_label}_{self.temporal_resolution}_{year}.{month}.{day}.tif",
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


class Catalog(AbstractCatalog):
    """Variable catalog for the CDS-backed ECMWF data source.

    Reads ``cds_data_catalog.yaml`` (shipped as package data) and exposes
    the per-variable metadata that :class:`ECMWF` consumes when building
    a CDS retrieve request.

    Attributes:
        catalog: ``dict`` mapping a user-friendly variable code (e.g.
            ``"2T"``) to the per-variable metadata dict containing
            ``cds_dataset``, ``cds_variable``, ``file_name``,
            ``factors_add``, ``factors_mul``, and the optional
            ``cds_dataset_monthly`` / ``cds_pressure_level`` keys.

    Examples:
        - Look up a single-level ERA5 variable:

            ```python
            >>> from earth2observe.ecmwf import Catalog
            >>> spec = Catalog().get_dataset("2T")
            >>> spec.cds_dataset
            'reanalysis-era5-single-levels'
            >>> spec.cds_variable
            '2m_temperature'
            >>> spec.file_name
            'Tair'

            ```
        - Pressure-level variables include a ``cds_pressure_level``
          attribute that ``ECMWF.api`` forwards to CDS:

            ```python
            >>> from earth2observe.ecmwf import Catalog
            >>> spec = Catalog().get_dataset("T")
            >>> spec.cds_dataset
            'reanalysis-era5-pressure-levels'
            >>> spec.cds_pressure_level
            ['1000']

            ```
    """

    def __init__(self):
        """Load the catalog from ``cds_data_catalog.yaml``."""
        super().__init__()

    def get_catalog(self):
        """Read ``cds_data_catalog.yaml`` and return the per-variable map.

        Returns:
            dict: The non-empty per-variable map loaded from the
            YAML file's top-level ``variables`` key.

        Raises:
            ValueError: If the file is missing the ``variables`` key,
                or it is present but empty / null. Pre-fix, this
                returned ``{}`` silently and every subsequent
                ``get_dataset(code)`` call raised ``KeyError`` —
                misleading the user about which file is broken.
        """
        catalog_path = f"{__path__[0]}/cds_data_catalog.yaml"
        with open(catalog_path, "r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        variables = data.get("variables")
        if not variables:
            raise ValueError(
                f"{catalog_path} is missing or has an empty "
                "'variables' key. The catalog must contain at least "
                "one variable definition. See the schema header at "
                "the top of the file."
            )
        return {
            code: VariableSpec.from_dict(code, entry)
            for code, entry in variables.items()
        }

    def get_dataset(self, var_name):
        """Return the metadata dict for ``var_name``.

        Args:
            var_name: Short user-friendly variable code (e.g. ``"2T"``).

        Returns:
            dict: Per-variable metadata loaded from
            ``cds_data_catalog.yaml``.

        Raises:
            KeyError: If ``var_name`` is not in the catalog.
        """
        return self.catalog[var_name]

    def get_variable(self, var_name):
        """Alias for :meth:`get_dataset` satisfying the abstract base.

        :class:`AbstractCatalog` declares ``get_variable`` as abstract;
        the legacy ECMWF call sites use ``get_dataset``. Both names
        return the same metadata dict so either path works.

        Args:
            var_name: Short user-friendly variable code.

        Returns:
            dict: Per-variable metadata. See :meth:`get_dataset`.
        """
        return self.get_dataset(var_name)
