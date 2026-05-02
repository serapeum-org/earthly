from __future__ import annotations

import datetime as dt

# import ee
from geopandas.geodataframe import GeoDataFrame

from earthly.gee.data import getCatalog
from earthly.gee.gee import GEE

catalog = getCatalog()
default_date_format = "%Y-%m-%d"


class Dataset(GEE):
    """Dataset."""

    def __init__(
        self,
        dataset_id: str,
        start_date: str,
        end_date: str,
        date_format: str = "%Y-%m-%d",
    ):
        if dataset_id not in catalog["dataset"].tolist():
            raise ValueError(
                f"the given dataset: {dataset_id} does nor exist in the catalog"
            )
        else:
            self.metadata = catalog.loc[catalog["dataset"] == dataset_id, :]
            self.id = id

        self.start_date, self.end_date = self.getDate(
            dataset_id, start_date, end_date, date_format
        )
        # self.catalog = catalog
        self.boundary = None

    @staticmethod
    def getDate(
        dataset_id: str,
        start_date: str = None,
        end_date: str = None,
        date_format: str = default_date_format,
    ):
        """getDate.

            getDate retrieves the start and end date of a dataset

        Parameters
        ----------
        dataset_id: [str]
            dataset id as in the catalog.
        start_date: [str]
            to check it the given start date falls in the available dataset
        end_date: [str]
            to check it the given end date falls in the available dataset
        date_format: [str]
            format of the given dates, Default is YYYY-MM-DD

        Returns
        -------
        start_date: [str]
            beginning of the temporal_resolution series.

        end_date: [str]
            end of the temporal_resolution series.
        """
        data = catalog.loc[catalog["dataset"] == dataset_id, :]

        dataset_start_date = dt.datetime.strptime(
            data["start_date"].values[0], default_date_format
        )
        dataset_end_date = data["end_date"].values[0]
        if dataset_end_date == "Now":
            dataset_end_date = dt.datetime.now().date()

        if not start_date:
            start_date = dt.datetime.strptime(start_date, date_format)
            if start_date < dataset_start_date:
                start_date = dataset_start_date
        else:
            start_date = dataset_start_date

        if not end_date:
            end_date = dt.datetime.strptime(end_date, date_format)
            if end_date > dataset_end_date:
                end_date = dataset_end_date
        else:
            end_date = dataset_end_date

        return start_date, end_date

    def addBoundary(self, gdf: GeoDataFrame):
        """addBoundary.

            addBoundary

        Parameters
        ----------
        gdf
        """
        self.boundary = gdf.copy()

    def filterByRegion(self, gdf: GeoDataFrame = None):
        """filterByRegion.

            filterByRegion

        Parameters
        ----------
        gdf
        """
        if gdf:
            self.addBoundary(gdf)
