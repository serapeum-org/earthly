import calendar
import datetime as dt
import os
from typing import Dict

import numpy as np
import pandas as pd
import yaml
import cdsapi
from loguru import logger
from netCDF4 import Dataset
from serapeum_utils.utils import print_progress_bar

from earth2observe import __path__
from earth2observe.abstractdatasource import AbstractCatalog, AbstractDataSource


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


class ECMWF(AbstractDataSource):
    """RemoteSensing.

    RemoteSensing class contains methods to download ECMWF data
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
        """ECMWF.

        Parameters
        ----------
        temporal_resolution (str, optional):
            [description]. Defaults to 'daily'.
        start (str, optional):
            [description]. Defaults to ''.
        end (str, optional):
            [description]. Defaults to ''.
        path (str, optional):
            Path where you want to save the downloaded data. Defaults to ''.
        variables (list, optional):
            Variable code: VariablesInfo('day').descriptions.keys(). Defaults to [].
        lat_lim (list, optional):
            [ymin, ymax]. Defaults to None.
        lon_lim (list, optional):
            [xmin, xmax]. Defaults to None.
        fmt (str, optional):
            [description]. Defaults to "%Y-%m-%d".
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
        """check validity of input dates.

        Parameters
        ----------
        temporal_resolution: (str, optional)
            [description]. Defaults to 'daily'.
        start: (str, optional)
            [description]. Defaults to ''.
        end: (str, optional)
            [description]. Defaults to ''.
        fmt: (str, optional)
            [description]. Defaults to "%Y-%m-%d".
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

        return {"start_date": start, "end_date": end, "time_freq": time_freq, "dates": dates}

    def initialize(self):
        """Initialize connection with ECMWF server."""
        try:
            # url = os.environ["ECMWF_API_URL"]
            # key = os.environ["ECMWF_API_KEY"]
            # email = os.environ["ECMWF_API_EMAIL"]
            client = cdsapi.Client()
        except KeyError:
            raise AuthenticationError(
                "Please define the following environment variables to successfully establish a "
                "connection with ecmwf server ECMWF_API_URL, ECMWF_API_KEY, ECMWF_API_EMAIL"
            )

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
        return {"lat_lim": lat_lim, "lon_lim": lon_lim}

    def download(
        self, dataset: str = "interim", progress_bar: bool = True, *args, **kwargs
    ):
        """Download wrapper over all given variables.

        ECMWF method downloads ECMWF daily data for a given variable, temporal_resolution
        interval, and spatial extent.


        Parameters
        ----------
        progress_bar : TYPE, optional
            0 or 1. to display the progress bar
        dataset:[str]
            Default is "interim"

        Returns
        -------
        None.
        """
        # read the datasource catalog
        catalog = Catalog()

        for var in self.variables:
            # Download data
            logger.info(
                f"Download ECMWF {var} data for period {self.time["start_date"]} till {self.time["end_date"]}"
            )
            var_info = catalog.get_dataset(var)
            self.download_dataset(var_info, dataset=dataset, progress_bar=progress_bar)
        # delete the downloaded netcdf
        del_ecmwf_dataset = os.path.join(self.path, "data_interim.nc")
        os.remove(del_ecmwf_dataset)

    def download_dataset(
        self,
        var_info: Dict[str, str],
        dataset: str = "interim",
        progress_bar: bool = True,
    ):
        """Download and post-process a single climate variable.

        Calls :meth:`api` to fetch the raw NetCDF from CDS, then
        :meth:`post_download` to slice it into per-date GeoTIFFs in
        ``self.path``.

        Args:
            var_info: Variable metadata pulled from ``cds_data_catalog.yaml``
                via :class:`Catalog`. See :meth:`api` for the required keys.
            dataset: Legacy dataset selector retained for the post-download
                stage only. The CDS dataset name itself is now derived from
                ``var_info['cds_dataset']`` and not from this argument.
                Defaults to ``"interim"``.
            progress_bar: Whether :meth:`post_download` should print a
                progress bar during the per-date loop. Defaults to ``True``.

        Returns:
            None.

        Raises:
            KeyError: If ``var_info`` is missing one of the keys required by
                :meth:`api` (``cds_dataset``, ``cds_variable``, or
                ``file_name``).

        Examples:
            - The catalog ships ``var_info`` dicts ready for this method;
              inspect one to see the required shape:

                ```python
                >>> var_info = {
                ...     "cds_dataset": "reanalysis-era5-single-levels",
                ...     "cds_variable": "2m_temperature",
                ...     "file_name": "Tair",
                ...     "factors_add": -273.15,
                ...     "factors_mul": 1,
                ... }
                >>> var_info["cds_variable"]
                '2m_temperature'
                >>> sorted(var_info)[:3]
                ['cds_dataset', 'cds_variable', 'factors_add']

                ```
            - Download via the user-facing :class:`Earth2Observe` facade
              (recommended). Marked ``# doctest: +SKIP`` because it
              requires a configured ``~/.cdsapirc`` and several minutes of
              CDS queue time:

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
            :meth:`api`: Builds and submits the CDS request.
            :meth:`post_download`: Slices the downloaded NetCDF into
                per-date GeoTIFFs.
            :class:`Catalog`: Loads ``var_info`` dicts from
                ``cds_data_catalog.yaml``.
        """
        # trigger the request to the server
        self.api(var_info)
        # process the downloaded data
        self.post_download(var_info, self.path, dataset, progress_bar)

    def api(self, var_info: Dict[str, str]):
        """Build a CDS request and submit it via :class:`cdsapi.Client`.

        Constructs the request dictionary expected by
        :meth:`cdsapi.Client.retrieve` from the catalog metadata for a
        single variable, then submits it. The retrieve call blocks until
        CDS has served the request and the NetCDF file has been written
        to disk — typically minutes due to CDS queue times.

        The request shape is fixed for the daily path of this iteration
        of the migration: ``product_type=['reanalysis']``, four
        six-hourly time slots (``00:00/06:00/12:00/18:00``), and
        ``data_format='netcdf'``. The monthly request shape
        (``product_type=['monthly_averaged_reanalysis']``, no ``time``
        key) is task ``M5`` in
        ``planning/cdsapi/migration-plan.md``.

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
            - Inspect the ``var_info`` shape that the method consumes
              and the file name it will produce:

                ```python
                >>> var_info = {
                ...     "cds_dataset": "reanalysis-era5-single-levels",
                ...     "cds_variable": "2m_temperature",
                ...     "file_name": "Tair",
                ... }
                >>> var_info["cds_dataset"]
                'reanalysis-era5-single-levels'
                >>> f"{var_info['file_name']}_{var_info['cds_dataset']}.nc"
                'Tair_reanalysis-era5-single-levels.nc'

                ```
            - Build the same dict for a pressure-level variable; the
              extra ``cds_pressure_level`` key is forwarded as the
              request's ``pressure_level``:

                ```python
                >>> var_info = {
                ...     "cds_dataset": "reanalysis-era5-pressure-levels",
                ...     "cds_variable": "temperature",
                ...     "cds_pressure_level": ["1000"],
                ...     "file_name": "Tair2m",
                ... }
                >>> var_info["cds_pressure_level"]
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
        dataset = var_info["cds_dataset"]
        dates = self.time["dates"]
        request = {
            "product_type": ["reanalysis"],
            "variable": [var_info["cds_variable"]],
            "year": sorted({str(d.year) for d in dates}),
            "month": sorted({f"{d.month:02d}" for d in dates}),
            "day": sorted({f"{d.day:02d}" for d in dates}),
            "time": ["00:00", "06:00", "12:00", "18:00"],
            "data_format": "netcdf",
            "area": [
                self.space["lat_lim"][1],
                self.space["lon_lim"][0],
                self.space["lat_lim"][0],
                self.space["lon_lim"][1],
            ],
        }
        if "cds_pressure_level" in var_info:
            request["pressure_level"] = var_info["cds_pressure_level"]

        target = self.root_dir / f"{var_info['file_name']}_{dataset}.nc"
        logger.info(
            f"Requesting {dataset} from CDS; this may take several minutes"
        )
        self.client.retrieve(dataset, request, str(target))
        return target

    @staticmethod
    def send_request(
        server,
    ):
        """send the request to the server.

        Parameters
        ----------
        output_folder: [str]
            directory where files will be saved
        """
        server.retrieve()


    def post_download(
        self, var_info: Dict[str, str], out_dir, dataset: str, progress_bar: bool = True
    ):
        """clip the downloaded data to the extent we want.

        Parameters
        ----------
        var_info: [str]
            variable detailed information
            >>> {
            >>>     'descriptions': 'Evaporation [m of water]',
            >>>     'units': 'mm',
            >>>     'types': 'flux',
            >>>     'temporal resolution': ['six hours', 'daily', 'monthly'],
            >>>     'file name': 'Evaporation',
            >>>     'download type': 2,
            >>>     'number_para': 182,
            >>>     'var_name': 'e',
            >>>     'factors_add': 0,
            >>>     'factors_mul': 1000
            >>> }
        out_dir: [str]
            root directory for where the files will be saved.
        dataset: [str]
            dataset name. Default is interm
        progress_bar: [bool]
            True to display a progress bar
        """
        # Open the downloaded data
        NC_filename = os.path.join(self.path, f"data_{dataset}.nc")
        fh = Dataset(NC_filename, mode="r")

        # Get the NC variable parameter
        parameter_var = var_info.get("var_name")
        Var_unit = var_info.get("units")
        factors_add = var_info.get("factors_add")
        factors_mul = var_info.get("factors_mul")

        # Open the NC data
        Data = fh.variables[parameter_var][:]
        Data_time = fh.variables["time"][:]
        lons = fh.variables["longitude"][:]
        lats = fh.variables["latitude"][:]

        # Define the georeference information
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

        # Create Waitbar
        if progress_bar:
            total_amount = len(self.dates)
            amount = 0
            print_progress_bar(
                amount, total_amount, prefix="Progress:", suffix="Complete", length=50
            )

        for date in self.dates:

            # Define the year, month and day
            year = date.year
            month = date.month
            day = date.day

            # Hours since 1900-01-01
            start = dt.datetime(year=1900, month=1, day=1)
            end = dt.datetime(year, month, day)
            diff = end - start
            hours_from_start_begin = diff.total_seconds() / 60 / 60

            Date_good = np.zeros(len(Data_time))

            if self.temporal_resolution == "daily":
                days_later = 1
            if self.temporal_resolution == "monthly":
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

            # convert the values to the units we want
            Data_end = factors_mul * np.nanmean(Data_one, 0) + factors_add

            if var_info.get("types") == "flux":
                Data_end = Data_end * days_later

            var_output_name = var_info.get("file name")

            # Define the out name
            name_out = os.path.join(
                out_dir,
                f"{var_output_name}_ECMWF_ERA-Interim_{Var_unit}_{self.temporal_resolution}_{year}.{month}.{day}.tif",
            )

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

        fh.close()


class Catalog(AbstractCatalog):
    """ECMWF data catalog This class contains the information about the ECMWF variables http://rda.ucar.edu/cgi-bin/transform?xml=/metadata/ParameterTables/WMO_GRIB1.98-0.128.xml&view=gribdoc."""

    def __init__(self):
        super().__init__()

    def get_catalog(self):
        """read the data catalog from disk."""
        with open(f"{__path__[0]}/ecmwf_data_catalog.yaml", "r") as stream:
            catalog = yaml.safe_load(stream)
        return catalog

    def get_dataset(self, var_name):
        """retrieve a variable form the datasource catalog."""
        return super().get_dataset(var_name)
