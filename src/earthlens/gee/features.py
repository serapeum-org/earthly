"""Shapely / GeoDataFrame → Earth Engine geometry converters.

The GEE backend lets a caller pass a `GeoDataFrame` as the clip
`region`; this module turns Shapely geometries and `GeoDataFrame`s into
the `ee.Geometry` / `ee.FeatureCollection` objects Earth Engine expects.
Only `Polygon` and `Point` geometries are supported (`LineString` is not
yet implemented); a `GeoDataFrame` of `MultiPolygon`s is exploded to one
feature per polygon part, and the non-geometry columns become each
feature's property dictionary.
"""

from __future__ import annotations

import ee
import pandas as pd
from ee.featurecollection import FeatureCollection
from ee.geometry import Geometry
from geopandas.geodataframe import GeoDataFrame
from loguru import logger
from shapely.geometry import LineString, Point, Polygon


def createGeometry(  # noqa: N802 - established public name
    shapely_geometry: Polygon | Point | LineString,
    epsg: int = 4326,
) -> Geometry | None:
    """Convert a Shapely `Polygon` or `Point` to an `ee.Geometry`.

    The geometry's GeoJSON coordinates are passed straight to
    `ee.Geometry.Polygon` / `ee.Geometry.Point` along with an
    `"epsg:<epsg>"` projection string.

    Args:
        shapely_geometry: A Shapely `Polygon` or `Point` (a `LineString`
            is accepted by the type but raises — see below).
        epsg: EPSG code of the geometry's coordinates. Defaults to
            `4326` (WGS84 lon/lat).

    Returns:
        The corresponding `ee.Geometry` (`Polygon` or `Point`), or
        `None` if `shapely_geometry` is some other geometry type (e.g.
        `MultiPolygon`) — in which case a debug message is logged.

    Raises:
        ValueError: If `shapely_geometry` is a `LineString` (not yet
            implemented).

    Examples:
        - Convert a unit-square polygon (needs the `ee` SDK initialised):
            ```python
            >>> from shapely.geometry import Polygon
            >>> from earthlens.gee.features import createGeometry
            >>> square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
            >>> geom = createGeometry(square)  # doctest: +SKIP

            ```
        - The GeoJSON coordinates that get handed to Earth Engine:
            ```python
            >>> from shapely.geometry import Polygon
            >>> Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]).__geo_interface__["coordinates"]
            (((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)),)

            ```
        - A `LineString` is rejected:
            ```python
            >>> from shapely.geometry import LineString
            >>> from earthlens.gee.features import createGeometry
            >>> createGeometry(LineString([(0, 0), (1, 1)]))
            Traceback (most recent call last):
                ...
            ValueError: LineStrings not yet implemented.

            ```

    See Also:
        createFeature: Builds an `ee.FeatureCollection` from a
            `GeoDataFrame`, calling this for each row's geometry.
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
        return None


def createFeature(  # noqa: N802 - established public name
    gdf: GeoDataFrame, columns: list[str] | None = None
) -> FeatureCollection:
    """Build an `ee.FeatureCollection` from a `GeoDataFrame`.

    Each row becomes an `ee.Feature` whose geometry is the converted
    Shapely geometry (via :func:`createGeometry`) and whose properties
    are that row's non-geometry columns (optionally narrowed to
    `columns`). A row holding a `MultiPolygon` is exploded into one
    feature per constituent polygon.

    Args:
        gdf: A `GeoDataFrame` whose `geometry` column holds `Polygon` /
            `Point` / `MultiPolygon` geometries.
        columns: If given, only these (non-geometry) columns become
            feature properties; otherwise all non-geometry columns are
            used. If the frame has no non-geometry columns (or `columns`
            is empty/`None` with a geometry-only frame), features are
            created without properties.

    Returns:
        An `ee.FeatureCollection` with one feature per (exploded) row.

    Raises:
        ValueError: If any row's geometry cannot be converted via
            :func:`createGeometry` (e.g. a `LineString`).
        KeyError: If `gdf` has no `geometry` column, or if any of the
            requested `columns` is missing from `gdf`.
        Other exceptions raised by `pandas` / `geopandas` /
        `earthengine-api` propagate verbatim (with their original type
        and traceback).

    Examples:
        - Build a collection from two polygons with a `name` property
          (needs the `ee` SDK initialised):
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.gee.features import createFeature
            >>> gdf = gpd.GeoDataFrame(
            ...     {"name": ["a", "b"],
            ...      "geometry": [Polygon([(0, 0), (1, 0), (1, 1)]),
            ...                   Polygon([(2, 2), (3, 2), (3, 3)])]},
            ...     crs="EPSG:4326",
            ... )
            >>> fc = createFeature(gdf)  # doctest: +SKIP

            ```
        - Restricting which columns become properties:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.gee.features import createFeature
            >>> gdf = gpd.GeoDataFrame(
            ...     {"name": ["a"], "value": [1],
            ...      "geometry": [Polygon([(0, 0), (1, 0), (1, 1)])]},
            ...     crs="EPSG:4326",
            ... )
            >>> fc = createFeature(gdf, columns=["name"])  # doctest: +SKIP

            ```

    See Also:
        createGeometry: Converts a single Shapely geometry; called per row.
    """
    geotype = [i.geom_type for i in gdf["geometry"]]
    # if any is "MultiPolygon" explode the dataframe to single polygons
    # (`index_parts=True` makes the resulted index multi-index if a multi-polygon
    #  resulted in many different polygons)
    if "MultiPolygon" in geotype:
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
            ee.Feature(geom, record)
            for geom, record in zip(ee_geom_list, records)
        ]
    return ee.FeatureCollection(ee_feature_list)
