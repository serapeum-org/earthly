from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemporalExtent(BaseModel):
    """Per-instance temporal context produced by :meth:`check_input_dates`.

    Replaces the ``self.time`` dict that earlier versions of
    :class:`AbstractDataSource` accepted from subclass overrides. The
    frozen pydantic model enforces presence of every consumer-visible
    field at construction time, so a subclass that returns a malformed
    container fails fast instead of surfacing as ``KeyError`` deep
    inside the download loop.

    Attributes:
        start_date: Inclusive start of the requested window. Typed
            :data:`~typing.Any` because pandas / numpy timestamp types
            are not native pydantic primitives; the cross-field
            validator below enforces ``start_date <= end_date`` for
            anything that supports comparison.
        end_date: Inclusive end of the requested window.
        resolution: Spacing between consecutive entries in
            :attr:`dates`, expressed as a pandas frequency alias —
            ``"D"`` for daily, ``"MS"`` for month-start. Same
            shorthand pandas uses for ``date_range(freq=...)``.
        dates: The :class:`pandas.DatetimeIndex` the download loop
            iterates. Typed :data:`~typing.Any` to avoid a hard
            pandas import in the abstract module.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    start_date: Any
    end_date: Any
    resolution: str
    dates: Any

    @model_validator(mode="after")
    def _check_start_le_end(self) -> TemporalExtent:
        """Validate that ``start_date <= end_date``.

        Raises:
            ValueError: If the window is inverted.
        """
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError(
                    f"TemporalExtent has inverted bounds: start_date "
                    f"{self.start_date} > end_date {self.end_date}"
                )
        return self


class SpatialExtent(BaseModel):
    """Geographic bounding box (WGS84) for a download request.

    Backend-agnostic. Coordinates are in **degrees**:

    * latitude in ``[-90, 90]`` (south negative, north positive)
    * longitude in ``[-180, 180]`` (west negative, east positive)

    Each concrete data source converts this to whatever format its
    protocol expects (CDS: ``[north, west, south, east]``; CHIRPS:
    per-row clipping; S3: prefix filter; GEE:
    ``ee.Geometry.Rectangle(west, south, east, north)``). For
    projected coordinates, define a separate ``ProjectedExtent``
    type — do not reuse this one with metric values.

    Attributes:
        latitude_min: Inclusive south edge of the bbox, in degrees.
        latitude_max: Inclusive north edge of the bbox, in degrees.
        longitude_min: Inclusive west edge of the bbox, in degrees.
        longitude_max: Inclusive east edge of the bbox, in degrees.
        resolution: Grid cell size in degrees, applied to both
            latitude and longitude. ``None`` for backends that work
            on irregular grids or do not need a cell size for their
            request shape (e.g. CHIRPS FTP file lookup, S3 prefix
            listing). Mirrors :attr:`TemporalExtent.resolution` —
            the spatial counterpart of the temporal cadence.
    """

    model_config = ConfigDict(frozen=True)

    latitude_min: float = Field(
        ge=-90.0, le=90.0, description="South edge in degrees"
    )
    latitude_max: float = Field(
        ge=-90.0, le=90.0, description="North edge in degrees"
    )
    longitude_min: float = Field(
        ge=-180.0, le=180.0, description="West edge in degrees"
    )
    longitude_max: float = Field(
        ge=-180.0, le=180.0, description="East edge in degrees"
    )
    resolution: float | None = Field(
        default=None, gt=0.0, description="Grid cell size in degrees"
    )

    @model_validator(mode="after")
    def _check_min_le_max(self) -> SpatialExtent:
        """Validate that ``min <= max`` on both axes.

        Per-field range constraints (``Field(ge=..., le=...)``) cannot
        express the cross-field invariant.

        Raises:
            ValueError: If either ``latitude_min > latitude_max`` or
                ``longitude_min > longitude_max``.
        """
        if self.latitude_min > self.latitude_max:
            raise ValueError(
                f"latitude_min ({self.latitude_min}) > "
                f"latitude_max ({self.latitude_max})"
            )
        if self.longitude_min > self.longitude_max:
            raise ValueError(
                f"longitude_min ({self.longitude_min}) > "
                f"longitude_max ({self.longitude_max})"
            )
        return self

    @classmethod
    def from_pairs(
        cls,
        lat_lim: list[float],
        lon_lim: list[float],
        resolution: float | None = None,
    ) -> SpatialExtent:
        """Build from the legacy ``[min, max]`` pair shape.

        :class:`AbstractDataSource.__init__` accepts ``lat_lim`` /
        ``lon_lim`` as constructor kwargs in the public API; this
        classmethod adapts that shape to the four named fields.

        Args:
            lat_lim: ``[lat_min, lat_max]`` in degrees.
            lon_lim: ``[lon_min, lon_max]`` in degrees.
            resolution: Grid cell size in degrees. Defaults to
                ``None`` (unspecified — typical for backends that
                work off file listings rather than gridded request
                shapes).

        Returns:
            SpatialExtent: A validated, frozen instance.
        """
        return cls(
            latitude_min=lat_lim[0],
            latitude_max=lat_lim[1],
            longitude_min=lon_lim[0],
            longitude_max=lon_lim[1],
            resolution=resolution,
        )

    @property
    def north(self) -> float:
        """Northern edge of the bbox (== ``latitude_max``)."""
        return self.latitude_max

    @property
    def south(self) -> float:
        """Southern edge of the bbox (== ``latitude_min``)."""
        return self.latitude_min

    @property
    def east(self) -> float:
        """Eastern edge of the bbox (== ``longitude_max``)."""
        return self.longitude_max

    @property
    def west(self) -> float:
        """Western edge of the bbox (== ``longitude_min``)."""
        return self.longitude_min


class AbstractDataSource(ABC):
    """Bluebrint for all class for different datasources."""

    def __init__(
        self,
        start: str = None,
        end: str = None,
        variables: list = None,
        temporal_resolution: str = "daily",
        lat_lim: list = None,
        lon_lim: list = None,
        fmt: str = "%Y-%m-%d",
        path: str = "",
    ):
        """Initialize a data source instance.

        Captures the return values of the abstract hooks so subclasses
        do not have to wire them onto ``self`` themselves:

        * ``self.client`` — whatever :meth:`initialize` returns (a CDS
          client, an S3 client, ``None`` for FTP). Subclasses that
          assign ``self.client`` inside :meth:`initialize` (e.g.
          :class:`S3`) keep their own assignment; the parent only sets
          the attribute when :meth:`initialize` returns a non-``None``
          value.
        * ``self.space`` — the dict returned by :meth:`create_grid`,
          containing ``lat_lim`` and ``lon_lim``. Subclasses that
          override :meth:`create_grid` to set attributes directly (e.g.
          :class:`CHIRPS`) and return ``None`` are unaffected.
        * ``self.time`` — the dict returned by :meth:`check_input_dates`,
          containing ``start_date``, ``end_date``, ``time_freq`` and
          ``dates``. Same opt-in semantics as ``self.space``.
        * ``self.root_dir`` — the absolute :class:`pathlib.Path` of the
          output directory. ``self.path`` is kept as a legacy alias so
          older backends (CHIRPS, S3) continue to work.

        Args:
            start: Inclusive start date as a string. Format controlled
                by ``fmt``. Defaults to ``None``.
            end: Inclusive end date as a string. Defaults to ``None``.
            variables: List of variable short codes to download.
            temporal_resolution: ``"daily"`` or ``"monthly"``. Defaults
                to ``"daily"``.
            lat_lim: ``[lat_min, lat_max]``.
            lon_lim: ``[lon_min, lon_max]``.
            fmt: ``strptime`` format for ``start`` / ``end``. Defaults
                to ``"%Y-%m-%d"``.
            path: Output directory. Created if it does not exist.
                Defaults to the current working directory.
        """
        client = self.initialize()
        if client is not None:
            self.client = client

        self.temporal_resolution = temporal_resolution
        self.vars = variables

        space = self.create_grid(lat_lim, lon_lim)
        if isinstance(space, SpatialExtent):
            self.space = space
        elif isinstance(space, dict):
            self.space = SpatialExtent.from_pairs(
                lat_lim=space["lat_lim"], lon_lim=space["lon_lim"]
            )

        time = self.check_input_dates(start, end, temporal_resolution, fmt)
        if isinstance(time, TemporalExtent):
            self.time = time
        elif isinstance(time, dict):
            self.time = TemporalExtent(
                start_date=time["start_date"],
                end_date=time["end_date"],
                resolution=time.get("resolution", time.get("time_freq")),
                dates=time["dates"],
            )

        self.root_dir = Path(path).absolute()
        self.path = self.root_dir
        if not os.path.exists(self.root_dir):
            os.makedirs(self.root_dir)

    @abstractmethod
    def check_input_dates(
        self, start: str, end: str, temporal_resolution: str, fmt: str
    ):
        """Check validity of input dates."""
        pass

    @abstractmethod
    def initialize(self, *args, **kwargs):
        """Initialize connection with the data source server (for non ftp servers)"""
        pass

    @abstractmethod
    def create_grid(self, lat_lim: list, lon_lim: list):
        """create a grid from the lat/lon boundaries."""
        pass

    @abstractmethod
    def download(self):
        """Wrapper over all the given variables."""
        # loop over dates if the downloaded rasters/netcdf are for a specific date out of the required
        # list of dates
        pass

    # @abstractmethod
    def downloadDataset(self):
        """Download single variable/dataset."""
        # used for non ftp servers
        pass

    @abstractmethod
    def API(self):
        """send/recieve request to the dataset server."""
        pass


class AbstractCatalog(ABC):
    """Abstract base class for per-data-source variable catalogs.

    Subclasses load a backend-specific catalog (a YAML file, an
    in-code dict, or a remote query) in :meth:`get_catalog` and
    expose individual entries via :meth:`get_variable`. The constructor
    eagerly loads the catalog into :attr:`catalog` so subclasses can
    treat it as a dict thereafter.

    Attributes:
        catalog: The full catalog mapping returned by
            :meth:`get_catalog`. Type and shape are backend-specific.
    """

    def __init__(self):
        self.catalog = self.get_catalog()

    @abstractmethod
    def get_catalog(self):
        """read the catalog of the datasource from disk or retrieve it from server."""
        pass

    @abstractmethod
    def get_variable(self, var_name) -> Dict[str, str]:
        """get the details of a specific variable."""
        return self.catalog.get(var_name)
