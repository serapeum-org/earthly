"""Front-end facade that routes downloads to a concrete data-source backend.

The :class:`EarthLens` class is the user-facing entry point of the
package. It keeps the choice of backend (CHIRPS, ERA5 on AWS S3, ECMWF
on the Copernicus Climate Data Store, Google Earth Engine) behind a
single string key so callers do not have to import each backend module
directly.

Each backend's runtime SDK is an optional dependency
(`pip install earthlens[ecmwf]`, `[s3]`, `[gee]`); the registry below
imports the backend module on first dispatch and rewrites a missing
SDK into a friendly `ImportError` naming the extra to install.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from earthlens.aggregate import AggregationConfig
    from earthlens.base import AbstractDataSource


#: Default longitude bounds used when `lon_lim` is not supplied
#: (whole-Earth coverage).
DEFAULT_LONGITUDE_LIMIT = [-180, 180]

#: Default latitude bounds used when `lat_lim` is not supplied
#: (whole-Earth coverage).
DEFAULT_LATITUDE_LIMIT = [-90, 90]


class _LazyRegistry(Mapping):
    """Maps a data-source key to its backend class, importing on demand.

    A read-only :class:`collections.abc.Mapping` over the registered
    backend keys: containment, iteration, `len()`, `.keys()` /
    `.values()` / `.items()` all work. The value is resolved on
    `__getitem__`, so backends whose optional SDK is not installed do
    not crash at package import time — a missing SDK surfaces as an
    `ImportError` naming the extra to install.

    Attributes:
        _mapping: Internal `key -> (module, class_name, extras_hint)`
            table populated at construction.
    """

    def __init__(self, mapping: dict[str, tuple[str, str, str]]) -> None:
        self._mapping = mapping

    def __contains__(self, key: object) -> bool:
        return key in self._mapping

    def __iter__(self):
        return iter(self._mapping)

    def __len__(self) -> int:
        return len(self._mapping)

    def __getitem__(self, key: str) -> type[AbstractDataSource]:
        module_name, class_name, extras = self._mapping[key]
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            hint = f" Install with `pip install earthlens[{extras}]`." if extras else ""
            raise ImportError(
                f"Backend {key!r} is unavailable — its runtime "
                f"dependency is not installed.{hint}"
            ) from exc
        return getattr(module, class_name)


class EarthLens:
    """Facade that routes a download to the requested backend.

    The class-level :attr:`DataSources` mapping resolves a string key
    (`"chc"`, `"amazon-s3"`, `"ecmwf"`, or `"gee"` / its alias
    `"google-earth-engine"`) to the concrete
    :class:`AbstractDataSource` subclass that owns the request shape,
    authentication, and post-processing for that provider. Each
    backend's SDK is an optional dependency, so :attr:`DataSources`
    is a :class:`_LazyRegistry`: indexing it imports the backend on
    demand and rewrites a missing SDK into a friendly
    `ImportError` naming the extra to install
    (e.g. `pip install earthlens[ecmwf]`).

    Attributes:
        DataSources: Class-level lazy registry of registered backends.
            Keys are the user-facing names accepted by `data_source`;
            values resolve at access time to the corresponding
            subclasses of
            :class:`earthlens.base.AbstractDataSource`.
        datasource: Instance attribute set by :meth:`__init__` —
            holds the concrete backend that :meth:`download` routes to.

    Examples:
        - Inspect the registered backends:

            ```python
            >>> from earthlens.earthlens import EarthLens
            >>> sorted(EarthLens.DataSources)
            ['amazon-s3', 'chc', 'chirps', 'ecmwf', 'gee', 'google-earth-engine']

            ```
        - Asking for an unknown backend raises `ValueError`:

            ```python
            >>> from earthlens.earthlens import EarthLens
            >>> EarthLens(variables=[], data_source="not-a-real-source")
            Traceback (most recent call last):
                ...
            ValueError: not-a-real-source not supported

            ```

    See Also:
        :class:`earthlens.chc.CHIRPS`: CHIRPS rainfall over FTP.
        :class:`earthlens.s3.S3`: ERA5 on AWS public S3 bucket.
        :class:`earthlens.ecmwf.ECMWF`: ERA5 via the Copernicus
            Climate Data Store (cdsapi).
        :class:`earthlens.gee.GEE`: imagery from Google Earth Engine
            (`earthengine-api`); keys `"gee"` / `"google-earth-engine"`.
    """

    DataSources = _LazyRegistry(
        {
            "chc": ("earthlens.chc", "CHIRPS", ""),
            # Back-compat alias: the package was originally named after
            # its best-known dataset (CHIRPS), then generalised to cover
            # the full Climate Hazards Center catalog. The `"chirps"`
            # key is kept for callers that still use it.
            "chirps": ("earthlens.chc", "CHIRPS", ""),
            "amazon-s3": ("earthlens.s3", "S3", "s3"),
            "ecmwf": ("earthlens.ecmwf", "ECMWF", "ecmwf"),
            "gee": ("earthlens.gee", "GEE", "gee"),
            "google-earth-engine": ("earthlens.gee", "GEE", "gee"),
        }
    )

    def __init__(
        self,
        variables: dict[str, list[str]] | list[str],
        data_source: str = "chc",
        temporal_resolution: str = "daily",
        start: str | None = None,
        end: str | None = None,
        path: Path | str = "",
        lat_lim: list[float] | None = None,
        lon_lim: list[float] | None = None,
        fmt: str = "%Y-%m-%d",
        **backend_kwargs: object,
    ):
        """Resolve the backend and construct it with the user's parameters.

        Validates `data_source` against :attr:`DataSources`, fills in
        whole-Earth defaults for missing `lat_lim` / `lon_lim`, and
        instantiates the concrete backend bound to `self.datasource`.

        Args:
            data_source: Backend key — one of `"chc"` (alias
                `"chirps"`), `"amazon-s3"`, `"ecmwf"`, or `"gee"`
                (alias `"google-earth-engine"`). Defaults to
                `"chc"`.
            temporal_resolution: `"daily"` or `"monthly"` for most
                backends; the GEE backend also accepts `"raw"` and
                `"yearly"`. The concrete backend may accept a narrower
                set; check its `temporal_resolution` handling.
                Defaults to `"daily"`.
            start: Inclusive start date as a string (parsed with
                `fmt`). Defaults to `None`.
            end: Inclusive end date as a string. Defaults to `None`.
            path: Output directory. Created by the backend if it does
                not exist. Defaults to the current working directory.
            variables: Backend-specific variable specification.
                Shape depends on the backend:

                * ECMWF: `dict[str, list[str]]` mapping CDS dataset
                  short name to a list of variable codes drawn from
                  that dataset, e.g.
                  `{"reanalysis-era5-single-levels": ["2m-temperature"]}`.
                * GEE: `dict[str, list[str]]` mapping an Earth Engine
                  asset id to a list of band ids, e.g.
                  `{"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]}`.
                * CHIRPS: either `list[str]` of variable codes
                  (legacy — auto-routed to the `"global-daily"` /
                  `"global-monthly"` dataset key via
                  `temporal_resolution`), or `dict[str, list[str]]`
                  mapping a CHIRPS catalog dataset key (e.g.
                  `"africa-pentad"`, `"chirps-v3-global-monthly"`)
                  to a list of variable codes drawn from that
                  dataset, e.g. `{"africa-monthly": ["precipitation"]}`.
                  See `Catalog().list_datasets()` for the curated
                  dataset keys.
                * S3 / ERA5: `list[str]` of variable codes from the
                  S3 backend's catalog.

                Defaults to `None`.
            lat_lim: `[lat_min, lat_max]`. Defaults to
                :data:`DEFAULT_LATITUDE_LIMIT` (whole Earth).
            lon_lim: `[lon_min, lon_max]`. Defaults to
                :data:`DEFAULT_LONGITUDE_LIMIT` (whole Earth).
            fmt: `strptime` format for `start` and `end`.
                Defaults to `"%Y-%m-%d"`.
            **backend_kwargs: Extra keyword arguments forwarded
                verbatim to the chosen backend's constructor — for
                backend-specific options the facade does not name
                explicitly (e.g. ECMWF's `skip_constraints`, or GEE's
                `service_account` / `service_key` / `project` / `scale` /
                `crs` / `reducer` / `export_via` / `drive_folder` /
                `gcs_bucket` / `region`). A kwarg the backend does not
                accept is its `TypeError`, not the facade's.

        Raises:
            ValueError: If `data_source` is not a key of
                :attr:`DataSources`.
            AuthenticationError: If the backend cannot authenticate —
                ECMWF (missing `~/.cdsapirc`; see
                :class:`earthlens.ecmwf.AuthenticationError`) or GEE
                (missing/invalid service key, unregistered project; see
                :class:`earthlens.gee.AuthenticationError`).
            ImportError: If the chosen backend's optional SDK is not
                installed (e.g. `data_source="gee"` without
                `pip install earthlens[gee]`).

        Examples:
            - The DataSources registry resolves the backend class
              before construction. Inspect what each key points to:

                ```python
                >>> from earthlens.earthlens import EarthLens
                >>> EarthLens.DataSources["chc"].__name__
                'CHIRPS'
                >>> EarthLens.DataSources["chirps"].__name__  # alias
                'CHIRPS'
                >>> EarthLens.DataSources["ecmwf"].__name__
                'ECMWF'
                >>> EarthLens.DataSources["gee"].__name__
                'GEE'

                ```
            - An unknown `data_source` is rejected before any backend
              code runs:

                ```python
                >>> from earthlens.earthlens import EarthLens
                >>> EarthLens(variables=[], data_source="bogus")
                Traceback (most recent call last):
                    ...
                ValueError: bogus not supported

                ```
            - Construct an ECMWF-backed facade. Marked
              `# doctest: +SKIP` because it builds a real
              :class:`cdsapi.Client`, which requires
              `~/.cdsapirc`:

                ```python
                >>> from earthlens.earthlens import EarthLens
                >>> earthlens = EarthLens(  # doctest: +SKIP
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

                ```

        See Also:
            :meth:`download`: Triggers the actual retrieval.
        """
        if data_source not in self.DataSources:
            raise ValueError(f"{data_source} not supported")

        if lat_lim is None:
            lat_lim = DEFAULT_LATITUDE_LIMIT
        if lon_lim is None:
            lon_lim = DEFAULT_LONGITUDE_LIMIT

        self.datasource = self.DataSources[data_source](
            start=start,
            end=end,
            variables=variables,
            lat_lim=lat_lim,
            lon_lim=lon_lim,
            temporal_resolution=temporal_resolution,
            path=path,
            fmt=fmt,
            **backend_kwargs,
        )

    def download(
        self,
        progress_bar: bool = True,
        aggregate: AggregationConfig | None = None,
        *args: object,
        **kwargs: object,
    ) -> Any:
        """Delegate the download to the bound backend.

        Forwards every argument verbatim to `self.datasource.download`.
        Each backend's `download` accepts its own backend-specific
        keyword arguments (for example, CHIRPS supports `cores` for
        parallel FTP retrieval), so unrecognised kwargs propagate
        through.

        Args:
            progress_bar: Whether the backend should print a per-date
                progress bar during the loop. Defaults to `True`.
            aggregate: Optional :class:`earthlens.aggregate.AggregationConfig`.
                Forwarded to backends that support it (currently
                ECMWF). CHIRPS / S3 accept `**kwargs` and ignore an
                unused `aggregate` payload, so passing it there is a
                no-op; the GEE backend explicitly rejects a non-`None`
                `aggregate` with `NotImplementedError` (planned — see
                the GEE plan task M3).
            *args: Forwarded positionally to `backend.download`.
            **kwargs: Forwarded as keywords to `backend.download`.

        Returns:
            Whatever the bound backend's `download` returns: `None` for
            CHIRPS / S3 / ECMWF (they write files to `path` as a side
            effect), or the list of written GeoTIFF paths / export
            destination strings for the GEE backend.

        Raises:
            AuthenticationError: When the ECMWF backend cannot
                authenticate against CDS (typically a missing
                `~/.cdsapirc`). See
                :class:`earthlens.ecmwf.AuthenticationError`.
            KeyError: When any backend receives an unknown variable
                code that the catalog cannot resolve.

        Examples:
            - End-to-end CHIRPS download. Marked `# doctest: +SKIP`
              because it makes a live FTP connection:

                ```python
                >>> from earthlens.earthlens import EarthLens
                >>> earthlens = EarthLens(  # doctest: +SKIP
                ...     data_source="chc",
                ...     start="2009-01-01",
                ...     end="2009-01-02",
                ...     variables=["precipitation"],
                ...     lat_lim=[4.19, 4.64],
                ...     lon_lim=[-75.65, -74.73],
                ...     path="examples/data/chirps",
                ... )
                >>> earthlens.download()  # doctest: +SKIP

                ```
            - ECMWF download via the facade. Marked
              `# doctest: +SKIP` because CDS requires
              `~/.cdsapirc` and the request blocks for minutes
              while the queue serves it:

                ```python
                >>> from earthlens.earthlens import EarthLens
                >>> earthlens = EarthLens(  # doctest: +SKIP
                ...     data_source="ecmwf",
                ...     start="2022-01-01",
                ...     end="2022-01-01",
                ...     variables={
                ...         "reanalysis-era5-single-levels": ["2m-temperature"],
                ...     },
                ...     lat_lim=[4.0, 5.0],
                ...     lon_lim=[-75.0, -74.0],
                ...     path="examples/data/era5",
                ... )
                >>> earthlens.download()  # doctest: +SKIP

                ```

        See Also:
            :meth:`earthlens.chc.CHIRPS.download`: CHIRPS
                backend implementation, including the `cores=`
                keyword for parallel retrieval.
            :meth:`earthlens.s3.S3.download`: S3/ERA5 backend
                implementation.
            :meth:`earthlens.ecmwf.ECMWF.download`: ECMWF/CDS
                backend implementation.
            :meth:`earthlens.gee.GEE.download`: Google Earth Engine
                backend implementation (`export_via`, the 32768-px
                synchronous cap).
        """
        if aggregate is not None:
            kwargs["aggregate"] = aggregate
        return self.datasource.download(progress_bar=progress_bar, *args, **kwargs)
