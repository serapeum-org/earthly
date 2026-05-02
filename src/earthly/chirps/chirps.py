from __future__ import annotations

import datetime as dt
import os
from ftplib import FTP

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from pyramids.dataset import Dataset
from pyramids._io import extract_from_gz
from tqdm import tqdm

from earthly.base import AbstractCatalog, AbstractDataSource


class CHIRPS(AbstractDataSource):
    """CHIRPS."""

    api_url: str = "data.chc.ucsb.edu"
    start_date: str = "1981-01-01"
    end_date: str = "Now"
    temporal_resolution = ["daily", "monthly"]
    lat_bondaries = [-50, 50]
    lon_boundaries = [-180, 180]
    globe_fname = "chirps-v2.0"
    clipped_fname = "P_CHIRPS.v2.0"

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
        """CHIRPS.

        Parameters
        ----------
        temporal_resolution (str, optional):
            'daily' or 'monthly'. Defaults to 'daily'.
        start (str, optional):
            [description]. Defaults to ''.
        end (str, optional):
            [description]. Defaults to ''.
        path (str, optional):
            Path where you want to save the downloaded data. Defaults to ''.
        variables (list, optional):
            Variable code: VariablesInfo('day').descriptions.keys(). Defaults to [].
        lat_lim (list, optional):
            [ymin, ymax] (values must be between -50 and 50). Defaults to [].
        lon_lim (list, optional):
            [xmin, xmax] (values must be between -180 and 180). Defaults to [].
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
        # check temporal_resolution variables
        if start is None:
            self.start = pd.Timestamp(self.start_date)
        else:
            self.start = dt.datetime.strptime(start, fmt)

        if end is None:
            self.end = pd.Timestamp(self.end_date)
        else:
            self.end = dt.datetime.strptime(end, fmt)

        # Define timestep for the timedates
        if temporal_resolution.lower() == "daily":
            self.time_freq = "D"
            # self.path = os.path.join(path, "precipitation", "chirps", "daily")
        elif temporal_resolution.lower() == "monthly":
            self.time_freq = "MS"
            # self.path = os.path.join(
            #     path, "Precipitation", "CHIRPS", "Monthly"
            # )
        else:
            raise KeyError("The input temporal_resolution interval is not supported")

        # Create days
        self.dates = pd.date_range(self.start, self.end, freq=self.time_freq)

    def initialize(self):
        """Initialize FTP server."""
        print("FTP server datasources does not need server initialization")
        pass

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
        self.lat_lim = []
        self.lon_lim = []
        # Check space variables
        # -50 , 50
        if lat_lim[0] < self.lat_bondaries[0] or lat_lim[1] > self.lat_bondaries[1]:
            print(
                "Latitude above 50N or below 50S is not possible."
                " Value set to maximum"
            )
            self.lat_lim[0] = np.max(lat_lim[0], self.lat_bondaries[0])
            self.lat_lim[1] = np.min(lon_lim[1], self.lat_bondaries[1])
        # -180, 180
        if lon_lim[0] < self.lon_boundaries[0] or lon_lim[1] > self.lon_boundaries[1]:
            print(
                "Longitude must be between 180E and 180W."
                " Now value is set to maximum"
            )
            self.lon_lim[0] = np.max(lat_lim[0], self.lon_boundaries[0])
            self.lon_lim[1] = np.min(lon_lim[1], self.lon_boundaries[1])
        else:
            self.lat_lim = lat_lim
            self.lon_lim = lon_lim

        # Define IDs
        self.yID = 2000 - np.int16(
            np.array(
                [np.ceil((lat_lim[1] + 50) * 20), np.floor((lat_lim[0] + 50) * 20)]
            )
        )
        self.xID = np.int16(
            np.array(
                [np.floor((lon_lim[0] + 180) * 20), np.ceil((lon_lim[1] + 180) * 20)]
            )
        )

    def download(self, progress_bar: bool = True, cores=None, *args, **kwargs):
        """Download.

            downloads CHIRPS data

        Parameters
        ----------
        progress_bar : TYPE, optional
            will print a waitbar. The default is 1.
        cores : TYPE, optional
            The number of cores used to run the routine. It can be 'False'
                 to avoid using parallel computing routines. The default is None.

        Returns
        -------
        results : TYPE
            DESCRIPTION.
        """
        # Pass variables to parallel function and run
        args = [
            self.path,
            self.temporal_resolution,
            self.xID,
            self.yID,
            self.lon_lim,
            self.lat_lim,
        ]

        if not cores:
            for date in tqdm(self.dates, desc="Progress", disable=not progress_bar):
                self.API(date, args)
            results = True
        else:
            results = Parallel(n_jobs=cores)(
                delayed(self.API)(date, args) for date in self.dates
            )
        return results

    def API(self, date, args):
        """form the request url abd trigger the request.

        Parameters
        ----------
        date:

        args: [list]
        """
        [path, temp_resolution, xID, yID, lon_lim, latlim] = args

        # Define FTP path to directory
        if temp_resolution.lower() == "daily":
            pathFTP = f"pub/org/chg/products/CHIRPS-2.0/global_daily/tifs/p05/{date.strftime('%Y')}/"
        elif temp_resolution == "monthly":
            pathFTP = "pub/org/chg/products/CHIRPS-2.0/global_monthly/tifs/"
        else:
            raise KeyError("The input temporal_resolution interval is not supported")

        # create all the input name (filename) and output (outfilename, filetif, DiFileEnd) names
        if temp_resolution.lower() == "daily":
            filename = f"{self.globe_fname}.{date.strftime('%Y')}.{date.strftime('%m')}.{date.strftime('%d')}.tif.gz"
            outfilename = os.path.join(
                path,
                f"{self.globe_fname}.{date.strftime('%Y')}.{date.strftime('%m')}.{date.strftime('%d')}.tif",
            )
            DirFileEnd = os.path.join(
                path,
                f"{self.clipped_fname}_mm-day-1_daily_{date.strftime('%Y')}.{date.strftime('%m')}.{date.strftime('%d')}.tif",
            )
        elif temp_resolution == "monthly":
            filename = (
                f"{self.globe_fname}.{date.strftime('%Y')}.{date.strftime('%m')}.tif.gz"
            )
            outfilename = os.path.join(
                path,
                f"{self.globe_fname}.{date.strftime('%Y')}.{date.strftime('%m')}.tif",
            )
            DirFileEnd = os.path.join(
                path,
                f"{self.clipped_fname}_mm-month-1_monthly_{date.strftime('%Y')}.{date.strftime('%m')}.{date.strftime('%d')}.tif",
            )
        else:
            raise KeyError("The input temporal_resolution interval is not supported")

        self.callAPI(pathFTP, path, filename)
        self.post_download(
            path, filename, lon_lim, latlim, xID, yID, outfilename, DirFileEnd
        )

    @staticmethod
    def callAPI(pathFTP: str, path: str, filename: str):
        """send the request to the server.

        RetrieveData method retrieves CHIRPS data for a given date from the
        https://data.chc.ucsb.edu/

        Parameters
        ----------
        filename
        path
        pathFTP


        Raises
        ------
        KeyError
            DESCRIPTION.

        Returns
        -------
        bool
            DESCRIPTION.
        """
        ftp = FTP(CHIRPS.api_url)
        ftp.login()
        # find the document name in this directory
        ftp.cwd(pathFTP)
        listing = []

        # read all the file names in the directory
        ftp.retrlines("LIST", listing.append)

        # download the global rainfall file
        local_filename = os.path.join(path, filename)
        lf = open(local_filename, "wb")
        ftp.retrbinary("RETR " + filename, lf.write, 8192)
        lf.close()

    def post_download(
        self,
        path,
        filename,
        lon_lim,
        lat_lim,
        x_id,
        y_id,
        out_file_name,
        dir_file_end,
    ):
        """clip the downloaded data to the extent we want.

        Parameters
        ----------
        path: [str]
            directory where files will be saved
        filename: [str]
            file name
        lon_lim: [list]
        lat_lim: [list]
        x_id: [list]
        y_id: [list]
        out_file_name: [str]
        dir_file_end: [str]
        """
        try:
            # unzip the file
            zip_filename = os.path.join(path, filename)
            extract_from_gz(zip_filename, out_file_name, delete=True)

            # open tiff file
            dataset = Dataset.read_file(out_file_name)

            data = dataset.read_array()
            no_data_value = dataset.no_data_value[0]

            # clip dataset to the given extent
            data = data[y_id[0]: y_id[1], x_id[0]: x_id[1]]
            # replace -ve values with -9999
            data[data < 0] = -9999

            # save dataset as a geotiff file
            geo = [lon_lim[0], 0.05, 0, lat_lim[1], 0, -0.05]

            new_dataset = Dataset.create_from_array(data, geo=geo, epsg=dataset.epsg, no_data_value=no_data_value)
            new_dataset.to_file(dir_file_end)

            # delete old tif file
            os.remove(out_file_name)

        except PermissionError:
            print(
                "The file covering the whole world could not be deleted please delete it after the download ends"
            )
        return True


class Catalog(AbstractCatalog):
    """CHIRPS data catalog."""

    def get_catalog(self):
        """return the catalog."""
        return {
            "Precipitation": {
                "descriptions": "rainfall [mm/temporal_resolution]",
                "units": "mm/temporal_resolution",
                "temporal resolution": ["daily", "monthly"],
                "file name": "rainfall",
                "var_name": "R",
            }
        }

    def get_variable(self, var_name):
        """get the details of a specific variable."""
        return super().get_variable(var_name)
