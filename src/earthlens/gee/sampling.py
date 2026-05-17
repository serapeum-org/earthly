"""Sample raster values at point locations via Google Earth Engine.

The single public entry point, :func:`sample_points`, takes an
`ee.Image` and a `GeoDataFrame` of point geometries and returns an
`ee.FeatureCollection` where each input point carries the reduced
raster value(s) from its surrounding pixels as feature properties.

Implementation note — the input points are first inserted into an
`rtree.index.Index` and grouped into that index's leaves; one
`image.reduceRegions` call is issued per leaf, with each leaf's
bounding box used to clip the image first. This keeps the number of
round-trips bounded by the leaf count rather than the point count
(one call per point would be wasteful) while still keeping each call's
clip box tight (one call over the full point set would force EE to
work over the entire image).

The reducer is selected by name (e.g. `"first"`, `"mean"`,
`"median"`) and resolved through a fixed whitelist on `ee.Reducer` —
no `eval` (per N1 in `planning/gee-utils.md`).
"""

from __future__ import annotations

import ee
from geopandas.geodataframe import GeoDataFrame
from rtree import index

import geopandas as gpd

from earthlens.gee.features import create_feature
from earthlens.gee.io import feature_collection_to_gdf as _fc_to_gdf

_REDUCER_WHITELIST: frozenset[str] = frozenset(
    {
        "first",
        "mean",
        "median",
        "mode",
        "max",
        "min",
        "sum",
        "stdDev",
        "variance",
        "count",
    }
)


def _resolve_reducer(name: str):
    """Resolve a reducer name to an `ee.Reducer` instance via the whitelist.

    Args:
        name: The reducer factory name on `ee.Reducer` (e.g. `"mean"`).
            Must be one of :data:`_REDUCER_WHITELIST`.

    Returns:
        The `ee.Reducer` instance returned by `getattr(ee.Reducer, name)()`.

    Raises:
        ValueError: If `name` is not in :data:`_REDUCER_WHITELIST`.
    """
    if name not in _REDUCER_WHITELIST:
        raise ValueError(
            f"unsupported reducer {name!r}; expected one of "
            f"{sorted(_REDUCER_WHITELIST)}"
        )
    return getattr(ee.Reducer, name)()


def sample_points(
    image: ee.Image,
    gdf: GeoDataFrame,
    *,
    scale_m: float,
    reducer: str = "first",
) -> ee.FeatureCollection:
    """Sample an Earth Engine image at every point in a `GeoDataFrame`.

    Each input point is reduced from its `scale_m`-metre neighbourhood
    with the named reducer; the resulting `ee.FeatureCollection` keeps
    the GDF's non-geometry columns as feature properties and adds the
    reduced band value(s).

    Args:
        image: The `ee.Image` to sample.
        gdf: A `GeoDataFrame` whose geometries are points; non-geometry
            columns are carried through as feature properties.
        scale_m: The pixel scale (metres) passed to
            `image.reduceRegions(scale=...)`.
        reducer: The name of an `ee.Reducer` factory; must be one of
            the supported reducers (see :data:`_REDUCER_WHITELIST`).
            Defaults to `"first"`.

    Returns:
        An `ee.FeatureCollection` of the input points, each carrying
        the reduced band value(s).

    Raises:
        ValueError: If `reducer` is not in :data:`_REDUCER_WHITELIST`,
            or if `gdf` is empty.
    """
    if len(gdf) == 0:
        raise ValueError("sample_points requires a non-empty GeoDataFrame")
    ee_reducer = _resolve_reducer(reducer)

    rtree_idx = index.Index()
    for i, geom in enumerate(gdf.geometry):
        rtree_idx.insert(i, geom.bounds)
    leaves = sorted(rtree_idx.leaves())

    collection = ee.FeatureCollection([])
    for _, indices, bbox in leaves:
        ee_bbox = ee.Geometry.BBox(bbox[0], bbox[1], bbox[2], bbox[3])
        ee_feature_col = create_feature(gdf.iloc[indices])
        clipped = image.clip(ee_bbox)
        collection = collection.merge(
            ee.FeatureCollection(
                clipped.reduceRegions(
                    collection=ee_feature_col,
                    reducer=ee_reducer,
                    scale=scale_m,
                )
            )
        )
    return ee.FeatureCollection(collection)


def sample_points_to_gdf(
    image: ee.Image,
    gdf: GeoDataFrame,
    *,
    scale_m: float,
    reducer: str = "first",
    crs: int | str = 4326,
) -> gpd.GeoDataFrame:
    """Sample an Earth Engine image at points and return a `GeoDataFrame`.

    Thin composition of :func:`sample_points` and
    :func:`earthlens.gee.io.feature_collection_to_gdf` — call
    `sample_points` for the leaf-batched `reduceRegions` work, then
    pull the result with a single `getInfo()` and reshape into a
    `GeoDataFrame`. Suitable for small to medium point sets (the
    `getInfo()` upper bound is ~5000 features / ~10 MB); for larger
    jobs, call `sample_points` directly and pipe its output through
    :func:`earthlens.gee.io.feature_collections_to_dataframe` instead.

    Args:
        image: The `ee.Image` to sample.
        gdf: A `GeoDataFrame` of point geometries.
        scale_m: The pixel scale (metres) passed to
            `image.reduceRegions(scale=...)`.
        reducer: The reducer name; one of :data:`_REDUCER_WHITELIST`.
            Defaults to `"first"`.
        crs: The CRS to stamp onto the output `GeoDataFrame`. Defaults
            to `4326`.

    Returns:
        A `GeoDataFrame` with one row per input point, carrying the
        input properties plus the reduced band value(s).

    Raises:
        ValueError: If `reducer` is not in :data:`_REDUCER_WHITELIST`,
            or if `gdf` is empty.
    """
    fc = sample_points(image, gdf, scale_m=scale_m, reducer=reducer)
    return _fc_to_gdf(fc, crs=crs)
