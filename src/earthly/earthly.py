"""Front-end facade that routes downloads to a concrete data-source backend.

The :class:`Earthly` class is the user-facing entry point of the
package. It keeps the choice of backend (CHIRPS, ERA5 on AWS S3, ECMWF
on the Copernicus Climate Data Store) behind a single string key so
callers do not have to import each backend module directly.

Each backend's runtime SDK is an optional dependency
(`pip install earthly[ecmwf]`, `[s3]`, `[gee]`); the registry below
imports the backend module on first dispatch and rewrites a missing
SDK into a friendly `ImportError` naming the extra to install.
"""
from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from earthly.base import AbstractDataSource


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
            hint = (
                f" Install with `pip install earthly[{extras}]`."
                if extras
                else ""
            )
            raise ImportError(
                f"Backend {key!r} is unavailable — its runtime "
                f"dependency is not installed.{hint}"
            ) from exc
        return getattr(module, class_name)

#: Default longitude bounds used when `lon_lim` is not supplied
#: (whole-Earth coverage).
DEFAULT_LONGITUDE_LIMIT = [-180, 180]

#: Default latitude bounds used when `lat_lim` is not supplied
#: (whole-Earth coverage).
DEFAULT_LATITUDE_LIMIT = [-90, 90]


class Earthly:
    """Facade that routes a download to the requested backend.

    The class-level :attr:`DataSources` mapping resolves a string key
    (`"chirps"`, `"amazon-s3"`, or `"ecmwf"`) to the concrete
    :class:`AbstractDataSource` subclass that owns the request shape,
    authentication, and post-processing for that provider. Each
    backend's SDK is an optional dependency, so :attr:`DataSources`
    is a :class:`_LazyRegistry`: indexing it imports the backend on
    demand and rewrites a missing SDK into a friendly
    `ImportError` naming the extra to install
    (e.g. `pip install earthly[ecmwf]`).

    Attributes:
        DataSources: Class-level lazy registry of registered backends.
            Keys are the user-facing names accepted by `data_source`;
            values resolve at access time to the corresponding
            subclasses of
            :class:`earthly.base.AbstractDataSource`.
        datasource: Instance attribute set by :meth:`__init__` —
            holds the concrete backend that :meth:`download` routes to.

    Examples:
        - Inspect the registered backends:

            ```python
            >>> from earthly.earthly import Earthly
            >>> sorted(Earthly.DataSources)
            ['amazon-s3', 'chirps', 'ecmwf']

            ```
        - Asking for an unknown backend raises `ValueError`:

            ```python
            >>> from earthly.earthly import Earthly
            >>> Earthly(data_source="not-a-real-source")
            Traceback (most recent call last):
                ...
            ValueError: not-a-real-source not supported

            ```

    See Also:
        :class:`earthly.chirps.CHIRPS`: CHIRPS rainfall over FTP.
        :class:`earthly.s3.S3`: ERA5 on AWS public S3 bucket.
        :class:`earthly.ecmwf.ECMWF`: ERA5 via the Copernicus
            Climate Data Store (cdsapi).
    """

    DataSources = _LazyRegistry(
        {
            "chirps": ("earthly.chirps", "CHIRPS", ""),
            "amazon-s3": ("earthly.s3", "S3", "s3"),
            "ecmwf": ("earthly.ecmwf", "ECMWF", "ecmwf"),
        }
    )

    def __init__(
        self,
        data_source: str = "chirps",
        temporal_resolution: str = "daily",
        start: str = None,
        end: str = None,
        path: Path = None,
        variables=None,
        lat_lim: list = None,
        lon_lim: list = None,
        fmt: str = "%Y-%m-%d",
    ):
        """Resolve the backend and construct it with the user's parameters.

        Validates `data_source` against :attr:`DataSources`, fills in
        whole-Earth defaults for missing `lat_lim` / `lon_lim`, and
        instantiates the concrete backend bound to `self.datasource`.

        Args:
            data_source: Backend key — one of `"chirps"`,
                `"amazon-s3"`, or `"ecmwf"`. Defaults to
                `"chirps"`.
            temporal_resolution: `"daily"` or `"monthly"`. The
                concrete backend may accept a narrower set; check its
                `temporal_resolution` class attribute. Defaults to
                `"daily"`.
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
                * CHIRPS: `list[str]` of variable codes
                  (e.g. `["precipitation"]`).
                * S3 / ERA5: `list[str]` of variable codes from the
                  S3 backend's catalog.

                Defaults to `None`.
            lat_lim: `[lat_min, lat_max]`. Defaults to
                :data:`DEFAULT_LATITUDE_LIMIT` (whole Earth).
            lon_lim: `[lon_min, lon_max]`. Defaults to
                :data:`DEFAULT_LONGITUDE_LIMIT` (whole Earth).
            fmt: `strptime` format for `start` and `end`.
                Defaults to `"%Y-%m-%d"`.

        Raises:
            ValueError: If `data_source` is not a key of
                :attr:`DataSources`.
            AuthenticationError: If `data_source="ecmwf"` and cdsapi
                cannot authenticate (typically a missing
                `~/.cdsapirc`). See
                :class:`earthly.ecmwf.AuthenticationError`.

        Examples:
            - The DataSources registry resolves the backend class
              before construction. Inspect what each key points to:

                ```python
                >>> from earthly.earthly import Earthly
                >>> Earthly.DataSources["chirps"].__name__
                'CHIRPS'
                >>> Earthly.DataSources["ecmwf"].__name__
                'ECMWF'

                ```
            - An unknown `data_source` is rejected before any backend
              code runs:

                ```python
                >>> from earthly.earthly import Earthly
                >>> Earthly(data_source="bogus")
                Traceback (most recent call last):
                    ...
                ValueError: bogus not supported

                ```
            - Construct an ECMWF-backed facade. Marked
              `# doctest: +SKIP` because it builds a real
              :class:`cdsapi.Client`, which requires
              `~/.cdsapirc`:

                ```python
                >>> from earthly.earthly import Earthly
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
        )

    def download(self, progress_bar: bool = True, *args, **kwargs):
        """Delegate the download to the bound backend.

        Forwards every argument verbatim to `self.datasource.download`.
        Each backend's `download` accepts its own backend-specific
        keyword arguments (for example, CHIRPS supports `cores` for
        parallel FTP retrieval), so unrecognised kwargs propagate
        through.

        Args:
            progress_bar: Whether the backend should print a per-date
                progress bar during the loop. Defaults to `True`.
            *args: Forwarded positionally to `backend.download`.
            **kwargs: Forwarded as keywords to `backend.download`.

        Returns:
            Whatever the bound backend's `download` method returns.
            All backends currently return `None` and write files to
            `path` as a side effect.

        Raises:
            Any exception the bound backend raises. ECMWF wraps
            authentication failures in
            :class:`earthly.ecmwf.AuthenticationError`; all
            backends propagate `KeyError` for unknown variable codes.

        Examples:
            - End-to-end CHIRPS download. Marked `# doctest: +SKIP`
              because it makes a live FTP connection:

                ```python
                >>> from earthly.earthly import Earthly
                >>> e2o = Earthly(  # doctest: +SKIP
                ...     data_source="chirps",
                ...     start="2009-01-01",
                ...     end="2009-01-02",
                ...     variables=["precipitation"],
                ...     lat_lim=[4.19, 4.64],
                ...     lon_lim=[-75.65, -74.73],
                ...     path="examples/data/chirps",
                ... )
                >>> e2o.download()  # doctest: +SKIP

                ```
            - ECMWF download via the facade. Marked
              `# doctest: +SKIP` because CDS requires
              `~/.cdsapirc` and the request blocks for minutes
              while the queue serves it:

                ```python
                >>> from earthly.earthly import Earthly
                >>> earthly = Earthly(  # doctest: +SKIP
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
                >>> earthly.download()  # doctest: +SKIP

                ```

        See Also:
            :meth:`earthly.chirps.CHIRPS.download`: CHIRPS
                backend implementation, including the `cores=`
                keyword for parallel retrieval.
            :meth:`earthly.s3.S3.download`: S3/ERA5 backend
                implementation.
            :meth:`earthly.ecmwf.ECMWF.download`: ECMWF/CDS
                backend implementation.
        """
        self.datasource.download(progress_bar=progress_bar, *args, **kwargs)
