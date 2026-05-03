from __future__ import annotations


import ee
import pandas as pd
from ee.featurecollection import FeatureCollection
from ee.geometry import Geometry
from geopandas.geodataframe import GeoDataFrame
from loguru import logger
from shapely.geometry import LineString, Point, Polygon


def createGeometry(
    shapely_geometry: Polygon | Point | LineString,
    epsg: int = 4326,
) -> Geometry:
    """createGeometry.

        create earth engine geometry.

    Parameters
    ----------
    shapely_geometry: [shapely.geometry]
        shapely geometry object [point, polyline, Linestring]
    epsg: [int]
        projection epsg number.

    Returns
    -------
    ee Geometry :
        ee geometry object [Polygon, Point, LineString]
    """
    coords = shapely_geometry.__geo_interface__["coordinates"]
    if shapely_geometry.geom_type == "Polygon":
        return ee.Geometry.Polygon(coords, f"epsg:{epsg}")

    elif shapely_geometry.geom_type in ["Point", "LineString"]:
        if shapely_geometry.geom_type == "LineString":
            raise ValueError("LineStrings not yet implemented.")
        else:
            return ee.Geometry.Point(coords, f"epsg:{epsg}")

    else:
        logger.debug(
            f"The given geometry is neiter of type LineString, Point nor Polygon, "
            f"but {shapely_geometry.geom_type}."
        )


def createFeature(
    gdf: GeoDataFrame, columns: list[str] | None = None
) -> FeatureCollection:
    """createFeature.

        createFeature creates a feature collection from a geodataframe

        collection with the data in a certain column in the geodataframe as a properties dictionary

    Parameters
    ----------
    gdf : [GeoDataFrame]

    columns: [list]
        list of strings for the columns' names

    Returns
    -------
    FeatureCollection : [ee.featurecollection.FeatureCollection]
        feature collection containing the geometry of each row in the given geodataframe
        with the information of one of the given columns as a property.
    """
    try:
        # get the geometry type for all rows
        geotype = [i.geom_type for i in gdf["geometry"]]
        # if any is "MultiPolygon" explode the dataframe to single polygons
        if "MultiPolygon" in geotype:
            # index_parts=True makes the resulted index multi-index if multi-polygon resulted in
            # many different polygons
            gdf = gdf.explode(index_parts=True)

        ee_geom_list = gdf.geometry.apply(lambda geom: createGeometry(geom)).to_list()
        records_df = pd.DataFrame(gdf.drop("geometry", axis=1))
        if columns:
            records_df = records_df[columns]
        records = records_df.to_dict("records")
        if not records:
            ee_feature_list = [ee.Feature(geom) for geom in ee_geom_list]
        else:
            ee_feature_list = [
                ee.Feature(geom, record) for geom, record in zip(ee_geom_list, records)
            ]
        return ee.FeatureCollection(ee_feature_list)

    except Exception as error:
        logger.error(error)
        raise ValueError(error)
