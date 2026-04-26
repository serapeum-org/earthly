import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class TimeWindow:
    """Per-instance temporal context produced by :meth:`check_input_dates`.

    Replaces the ``self.time`` dict that earlier versions of
    :class:`AbstractDataSource` accepted from subclass overrides. The
    frozen dataclass enforces presence of every consumer-visible
    field at construction time, so a subclass that returns a malformed
    container fails fast instead of surfacing as ``KeyError`` deep
    inside the download loop.

    Attributes:
        start_date: Inclusive start of the requested window.
        end_date: Inclusive end of the requested window.
        time_freq: ``"D"`` for daily, ``"MS"`` for month-start. Same
            shorthand pandas uses for ``date_range(freq=...)``.
        dates: The :class:`pandas.DatetimeIndex` the download loop
            iterates. Typed ``Any`` here to avoid a hard pandas
            import in the abstract module.
    """

    start_date: Any
    end_date: Any
    time_freq: str
    dates: Any

    def __post_init__(self):
        """Validate ``start_date <= end_date`` at construction.

        Raises:
            ValueError: If the window is inverted.
        """
        if self.start_date is not None and self.end_date is not None:
            if self.start_date > self.end_date:
                raise ValueError(
                    f"TimeWindow has inverted bounds: start_date "
                    f"{self.start_date} > end_date {self.end_date}"
                )


@dataclass(frozen=True)
class SpatialBounds:
    """Per-instance spatial bbox produced by :meth:`create_grid`.

    Replaces the ``self.space`` dict. The two limit pairs are stored
    as ``[min, max]`` lists so existing call sites that read
    ``self.space["lat_lim"][1]`` (the latitude max) keep working when
    they switch to attribute access (``self.space.lat_lim[1]``).

    Attributes:
        lat_lim: ``[lat_min, lat_max]`` in degrees.
        lon_lim: ``[lon_min, lon_max]`` in degrees.
    """

    lat_lim: List[float]
    lon_lim: List[float]

    def __post_init__(self):
        """Validate ``min <= max`` for both axes.

        Raises:
            ValueError: If either limit pair is inverted.
        """
        if self.lat_lim[0] > self.lat_lim[1]:
            raise ValueError(
                f"SpatialBounds.lat_lim is inverted: {self.lat_lim}"
            )
        if self.lon_lim[0] > self.lon_lim[1]:
            raise ValueError(
                f"SpatialBounds.lon_lim is inverted: {self.lon_lim}"
            )


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
        if isinstance(space, SpatialBounds):
            self.space = space
        elif isinstance(space, dict):
            self.space = SpatialBounds(
                lat_lim=space["lat_lim"], lon_lim=space["lon_lim"]
            )

        time = self.check_input_dates(start, end, temporal_resolution, fmt)
        if isinstance(time, TimeWindow):
            self.time = time
        elif isinstance(time, dict):
            self.time = TimeWindow(
                start_date=time["start_date"],
                end_date=time["end_date"],
                time_freq=time["time_freq"],
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
