"""Tests for `earthlens.gee.features` — Shapely / GeoDataFrame → Earth Engine.

`ee` is faked via ``monkeypatch`` with a recorder so no Earth Engine
calls are made; real Shapely geometries and a real `GeoDataFrame` are
used (geopandas is a hard dependency).
"""

from __future__ import annotations

from types import SimpleNamespace

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon

from earthlens.gee import features as features_module
from earthlens.gee.features import createFeature, createGeometry

_SQUARE = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
_SQUARE_2 = Polygon([(2, 2), (3, 2), (3, 3), (2, 3)])


class _FakeGeometry:
    """Records the constructor used and the args (stands in for `ee.Geometry.*`)."""

    def __init__(self, kind: str, coords, crs):
        self.kind = kind
        self.coords = coords
        self.crs = crs


class _FakeFeature:
    """Stands in for `ee.Feature(geometry[, properties])`."""

    def __init__(self, geometry, properties=None):
        self.geometry = geometry
        self.properties = properties


class _FakeFeatureCollection:
    """Stands in for `ee.FeatureCollection([Feature, ...])`."""

    def __init__(self, features):
        self.features = list(features)


@pytest.fixture(scope="function")
def fake_ee(monkeypatch):
    """Replace `earthlens.gee.features.ee` with a recording fake.

    Returns:
        The fake `ee` namespace (its `Geometry.Polygon`/`Geometry.Point`
        construct `_FakeGeometry`, `Feature` → `_FakeFeature`,
        `FeatureCollection` → `_FakeFeatureCollection`).
    """
    fake = SimpleNamespace(
        Geometry=SimpleNamespace(
            Polygon=lambda coords, crs=None: _FakeGeometry("Polygon", coords, crs),
            Point=lambda coords, crs=None: _FakeGeometry("Point", coords, crs),
        ),
        Feature=lambda geometry, properties=None: _FakeFeature(geometry, properties),
        FeatureCollection=lambda features: _FakeFeatureCollection(features),
    )
    monkeypatch.setattr(features_module, "ee", fake)
    return fake


class TestCreateGeometry:
    """Tests for `createGeometry`."""

    def test_polygon(self, fake_ee):
        """A Shapely `Polygon` becomes an `ee.Geometry.Polygon` with the EPSG."""
        geom = createGeometry(_SQUARE)
        assert geom.kind == "Polygon"
        assert geom.crs == "epsg:4326"
        assert geom.coords == _SQUARE.__geo_interface__["coordinates"]

    def test_polygon_custom_epsg(self, fake_ee):
        """A non-default `epsg` is forwarded to `ee.Geometry.Polygon`."""
        assert createGeometry(_SQUARE, epsg=3857).crs == "epsg:3857"

    def test_point(self, fake_ee):
        """A Shapely `Point` becomes an `ee.Geometry.Point`."""
        geom = createGeometry(Point(3, 4))
        assert geom.kind == "Point"
        assert geom.coords == Point(3, 4).__geo_interface__["coordinates"]

    def test_linestring_not_implemented(self, fake_ee):
        """A `LineString` raises `NotImplementedError` (not yet supported)."""
        with pytest.raises(NotImplementedError, match="LineString geometries"):
            createGeometry(LineString([(0, 0), (1, 1)]))

    def test_unsupported_type_raises_not_implemented(self, fake_ee):
        """An unsupported geometry type (e.g. `MultiPolygon`) raises `NotImplementedError`."""
        with pytest.raises(NotImplementedError, match="MultiPolygon geometries"):
            createGeometry(MultiPolygon([_SQUARE, _SQUARE_2]))


class TestCreateFeature:
    """Tests for `createFeature`."""

    def test_polygons_with_properties(self, fake_ee):
        """Each row becomes an `ee.Feature(geometry, {col: value, ...})`."""
        gdf = gpd.GeoDataFrame(
            {"name": ["a", "b"], "value": [1, 2], "geometry": [_SQUARE, _SQUARE_2]},
            crs="EPSG:4326",
        )
        fc = createFeature(gdf)
        assert isinstance(fc, _FakeFeatureCollection)
        assert len(fc.features) == 2
        assert fc.features[0].properties == {"name": "a", "value": 1}
        assert fc.features[1].properties == {"name": "b", "value": 2}

    def test_columns_subset(self, fake_ee):
        """Only the requested `columns` end up as feature properties."""
        gdf = gpd.GeoDataFrame(
            {"name": ["a"], "value": [1], "geometry": [_SQUARE]}, crs="EPSG:4326"
        )
        fc = createFeature(gdf, columns=["name"])
        assert fc.features[0].properties == {"name": "a"}

    def test_no_columns_yields_features_without_properties(self, fake_ee):
        """A geometry-only GeoDataFrame yields features with no properties."""
        gdf = gpd.GeoDataFrame({"geometry": [_SQUARE, _SQUARE_2]}, crs="EPSG:4326")
        fc = createFeature(gdf)
        assert len(fc.features) == 2
        assert all(f.properties is None for f in fc.features)

    def test_multipolygon_is_exploded(self, fake_ee):
        """A `MultiPolygon` row is exploded into one feature per part."""
        gdf = gpd.GeoDataFrame(
            {"name": ["both"], "geometry": [MultiPolygon([_SQUARE, _SQUARE_2])]},
            crs="EPSG:4326",
        )
        fc = createFeature(gdf)
        assert len(fc.features) == 2
        assert all(g.kind == "Polygon" for g in (f.geometry for f in fc.features))

    def test_linestring_row_raises_valueerror(self, fake_ee):
        """A row whose geometry can't be converted surfaces as `ValueError`."""
        gdf = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326"
        )
        with pytest.raises(ValueError):
            createFeature(gdf)

    def test_unsupported_geometry_raises_locally(self, fake_ee):
        """`MultiPoint` (and other unsupported types) raise locally, not at EE (M2)."""
        gdf = gpd.GeoDataFrame(
            {
                "name": ["ok", "bad"],
                "geometry": [_SQUARE, MultiPoint([(0, 0), (1, 1)])],
            },
            crs="EPSG:4326",
        )
        with pytest.raises(ValueError, match="row 1 .MultiPoint."):
            createFeature(gdf)
