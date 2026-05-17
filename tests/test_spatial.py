"""Tests for `earthlens.spatial` (PY-2)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import MultiPoint, Point, Polygon

from earthlens.spatial import (
    convex_hull_from_points,
    count_cells_convex_hull_from_points,
    count_cells_polygon,
    grid_polygon,
    split_points,
    split_polygon,
    union_of_convex_hulls,
)


def _wgs84_box(west: float, south: float, east: float, north: float) -> gpd.GeoDataFrame:
    poly = Polygon(
        [(west, south), (east, south), (east, north), (west, north), (west, south)]
    )
    return gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")


class TestGridPolygon:
    """Tests for `grid_polygon`."""

    def test_small_bbox_within_cap_returns_single_tile(self):
        """A bbox that fits the per-block cap is emitted as one tile."""
        gdf = _wgs84_box(31.0, 30.0, 31.05, 30.05)
        tiles = grid_polygon(gdf, cell_size=0.01, max_pixels=1e6)
        assert len(tiles) == 1
        assert str(tiles.crs).upper() == "EPSG:4326"

    def test_tile_count_grows_when_cap_tightens(self):
        """Lowering `max_pixels` past the bbox's cell-count yields more tiles."""
        gdf = _wgs84_box(31.0, 30.0, 31.5, 30.5)
        loose = grid_polygon(gdf, cell_size=0.01, max_pixels=1e6)
        tight = grid_polygon(gdf, cell_size=0.01, max_pixels=1e2)
        assert len(loose) == 1
        assert len(tight) > 1

    def test_tiles_cover_at_least_the_input_bbox(self):
        """The union of tile bboxes covers the input bbox."""
        gdf = _wgs84_box(31.0, 30.0, 31.5, 30.5)
        tiles = grid_polygon(gdf, cell_size=0.01, max_pixels=1e3)
        bbox = gdf.total_bounds
        union = tiles.geometry.union_all()
        assert union.bounds[0] <= bbox[0] + 1e-9
        assert union.bounds[1] <= bbox[1] + 1e-9
        assert union.bounds[2] >= bbox[2] - 1e-9
        assert union.bounds[3] >= bbox[3] - 1e-9

    @pytest.mark.parametrize("cell_size, max_pixels", [(0, 100), (-1, 100), (10, 0), (10, -5)])
    def test_non_positive_args_raise(self, cell_size, max_pixels):
        """`cell_size` and `max_pixels` must both be positive."""
        gdf = _wgs84_box(31.0, 30.0, 31.05, 30.05)
        with pytest.raises(ValueError, match="must be positive"):
            grid_polygon(gdf, cell_size=cell_size, max_pixels=max_pixels)


class TestCountCellsPolygon:
    """Tests for `count_cells_polygon`."""

    def test_returns_positive_count_and_utm_projection(self):
        """A small WGS84 box returns a positive count and a UTM-projected GDF."""
        gdf = _wgs84_box(31.0, 30.0, 31.01, 30.01)
        n_cells, projected = count_cells_polygon(gdf, cell_size=30)
        assert n_cells > 0
        assert projected.crs.to_epsg() == 32636

    def test_count_scales_with_area_factor(self):
        """`area_factor=2` exactly doubles the cell count (mod ceil)."""
        gdf = _wgs84_box(31.0, 30.0, 31.05, 30.05)
        n1, _ = count_cells_polygon(gdf, cell_size=30, area_factor=1.0)
        n2, _ = count_cells_polygon(gdf, cell_size=30, area_factor=2.0)
        assert abs(n2 - 2 * n1) <= 2


class TestConvexHullFromPoints:
    """Tests for `convex_hull_from_points`."""

    def test_three_non_colinear_points_yield_a_triangle(self):
        """Three points form a triangular hull."""
        pts = gpd.GeoDataFrame(
            {"id": [0, 1, 2]},
            geometry=[Point(0, 0), Point(2, 0), Point(1, 2)],
            crs="EPSG:4326",
        )
        hull_gdf, union = convex_hull_from_points(pts)
        assert hull_gdf.iloc[0].geometry.geom_type == "Polygon"
        assert len(hull_gdf) == 1
        assert isinstance(union, MultiPoint)

    def test_hull_crs_matches_input(self):
        """The hull GDF inherits its input's CRS."""
        pts = gpd.GeoDataFrame(
            geometry=[Point(0, 0), Point(1, 1), Point(0, 1)],
            crs="EPSG:3857",
        )
        hull_gdf, _ = convex_hull_from_points(pts)
        assert str(hull_gdf.crs).upper() == "EPSG:3857"


class TestCountCellsConvexHullFromPoints:
    """Tests for `count_cells_convex_hull_from_points`."""

    def test_returns_positive_count(self):
        """A non-trivial points set yields a positive cell count."""
        pts = gpd.GeoDataFrame(
            geometry=[Point(31.0, 30.0), Point(31.01, 30.0), Point(31.005, 30.01)],
            crs="EPSG:4326",
        )
        assert count_cells_convex_hull_from_points(pts, cell_size=30) > 0


class TestUnionOfConvexHulls:
    """Tests for `union_of_convex_hulls`."""

    def test_disjoint_polygons_yield_a_multipolygon(self):
        """Two non-overlapping squares' hulls union to a `MultiPolygon`."""
        a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        b = Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])
        gdf = gpd.GeoDataFrame(geometry=[a, b], crs="EPSG:4326")
        out = union_of_convex_hulls(gdf)
        assert out.geom_type == "MultiPolygon"

    def test_overlapping_polygons_yield_a_single_polygon(self):
        """Overlapping hulls union to a single polygon."""
        a = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
        b = Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])
        gdf = gpd.GeoDataFrame(geometry=[a, b], crs="EPSG:4326")
        out = union_of_convex_hulls(gdf)
        assert out.geom_type == "Polygon"


class TestSplitPolygon:
    """Tests for `split_polygon`."""

    def test_small_polygon_returns_false_none(self):
        """A polygon below `max_pixels` returns `(False, None)`."""
        gdf = _wgs84_box(31.0, 30.0, 31.05, 30.05)
        assert split_polygon(gdf, cell_size=30) == (False, None)

    def test_oversized_polygon_returns_grid_in_input_crs(self):
        """When split, the grid is reprojected back to the input CRS by default."""
        gdf = _wgs84_box(31.0, 30.0, 31.5, 30.5)
        was_split, grid = split_polygon(gdf, cell_size=30, max_pixels=1e5)
        assert was_split is True
        assert grid is not None and len(grid) > 1
        assert str(grid.crs).upper() == "EPSG:4326"

    def test_project_to_map_epsg_false_keeps_utm(self):
        """`project_to_map_epsg=False` leaves the grid in the UTM CRS."""
        gdf = _wgs84_box(31.0, 30.0, 31.5, 30.5)
        _, grid = split_polygon(
            gdf, cell_size=30, max_pixels=1e5, project_to_map_epsg=False,
        )
        assert grid.crs.to_epsg() == 32636

    def test_explicit_map_epsg_is_honoured(self):
        """`map_epsg=` overrides the input CRS for the reprojection target."""
        gdf = _wgs84_box(31.0, 30.0, 31.5, 30.5)
        _, grid = split_polygon(
            gdf, cell_size=30, max_pixels=1e5, map_epsg="EPSG:3857",
        )
        assert grid.crs.to_epsg() == 3857


class TestSplitPoints:
    """Tests for `split_points`."""

    def test_below_min_points_returns_single_chunk_unchanged(self):
        """When the input has fewer rows than `min_points`, return `[input]`."""
        pts = gpd.GeoDataFrame(
            {"id": [0, 1]},
            geometry=[Point(31.0, 30.0), Point(31.01, 30.0)],
            crs="EPSG:4326",
        )
        chunks = split_points(pts, cell_size=30, min_points=10)
        assert len(chunks) == 1 and len(chunks[0]) == 2

    def test_when_hull_fits_returns_single_chunk(self):
        """If the convex hull fits the cap, all points come back as one chunk."""
        pts = gpd.GeoDataFrame(
            {"id": list(range(5))},
            geometry=[Point(31.0 + i * 0.001, 30.0) for i in range(5)],
            crs="EPSG:4326",
        )
        chunks = split_points(pts, cell_size=30)
        assert len(chunks) == 1 and len(chunks[0]) == 5

    def test_oversized_hull_yields_chunks_covering_all_points(self):
        """When split, the per-block chunks' total point count equals the input."""
        pts = gpd.GeoDataFrame(
            {"id": list(range(50))},
            geometry=[Point(31.0 + i * 0.01, 30.0 + (i % 5) * 0.01) for i in range(50)],
            crs="EPSG:4326",
        )
        chunks = split_points(pts, cell_size=30, max_pixels=1e5, min_points=10)
        total = sum(len(c) for c in chunks)
        assert total == len(pts)
