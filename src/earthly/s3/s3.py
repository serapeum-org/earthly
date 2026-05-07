"""Amazon S3."""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import boto3
import botocore
import pandas as pd
from botocore import exceptions
from tqdm import tqdm

from earthly.base import AbstractCatalog, AbstractDataSource


class S3(AbstractDataSource):
    """Amazon S3 data source."""

    def __init__(
        self,
        start: str,
        end: str,
        lat_lim: list[float],
        lon_lim: list[float],
        temporal_resolution: str = "monthly",
        path: str = "",
        variables: list[str] | str = "precipitation_amount_1hour_Accumulation",
        fmt: str = "%Y-%m-%d",
    ):
        """S3.

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

    def _initialize(self, bucket: str = "era5-pds") -> object:
        """initialize connection with amazon s3 and create a client.

        Parameters
        ----------
        bucket: [str]
            S3 bucket name.

        Returns
        -------
        client: [botocore.client.S3]
            Amazon S3 client
        """
        # AWS access / secret keys required
        # s3 = boto3.resource('s3')
        # bucket = s3.Bucket(era5_bucket)

        # No AWS keys required
        client = boto3.client(
            "s3", config=botocore.client.Config(signature_version=botocore.UNSIGNED)
        )
        self.client = client
        return client

    def _create_grid(self, lat_lim: list, lon_lim: list):
        """TODO:"""
        pass

    def _check_input_dates(
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
        self.start = dt.datetime.strptime(start, fmt)
        self.end = dt.datetime.strptime(end, fmt)

        # Set required data for the daily option
        if temporal_resolution == "daily":
            self.dates = pd.date_range(self.start, self.end, freq="D")
        elif temporal_resolution == "monthly":
            self.dates = pd.date_range(self.start, self.end, freq="MS")

    def download(self, progress_bar: bool = True):
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
        catalog = Catalog()
        for var in self.vars:
            var_info = catalog.get_variable(var)
            var_s3_name = var_info.get("bucket_name")
            self._download_dataset(var_s3_name, progress_bar=progress_bar)

    def _download_dataset(self, var: str, progress_bar: bool = True):
        """Download a climate variable.

                    This function downloads ECMWF six-hourly, daily or monthly data.

        Parameters
        ----------
        var: [str]
            variable detailed information
            >>> {
            >>>     'descriptions': 'Evaporation [m of water]',
            >>>     'units': 'mm',
            >>>     'types': 'flux',
            >>>     'temporal resolution': ['six hours', 'daily', 'monthly'],
            >>>     'file name': 'Evaporation',
            >>> }
        progress_bar: [bool]
            True if you want to display a progress bar.
        """
        for date in tqdm(self.dates, desc="Progress", disable=not progress_bar):
            year = date.strftime("%Y")
            month = date.strftime("%m")
            # file path patterns for remote S3 objects and corresponding local file
            s3_data_key = f"{year}/{month}/data/{var}.nc"
            downloaded_file_dir = (
                f"{self.path}/{year}{month}_{self.temporal_resolution}_{var}.nc"
            )

            self._api(s3_data_key, downloaded_file_dir)

    def _api(self, s3_file_path: str, local_dir_fname: str, bucket: str = "era5-pds"):
        """Download file from s3 bucket.

        Parameters
        ----------
        s3_file_path: str
            the whole path for the file inside the bcket. i.e. "2022/02/main.nc"
        local_dir_fname: [str]
            absolute path for the file name and directory in your local drive.
        bucket: [str]
            bucket name. Default is "era5-pds"

        Returns
        -------
        Download the file to your local drive.
        """
        if not os.path.isfile(local_dir_fname):  # check if file already exists
            print(f"Downloading {s3_file_path} from S3...")
            try:
                self.client.download_file(bucket, s3_file_path, local_dir_fname)
            except exceptions.ClientError:
                print(
                    f"Error while downloading the {s3_file_path} please check the file name"
                )
        else:
            print(f"The file {local_dir_fname} already in your local directory")

    @staticmethod
    def parse_response_metadata(response: dict[str, str]):
        """parse client response.

        Parameters
        ----------
        response:
            Dict returned by boto3 S3 calls. Example shape (placeholder
            values shown for clarity — real `HostId` / `x-amz-id-2`
            are opaque high-entropy strings):
        >>> {
        >>>     'RequestId': '<example-request-id>',
        >>>     'HostId': '<example-host-id>',
        >>>     'HTTPStatusCode': 200,
        >>>     'HTTPHeaders': {'x-amz-id-2': '<example-amz-id-2>',
        >>>     'x-amz-request-id': '<example-request-id>',
        >>>     'date': 'Sun, 15 Jan 2023 22:36:28 GMT',
        >>>     'x-amz-bucket-region': 'us-east-1',
        >>>     'content-type': 'application/xml',
        >>>     'transfer-encoding': 'chunked',
        >>>     'server': 'AmazonS3'},
        >>>     'RetryAttempts': 0
        >>> }
        """
        response_meta = response.get("ResponseMetadata")
        keys = []
        if response_meta.get("HTTPStatusCode") == 200:
            contents_list = response.get("Contents")
            if contents_list is None:
                print("No objects are available")  # {date.strftime('%B, %Y')}
            else:
                for obj in contents_list:
                    keys.append(obj.get("Key"))
                print(
                    f"There are {len(keys)} objects available for\n--"
                )  # {date.strftime('%B, %Y')}
                for k in keys:
                    print(k)
        else:
            print("There was an error with your request.")

        return keys


class Catalog(AbstractCatalog):
    """S3 data catalog."""

    bucket: str = "era5-pds"
    client: Any = None

    def model_post_init(self, __context: Any) -> None:
        super().model_post_init(__context)
        self.client = self.initialize(bucket=self.bucket)

    @staticmethod
    def initialize(bucket: str = "era5-pds") -> object:
        """initialize connection with amazon s3 and create a client.

        Parameters
        ----------
        bucket: [str]
            S3 bucket name.

        Returns
        -------
        client: [botocore.client.S3]
            Amazon S3 client
        """
        # AWS access / secret keys required
        # s3 = boto3.resource('s3')
        # bucket = s3.Bucket(era5_bucket)

        # No AWS keys required
        client = boto3.client(
            "s3", config=botocore.client.Config(signature_version=botocore.UNSIGNED)
        )
        return client

    def get_catalog(self):
        """return the catalog."""
        return {
            "precipitation": {
                "descriptions": "rainfall [mm/temporal_resolution]",
                "units": "mm/temporal_resolution",
                "temporal resolution": ["daily", "monthly"],
                "file name": "rainfall",
                "var_name": "R",
                "bucket_name": "precipitation_amount_1hour_Accumulation",
            }
        }

    def get_variable(self, var_name) -> dict[str, str]:
        """get the details of a specific variable."""
        return super().get_variable(var_name)

    def get_available_years(self, bucket: str = "era5-pds"):
        """The ERA5 data is chunked into distinct NetCDF files per variable, each containing a month of hourly data. These files are organized in the S3 bucket by year, month, and variable name.

        The data is structured as follows:

        /{year}/{month}/main.nc
                       /data/{var1}.nc
                            /{var2}.nc
                            /{....}.nc
                            /{varN}.nc

        - where year is expressed as four digits (e.g. YYYY) and month as two digits (e.g. MM).

        Parameters
        ----------
        bucket: [str]
            S3 bucket name

        Returns
        -------
        List:
            list of years that have available data.
        """
        paginator = self.client.get_paginator("list_objects")
        result = paginator.paginate(Bucket=bucket, Delimiter="/")
        # for prefix in result.search('CommonPrefixes'):
        #     print(prefix.get('Prefix'))
        years = [i.get("Prefix")[:-1] for i in result.search("CommonPrefixes")]
        return years

    def get_available_data(
        self,
        date: str,
        bucket: str = "era5-pds",
        fmt: str = "%Y-%m-%d",
        absolute_path: bool = False,
    ) -> list[str]:
        """get the available data at a given year.

        - Granule variable structure and metadata attributes are stored in main.nc. This file contains coordinate and
        auxiliary variable data. This file is also annotated using NetCDF CF metadata conventions.

        Parameters
        ----------
        date: [str]
            date i.e. "YYYY-mm-dd"
        bucket: [str]
            The bucket you want to get its available data. Default is 'era5-pds'.
        fmt: [str]
            Date format. Default is "%Y-%m-%d".
        absolute_path: [bool]
            True if you want to get the file names including the whole path inside the bucket.
            Default is False.
            >>> absolute_path = True
            [
                '2022/05/air_pressure_at_mean_sea_level.nc',
                 '2022/05/air_temperature_at_2_metres.nc',
                 '2022/05/air_temperature_at_2_metres_1hour_Maximum.nc',
                 '2022/05/air_temperature_at_2_metres_1hour_Minimum.nc',
                 '2022/05/dew_point_temperature_at_2_metres.nc',
                 '2022/05/eastward_wind_at_100_metres.nc'
             ]
            >>> absolute_path = False
            [
                'air_pressure_at_mean_sea_level.nc',
                'air_temperature_at_2_metres.nc',
                'air_temperature_at_2_metres_1hour_Maximum.nc',
                'air_temperature_at_2_metres_1hour_Minimum.nc',
                'dew_point_temperature_at_2_metres.nc',
                'eastward_wind_at_100_metres.nc'
             ]
        Returns
        -------
        List:
            available data in a list
        """
        date_obj = dt.datetime.strptime(date, fmt)
        # date = dt.date(2022,5,1) # update to desired date
        prefix = date_obj.strftime("%Y/%m/")
        response = self.client.list_objects_v2(Bucket=bucket, Prefix=prefix)
        keys = S3.parse_response_metadata(response)
        if absolute_path:
            available_date = keys
        else:
            available_date = [i.split("/")[-1] for i in keys]
        return available_date
