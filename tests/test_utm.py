"""Tests for `earthlens.utm` (PY-1)."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from earthlens.utm import (
    project_to_utm,
    utm_epsg,
    utm_epsg_for_polygon,
    utm_zone,
)


class TestUtmZone:
    """Tests for `utm_zone`."""

    @pytest.mark.parametrize(
        "lon, lat, expected",
        [
            (-179.5, 0.0, 1),
            (-177.0, 0.0, 1),
            (-3.0, 0.0, 30),
            (3.0, 0.0, 31),
            (15.0, 0.0, 33),
            (31.25, 30.05, 36),
            (179.5, 0.0, 60),
        ],
    )
    def test_standard_formula(self, lon, lat, expected):
        """Outside Norway/Svalbard, `floor((lon+180)/6) + 1` applies."""
        assert utm_zone(lon, lat) == expected

    @pytest.mark.parametrize(
        "lon, lat, expected",
        [
            (3.5, 56.0, 32),
            (5.0, 60.0, 32),
            (8.99, 63.99, 32),
        ],
    )
    def test_norway_zone_32_exception(self, lon, lat, expected):
        """Within 56-64N / 3-12E the standard zone 31 is widened to 32."""
        assert utm_zone(lon, lat) == expected

    def test_norway_exception_bounds_are_half_open(self):
        """The lat/lon bounds for the Norway exception are `[lo, hi)`."""
        assert utm_zone(2.99, 60.0) == 31
        assert utm_zone(12.0, 60.0) == 33
        assert utm_zone(5.0, 55.99) == 31
        assert utm_zone(5.0, 64.0) == 31

    @pytest.mark.parametrize(
        "lon, lat, expected",
        [
            (5.0, 78.0, 31),
            (15.0, 78.0, 33),
            (25.0, 78.0, 35),
            (35.0, 78.0, 37),
        ],
    )
    def test_svalbard_skips_even_zones(self, lon, lat, expected):
        """Within 72-84N, zones 31/33/35/37 cover the whole arc."""
        assert utm_zone(lon, lat) == expected

    def test_svalbard_only_kicks_in_above_72n(self):
        """Below 72°N or at/above 84°N the Svalbard rules don't fire."""
        assert utm_zone(15.0, 71.0) == 33
        assert utm_zone(11.0, 71.0) == 32
        assert utm_zone(15.0, 85.0) == 33


class TestUtmEpsg:
    """Tests for `utm_epsg`."""

    def test_northern_hemisphere_uses_32600_base(self):
        """A north-of-equator lat picks `32600 + zone`."""
        assert utm_epsg(31.25, 30.05) == 32636

    def test_southern_hemisphere_uses_32700_base(self):
        """A south-of-equator lat picks `32700 + zone`."""
        assert utm_epsg(31.25, -25.0) == 32736

    def test_equator_uses_north(self):
        """`lat == 0` is treated as northern hemisphere."""
        assert utm_epsg(31.25, 0.0) == 32636


class TestUtmEpsgForPolygon:
    """Tests for `utm_epsg_for_polygon`."""

    def test_picks_centroid_zone(self):
        """The EPSG is derived from the bbox centroid of the input."""
        poly = Polygon(
            [(31.0, 30.0), (31.2, 30.0), (31.2, 30.2), (31.0, 30.2)]
        )
        gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
        assert utm_epsg_for_polygon(gdf) == 32636

    def test_reprojects_non_4326_input(self):
        """A non-WGS84 input is reprojected before the centroid is taken."""
        poly = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:3857")
        assert utm_epsg_for_polygon(gdf) == 32631

    def test_empty_input_raises(self):
        """An empty `GeoDataFrame` raises `ValueError`."""
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        with pytest.raises(ValueError, match="empty"):
            utm_epsg_for_polygon(empty)


class TestProjectToUtm:
    """Tests for `project_to_utm`."""

    def test_returns_projected_gdf_and_epsg(self):
        """The output GDF carries the new CRS and the EPSG matches."""
        poly = Polygon(
            [(31.0, 30.0), (31.2, 30.0), (31.2, 30.2), (31.0, 30.2)]
        )
        gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
        projected, epsg = project_to_utm(gdf)
        assert epsg == 32636
        assert projected.crs.to_epsg() == 32636

    def test_geometry_coords_are_in_metres(self):
        """Projected coords are metres; bbox width must be ~22 km for 0.2°."""
        poly = Polygon(
            [(31.0, 30.0), (31.2, 30.0), (31.2, 30.2), (31.0, 30.2)]
        )
        gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
        projected, _ = project_to_utm(gdf)
        xmin, _, xmax, _ = projected.total_bounds
        assert 19_000 < (xmax - xmin) < 24_000

    def test_empty_input_raises(self):
        """An empty input propagates the `ValueError` from `utm_epsg_for_polygon`."""
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        with pytest.raises(ValueError, match="empty"):
            project_to_utm(empty)
