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
from shapely.geometry import LineString, Point, Polygon


def create_geometry(
    shapely_geometry: Polygon | Point | LineString,
    epsg: int = 4326,
) -> Geometry:
    """Convert a Shapely `Polygon` or `Point` to an `ee.Geometry`.

    The geometry's GeoJSON coordinates are passed straight to
    `ee.Geometry.Polygon` / `ee.Geometry.Point` along with an
    `"epsg:<epsg>"` projection string.

    Args:
        shapely_geometry: A Shapely `Polygon` or `Point`.
        epsg: EPSG code of the geometry's coordinates. Defaults to
            `4326` (WGS84 lon/lat).

    Returns:
        The corresponding `ee.Geometry` (`Polygon` or `Point`).

    Raises:
        NotImplementedError: If `shapely_geometry` is any geometry type
            other than `Polygon` or `Point` (e.g. `LineString`,
            `MultiPoint`, `GeometryCollection`). Use
            :func:`create_feature` for `MultiPolygon` inputs — it
            explodes them into per-polygon rows before calling this.

    Examples:
        - Convert a unit-square polygon (needs the `ee` SDK initialised):
            ```python
            >>> from shapely.geometry import Polygon
            >>> from earthlens.gee.features import create_geometry
            >>> square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
            >>> geom = create_geometry(square)  # doctest: +SKIP

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
            >>> from earthlens.gee.features import create_geometry
            >>> create_geometry(LineString([(0, 0), (1, 1)]))
            Traceback (most recent call last):
                ...
            NotImplementedError: LineString geometries are not yet supported by the GEE backend.

            ```

    See Also:
        create_feature: Builds an `ee.FeatureCollection` from a
            `GeoDataFrame`, calling this for each row's geometry.
    """
    coords = shapely_geometry.__geo_interface__["coordinates"]
    geom_type = shapely_geometry.geom_type
    if geom_type == "Polygon":
        return ee.Geometry.Polygon(coords, f"epsg:{epsg}")
    if geom_type == "Point":
        return ee.Geometry.Point(coords, f"epsg:{epsg}")
    if geom_type == "LineString":
        raise NotImplementedError(
            "LineString geometries are not yet supported by the GEE backend."
        )
    raise NotImplementedError(
        f"{geom_type} geometries are not supported by the GEE backend; "
        "only Polygon and Point are accepted (MultiPolygon is auto-exploded "
        "by create_feature)."
    )


def create_feature(
    gdf: GeoDataFrame, columns: list[str] | None = None
) -> FeatureCollection:
    """Build an `ee.FeatureCollection` from a `GeoDataFrame`.

    Each row becomes an `ee.Feature` whose geometry is the converted
    Shapely geometry (via :func:`create_geometry`) and whose properties
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
            :func:`create_geometry` (e.g. a `LineString`).
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
            >>> from earthlens.gee.features import create_feature
            >>> gdf = gpd.GeoDataFrame(
            ...     {"name": ["a", "b"],
            ...      "geometry": [Polygon([(0, 0), (1, 0), (1, 1)]),
            ...                   Polygon([(2, 2), (3, 2), (3, 3)])]},
            ...     crs="EPSG:4326",
            ... )
            >>> fc = create_feature(gdf)  # doctest: +SKIP

            ```
        - Restricting which columns become properties:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.gee.features import create_feature
            >>> gdf = gpd.GeoDataFrame(
            ...     {"name": ["a"], "value": [1],
            ...      "geometry": [Polygon([(0, 0), (1, 0), (1, 1)])]},
            ...     crs="EPSG:4326",
            ... )
            >>> fc = create_feature(gdf, columns=["name"])  # doctest: +SKIP

            ```

    See Also:
        create_geometry: Converts a single Shapely geometry; called per row.
    """
    geotype = [i.geom_type for i in gdf["geometry"]]
    # if any is "MultiPolygon" explode the dataframe to single polygons
    # (`index_parts=True` makes the resulted index multi-index if a multi-polygon
    #  resulted in many different polygons)
    if "MultiPolygon" in geotype:
        gdf = gdf.explode(index_parts=True)

    # Convert per-row; on the first failure raise a `ValueError` naming
    # the offending row index so the user can spot it in a large frame
    # (M2 in pr-diff-review: don't hand `None` / opaque errors to EE).
    ee_geom_list: list[Geometry] = []
    for i, geom in enumerate(gdf.geometry):
        try:
            ee_geom_list.append(create_geometry(geom))
        except NotImplementedError as exc:
            raise ValueError(
                f"create_feature cannot convert row {i} "
                f"({geom.geom_type}): {exc}"
            ) from exc
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
