"""Polygon sizing and gridding helpers.

Pure-Shapely / pyproj / GeoPandas. Operates on `GeoDataFrame` polygons
(or point sets, for the convex-hull helpers); no Earth Engine, no AOI
semantics other than "is this polygon too big at this cell size?"

Two related-but-distinct concerns live here:

1. **Sizing** — how many pixels at a given `cell_size` does a polygon
   take up? `count_cells_polygon` / `count_cells_convex_hull_from_points`.
2. **Splitting** — when a polygon is "too big" (more than `max_pixels`),
   slice it into a grid of adjacent blocks each below the cap.
   `grid_polygon` does the grid math; `split_polygon` is the
   work-horse that decides whether to split and returns the grid.

The `_points` cousin (`split_points`) partitions a `GeoDataFrame` of
points into spatially-coherent chunks via the polygon-side splitter on
the points' convex hull.

The standalone `union_of_convex_hulls` helper returns the pure-Shapely
union of per-row convex hulls; callers that need an `ee.Geometry`
should keep that wrapper next to their Earth Engine code (see
`earthlens.gee`).

Public surface:

* :func:`grid_polygon` — tile a polygon's bbox into adjacent blocks.
* :func:`count_cells_polygon` — pixel count for a polygon at `cell_size`.
* :func:`convex_hull_from_points` — `(hull_gdf, hull_geometry)` for a points GDF.
* :func:`count_cells_convex_hull_from_points` — pixel count for a points convex hull.
* :func:`union_of_convex_hulls` — single-geometry union of per-row convex hulls.
* :func:`split_polygon` — split-if-too-big work-horse.
* :func:`split_points` — partition a points GDF into spatially-coherent chunks.

Examples:
    - Decide a polygon fits at 30 m / 1e7-cell cap (no split needed):
        ```python
        >>> import geopandas as gpd
        >>> from shapely.geometry import Polygon
        >>> from earthlens.spatial import split_polygon
        >>> poly = Polygon([(31.0, 30.0), (31.05, 30.0), (31.05, 30.05), (31.0, 30.05)])
        >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
        >>> was_split, _ = split_polygon(gdf, cell_size=30)
        >>> was_split
        False

        ```
    - Force a split with a tight `max_pixels` cap:
        ```python
        >>> import geopandas as gpd
        >>> from shapely.geometry import Polygon
        >>> from earthlens.spatial import split_polygon
        >>> poly = Polygon([(31.0, 30.0), (31.5, 30.0), (31.5, 30.5), (31.0, 30.5)])
        >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
        >>> was_split, grid = split_polygon(gdf, cell_size=30, max_pixels=1e5)
        >>> was_split
        True
        >>> len(grid) > 1
        True

        ```
"""

from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
from geopandas import GeoDataFrame
from pyproj.exceptions import CRSError
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from earthlens.utm import project_to_utm

_WGS84_CODE: int = 4326


def grid_polygon(
    poly_gdf: GeoDataFrame,
    cell_size: float,
    max_pixels: float = 1e6,
) -> GeoDataFrame:
    """Tile a polygon's bbox into a grid of adjacent square blocks.

    Each block has at most `max_pixels` cells at the given `cell_size`
    (block side length is then `sqrt(max_pixels) * cell_size` in CRS
    units, with the tile-count rounded up). The output `GeoDataFrame`
    inherits its CRS from `poly_gdf`. When the bbox already fits the
    cap, a single tile covering the bbox is returned.

    Args:
        poly_gdf: A `GeoDataFrame` containing the polygon(s) whose
            bounding box is to be tiled. Its CRS units determine the
            interpretation of `cell_size` (metres for a UTM CRS,
            degrees for `EPSG:4326`).
        cell_size: Pixel size in the CRS units.
        max_pixels: Maximum cells per output block. Defaults to `1e6`.

    Returns:
        A `GeoDataFrame` of bounding-box `Polygon`s tiling the input
        bbox, in row-major order (south-to-north, west-to-east).

    Raises:
        ValueError: If `cell_size <= 0` or `max_pixels <= 0`.

    Examples:
        - Tile a 0.5°-wide WGS84 bbox at a tight cap:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.spatial import grid_polygon
            >>> poly = Polygon([(31.0, 30.0), (31.5, 30.0), (31.5, 30.5), (31.0, 30.5)])
            >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
            >>> tiles = grid_polygon(gdf, cell_size=0.01, max_pixels=100)
            >>> len(tiles) >= 4
            True
            >>> str(tiles.crs).upper()
            'EPSG:4326'

            ```
    """
    if cell_size <= 0:
        raise ValueError(f"cell_size must be positive, got {cell_size}")
    if max_pixels <= 0:
        raise ValueError(f"max_pixels must be positive, got {max_pixels}")

    xmin, ymin, xmax, ymax = poly_gdf.total_bounds
    epsg = poly_gdf.crs.to_epsg() if poly_gdf.crs is not None else None

    total_area = (xmax - xmin) * (ymax - ymin)
    cell_area = cell_size * cell_size
    no_cells = math.ceil(total_area / cell_area)

    if no_cells <= max_pixels:
        cols = rows = 1
        blocks_size = max(xmax - xmin, ymax - ymin)
    else:
        blocks_size = math.sqrt(max_pixels * cell_area)
        cols = int(math.ceil((xmax - xmin) / blocks_size))
        rows = int(math.ceil((ymax - ymin) / blocks_size))

    polys: list[Polygon] = []
    for i in range(rows):
        y0 = ymin + blocks_size * i
        y1 = y0 + blocks_size
        for j in range(cols):
            x0 = xmin + blocks_size * j
            x1 = x0 + blocks_size
            polys.append(Polygon([(x0, y0), (x0, y1), (x1, y1), (x1, y0), (x0, y0)]))

    crs_arg = f"EPSG:{epsg}" if epsg is not None else poly_gdf.crs
    return gpd.GeoDataFrame(geometry=polys, crs=crs_arg).reset_index(drop=True)


def count_cells_polygon(
    poly_gdf: GeoDataFrame,
    cell_size: float,
    area_factor: float = 1.0,
) -> tuple[int, GeoDataFrame]:
    """Estimate the pixel count for a polygon at a given `cell_size`.

    Projects the input to its UTM zone (via :func:`earthlens.utm.project_to_utm`)
    so `cell_size` can be interpreted in metres, then computes
    `ceil((bbox_area * area_factor) / cell_size**2)`.

    Args:
        poly_gdf: A `GeoDataFrame` in WGS84 (`EPSG:4326`) or any CRS
            that :func:`earthlens.utm.project_to_utm` accepts.
        cell_size: Pixel size in metres.
        area_factor: Multiplier applied to the bbox area before
            dividing by `cell_size**2`. Defaults to `1.0`.

    Returns:
        `(no_cells, projected_gdf)` — the cell count and the
        UTM-projected `GeoDataFrame`.

    Examples:
        - Small WGS84 polygon at 30 m:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.spatial import count_cells_polygon
            >>> poly = Polygon([(31.0, 30.0), (31.01, 30.0), (31.01, 30.01), (31.0, 30.01)])
            >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
            >>> n_cells, projected = count_cells_polygon(gdf, cell_size=30)
            >>> n_cells > 0
            True
            >>> projected.crs.to_epsg() == 32636
            True

            ```
    """
    projected, _ = project_to_utm(poly_gdf)
    xmin, ymin, xmax, ymax = projected.total_bounds
    total_area = (xmax - xmin) * (ymax - ymin) * area_factor
    cell_area = cell_size * cell_size
    return int(math.ceil(total_area / cell_area)), projected


def convex_hull_from_points(
    geo_df: GeoDataFrame,
) -> tuple[GeoDataFrame, BaseGeometry]:
    """Return the convex hull of a points `GeoDataFrame` as `(gdf, geom)`.

    The points are unioned into a Shapely `MultiPoint` and `.convex_hull`
    is computed; the polygon is wrapped in a single-row `GeoDataFrame`
    with the same CRS as the input, and the underlying point union is
    returned alongside for re-use.

    Args:
        geo_df: A `GeoDataFrame` of point geometries (any CRS).

    Returns:
        `(hull_gdf, points_union)` — a one-row `GeoDataFrame` holding
        the convex-hull polygon, plus the unioned points geometry.

    Examples:
        - Convex hull of three points:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Point
            >>> from earthlens.spatial import convex_hull_from_points
            >>> pts = gpd.GeoDataFrame(
            ...     {"id": [0, 1, 2]},
            ...     geometry=[Point(0, 0), Point(2, 0), Point(1, 2)],
            ...     crs="EPSG:4326",
            ... )
            >>> hull_gdf, union = convex_hull_from_points(pts)
            >>> hull_gdf.iloc[0].geometry.geom_type
            'Polygon'

            ```
    """
    points_union = unary_union(geo_df.geometry)
    hull = points_union.convex_hull
    hull_gdf = gpd.GeoDataFrame(geometry=[hull], crs=geo_df.crs)
    return hull_gdf, points_union


def count_cells_convex_hull_from_points(
    geo_df: GeoDataFrame,
    cell_size: float,
    area_factor: float = 1.0,
) -> int:
    """Estimate the pixel count for the convex hull of a points GDF.

    Convenience: composes :func:`convex_hull_from_points` and
    :func:`count_cells_polygon`.

    Args:
        geo_df: A `GeoDataFrame` of point geometries.
        cell_size: Pixel size in metres.
        area_factor: Bbox-area multiplier. Defaults to `1.0`.

    Returns:
        The estimated cell count over the points' convex hull.

    Examples:
        - Pixel count for the convex hull of three WGS84 points:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Point
            >>> from earthlens.spatial import count_cells_convex_hull_from_points
            >>> pts = gpd.GeoDataFrame(
            ...     geometry=[Point(31.0, 30.0), Point(31.01, 30.0), Point(31.005, 30.01)],
            ...     crs="EPSG:4326",
            ... )
            >>> count_cells_convex_hull_from_points(pts, cell_size=30) > 0
            True

            ```
    """
    hull_gdf, _ = convex_hull_from_points(geo_df)
    n_cells, _ = count_cells_polygon(hull_gdf, cell_size, area_factor=area_factor)
    return n_cells


def union_of_convex_hulls(gdf: GeoDataFrame) -> BaseGeometry:
    """Return the union of every row's `.convex_hull`.

    Pure-Shapely — the callers that need an `ee.Geometry` wrap this
    result on their side (this module has no Earth Engine dependency).

    Args:
        gdf: A `GeoDataFrame` whose per-row geometries each support
            `.convex_hull`.

    Returns:
        The single Shapely geometry formed by unioning every row's
        convex hull.

    Examples:
        - Union of two disjoint squares' hulls is a `MultiPolygon`:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.spatial import union_of_convex_hulls
            >>> a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
            >>> b = Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])
            >>> gdf = gpd.GeoDataFrame(geometry=[a, b], crs="EPSG:4326")
            >>> u = union_of_convex_hulls(gdf)
            >>> u.geom_type
            'MultiPolygon'

            ```
    """
    hulls = [geom.convex_hull for geom in gdf.geometry]
    return unary_union(hulls)


def split_polygon(
    poly_gdf: GeoDataFrame,
    cell_size: float,
    max_pixels: float = 1e7,
    area_factor: float = 1.0,
    project_to_map_epsg: bool = True,
    map_epsg: int | str | None = None,
) -> tuple[bool, GeoDataFrame | None]:
    """Split a polygon into a grid when its pixel count exceeds `max_pixels`.

    The "is it too big?" check is done in UTM (so `cell_size` reads as
    metres); the grid is then computed in UTM and optionally
    reprojected back to `map_epsg` (or, when unset, the input CRS).
    If the polygon already fits the budget, returns `(False, None)`.

    Args:
        poly_gdf: A `GeoDataFrame` containing one polygon. Its CRS
            must be set; non-WGS84 inputs are accepted.
        cell_size: Pixel size in metres.
        max_pixels: Maximum cells per output block. Defaults to `1e7`.
        area_factor: Bbox-area multiplier applied during the count.
            Defaults to `1.0`.
        project_to_map_epsg: If `True` (the default), reproject the
            resulting grid back to `map_epsg` (or `poly_gdf`'s CRS).
            If the reprojection fails (`CRSError`), the grid is left
            in WGS84.
        map_epsg: Target CRS for the returned grid; defaults to
            `poly_gdf.crs` if `None`.

    Returns:
        `(was_split, grid_gdf_or_None)` — `(False, None)` when the
        polygon already fits; `(True, grid)` when split.

    Examples:
        - Small polygon fits — no split, returns `(False, None)`:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.spatial import split_polygon
            >>> poly = Polygon([(31.0, 30.0), (31.05, 30.0), (31.05, 30.05), (31.0, 30.05)])
            >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
            >>> split_polygon(gdf, cell_size=30)
            (False, None)

            ```
        - Wide polygon at tight cap — split, grid stays in input CRS:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.spatial import split_polygon
            >>> poly = Polygon([(31.0, 30.0), (31.5, 30.0), (31.5, 30.5), (31.0, 30.5)])
            >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
            >>> was_split, grid = split_polygon(gdf, cell_size=30, max_pixels=1e5)
            >>> was_split
            True
            >>> str(grid.crs).upper()
            'EPSG:4326'

            ```
    """
    no_cells, projected = count_cells_polygon(
        poly_gdf, cell_size, area_factor=area_factor
    )
    if no_cells <= max_pixels:
        return False, None

    grid = grid_polygon(projected, cell_size, max_pixels=max_pixels)
    if project_to_map_epsg:
        target = map_epsg if map_epsg is not None else poly_gdf.crs
        try:
            grid = grid.to_crs(target)
        except CRSError:
            grid = grid.to_crs(_WGS84_CODE)
    return True, grid


def split_points(
    samples: GeoDataFrame,
    *,
    cell_size: float,
    min_points: int = 1,
    max_pixels: float = 1e7,
    output_crs: int | str | None = None,
) -> list[GeoDataFrame]:
    """Partition a points `GeoDataFrame` into spatially-coherent chunks.

    Computes the convex hull of the points, splits that hull into a
    grid via :func:`split_polygon`, and for each grid block claims all
    input points whose geometry it `covers`. Points on shared edges are
    dropped from later blocks so the returned chunks are disjoint.
    When `len(samples) < min_points`, falls back to a single chunk.

    Args:
        samples: A `GeoDataFrame` of point geometries.
        cell_size: Pixel size in metres (controls block sizing).
        min_points: If `samples` has fewer rows than this, return
            `[samples]` unchanged. Defaults to `1`.
        max_pixels: Maximum cells per output block. Defaults to `1e7`.
        output_crs: CRS each returned chunk should be in. Defaults to
            `samples.crs`.

    Returns:
        A list of `GeoDataFrame`s; their concatenation contains every
        input point exactly once.

    Examples:
        - A small points set returns a single chunk (below `min_points`):
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Point
            >>> from earthlens.spatial import split_points
            >>> pts = gpd.GeoDataFrame(
            ...     {"id": [0, 1]},
            ...     geometry=[Point(31.0, 30.0), Point(31.01, 30.0)],
            ...     crs="EPSG:4326",
            ... )
            >>> chunks = split_points(pts, cell_size=30, min_points=10)
            >>> len(chunks)
            1
            >>> len(chunks[0])
            2

            ```
    """
    if len(samples) < min_points:
        return [samples]

    hull_gdf, _ = convex_hull_from_points(samples)
    was_split, grid = split_polygon(
        hull_gdf, cell_size=cell_size, max_pixels=max_pixels,
    )
    if not was_split:
        return [samples]

    target_crs = output_crs if output_crs is not None else samples.crs
    grid_in_samples = grid.to_crs(samples.crs)

    claimed = pd.Series(False, index=samples.index)
    chunks: list[GeoDataFrame] = []
    for block in grid_in_samples.geometry:
        mask = samples.within(block) & ~claimed
        if mask.any():
            chunk = samples.loc[mask].copy()
            if target_crs is not None and str(samples.crs) != str(target_crs):
                chunk = chunk.to_crs(target_crs)
            chunks.append(chunk.reset_index(drop=True))
            claimed = claimed | mask

    if not claimed.all():
        leftover = samples.loc[~claimed].copy()
        if target_crs is not None and str(samples.crs) != str(target_crs):
            leftover = leftover.to_crs(target_crs)
        chunks.append(leftover.reset_index(drop=True))

    return chunks
