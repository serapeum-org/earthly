import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict


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
        if isinstance(space, dict):
            self.space = space

        time = self.check_input_dates(start, end, temporal_resolution, fmt)
        if isinstance(time, dict):
            self.time = time

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
    """abstrach class for the datasource catalog."""

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
