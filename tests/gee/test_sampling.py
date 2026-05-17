"""Tests for `earthlens.gee.sampling.sample_points` and the reducer whitelist."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import ee
import geopandas as gpd
import pytest
from shapely.geometry import Point

import geopandas as _gpd

from earthlens.gee import sampling
from earthlens.gee.sampling import (
    _REDUCER_WHITELIST,
    _resolve_reducer,
    sample_points,
    sample_points_to_gdf,
)


class _FakeFeatureCollection:
    """Stand-in for `ee.FeatureCollection` — records merges, ignores payload."""

    def __init__(self, payload=None):
        self.payload = payload
        self.merged_with: list = []

    def merge(self, other):
        self.merged_with.append(other)
        return self


class _FakeClipped:
    def __init__(self, recorder):
        self._recorder = recorder

    def reduceRegions(self, collection, reducer, scale):  # noqa: N802
        self._recorder["reduce_calls"].append(
            {"collection": collection, "reducer": reducer, "scale": scale}
        )
        return f"reduced<{scale}>"


class _FakeImage:
    def __init__(self):
        self.recorder = {"clip_bboxes": [], "reduce_calls": []}

    def clip(self, bbox):
        self.recorder["clip_bboxes"].append(bbox)
        return _FakeClipped(self.recorder)


def _fake_reducer_namespace():
    """Build a namespace mimicking `ee.Reducer` for every whitelisted name."""
    return SimpleNamespace(**{name: lambda n=name: f"reducer<{n}>" for name in _REDUCER_WHITELIST})


@pytest.fixture
def fake_ee(monkeypatch):
    """Patch the `ee` symbols `sample_points` touches so no EE init is needed."""
    monkeypatch.setattr(sampling.ee, "Reducer", _fake_reducer_namespace())
    monkeypatch.setattr(sampling.ee, "FeatureCollection", _FakeFeatureCollection)
    monkeypatch.setattr(
        sampling.ee,
        "Geometry",
        SimpleNamespace(BBox=lambda *args: ("bbox", args)),
    )
    # Isolate `sample_points` from `features.create_feature` (which would
    # otherwise need its own `ee.Geometry.Point` / `ee.Feature` mocks).
    monkeypatch.setattr(
        sampling, "create_feature", lambda gdf: ("fake_fc", tuple(gdf.index))
    )


def _points_gdf(n: int) -> gpd.GeoDataFrame:
    pts = [Point(i * 0.1, i * 0.1) for i in range(n)]
    return gpd.GeoDataFrame({"id": list(range(n)), "geometry": pts}, crs="EPSG:4326")


class TestResolveReducer:
    """Tests for the reducer-name whitelist (N1)."""

    def test_module_does_not_use_eval(self):
        """Anti-regression for N1 — no `eval(` in the sampling module source."""
        assert "eval(" not in inspect.getsource(sampling)

    def test_rejects_unknown_reducer(self):
        """Unknown reducer names raise `ValueError` listing the valid ones."""
        with pytest.raises(ValueError, match="unsupported reducer 'p95'"):
            _resolve_reducer("p95")

    def test_rejects_dunder_attribute(self):
        """Pre-N1 `eval` would have happily run `__class__`; the whitelist blocks it."""
        with pytest.raises(ValueError, match="unsupported reducer '__class__'"):
            _resolve_reducer("__class__")

    @pytest.mark.parametrize("name", sorted(_REDUCER_WHITELIST))
    def test_accepts_each_whitelisted_name(self, name, fake_ee):
        """Every whitelisted name is dispatched to `getattr(ee.Reducer, name)()`."""
        assert _resolve_reducer(name) == f"reducer<{name}>"


class TestSamplePoints:
    """Tests for `sample_points`."""

    def test_empty_gdf_raises(self):
        """An empty `GeoDataFrame` is rejected up-front, before any EE call."""
        empty = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        with pytest.raises(ValueError, match="non-empty GeoDataFrame"):
            sample_points(_FakeImage(), empty, scale_m=30)

    def test_polygon_geometry_rejected(self, fake_ee):
        """Non-point geometries raise `ValueError` naming the offending row (L4)."""
        from shapely.geometry import Polygon
        gdf = gpd.GeoDataFrame(
            {"id": [0, 1]},
            geometry=[
                Point(0, 0),
                Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
            ],
            crs="EPSG:4326",
        )
        with pytest.raises(ValueError, match="row 1 is a Polygon"):
            sample_points(_FakeImage(), gdf, scale_m=30)

    def test_multipoint_accepted(self, fake_ee):
        """`MultiPoint` rows pass the type guard (they have `.bounds`)."""
        from shapely.geometry import MultiPoint
        gdf = gpd.GeoDataFrame(
            {"id": [0]},
            geometry=[MultiPoint([(0, 0), (1, 1)])],
            crs="EPSG:4326",
        )
        # Should not raise.
        sample_points(_FakeImage(), gdf, scale_m=30)

    def test_rejects_unknown_reducer_before_ee_calls(self, fake_ee):
        """The reducer is validated up-front; no `clip` is issued on rejection."""
        gdf = _points_gdf(3)
        image = _FakeImage()
        with pytest.raises(ValueError, match="unsupported reducer 'bogus'"):
            sample_points(image, gdf, scale_m=30, reducer="bogus")
        assert image.recorder["clip_bboxes"] == []

    def test_issues_reduce_regions_per_rtree_leaf(self, fake_ee):
        """Each RTree leaf produces matched `clip` + `reduceRegions` calls."""
        gdf = _points_gdf(20)
        image = _FakeImage()
        result = sample_points(image, gdf, scale_m=42)

        n_clips = len(image.recorder["clip_bboxes"])
        n_reduces = len(image.recorder["reduce_calls"])

        assert n_clips == n_reduces >= 1
        assert {call["scale"] for call in image.recorder["reduce_calls"]} == {42}
        assert {call["reducer"] for call in image.recorder["reduce_calls"]} == {"reducer<first>"}
        assert isinstance(result, _FakeFeatureCollection)

    def test_named_reducer_threads_through_to_each_call(self, fake_ee):
        """A non-default reducer is resolved once and threaded to every reduceRegions."""
        gdf = _points_gdf(5)
        image = _FakeImage()
        sample_points(image, gdf, scale_m=10, reducer="mean")
        assert {call["reducer"] for call in image.recorder["reduce_calls"]} == {"reducer<mean>"}

    def test_returns_real_feature_collection_when_unpatched(self):
        """Without monkey-patching, the symbol resolves to the real `ee.FeatureCollection`.

        Just verifies wiring — the real EE call requires auth and lives in e2e.
        """
        assert sampling.ee.FeatureCollection is ee.FeatureCollection


class TestSamplePointsToGdf:
    """Tests for `sample_points_to_gdf` (L4 composition)."""

    def test_composes_sample_points_and_fc_to_gdf(self, fake_ee, monkeypatch):
        """The helper sample-points the image then routes the FC through `_fc_to_gdf`."""
        seen: dict = {}

        def _stub_fc_to_gdf(fc, *, crs=4326):
            seen["fc"] = fc
            seen["crs"] = crs
            return _gpd.GeoDataFrame({"x": [1]}, geometry=_gpd.points_from_xy([0], [0]), crs=f"EPSG:{crs}")

        monkeypatch.setattr(sampling, "_fc_to_gdf", _stub_fc_to_gdf)
        gdf = _points_gdf(5)
        out = sample_points_to_gdf(_FakeImage(), gdf, scale_m=30, reducer="mean", crs=4326)
        assert isinstance(out, _gpd.GeoDataFrame)
        assert seen["crs"] == 4326
        # The FC handed to `_fc_to_gdf` is the merged collection produced by
        # `sample_points` (a `_FakeFeatureCollection`).
        assert isinstance(seen["fc"], _FakeFeatureCollection)

    def test_propagates_reducer_whitelist_error(self):
        """`sample_points_to_gdf` rejects an unknown reducer up-front."""
        gdf = _points_gdf(2)
        with pytest.raises(ValueError, match="unsupported reducer 'bogus'"):
            sample_points_to_gdf(_FakeImage(), gdf, scale_m=30, reducer="bogus")
