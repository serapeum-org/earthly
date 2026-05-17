"""Tests for `earthlens.gee.filters` (L1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from earthlens.gee import filters
from earthlens.gee.filters import (
    by_bounds,
    by_cloud_cover_lte,
    by_property_in,
    by_year,
    by_year_and_bounds,
)


class _FakeCollection:
    """Stand-in for an `ee.ImageCollection`; records filter calls."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def _chain(self, name: str, *args) -> "_FakeCollection":
        out = _FakeCollection()
        out.calls = self.calls + [(name, args)]
        return out

    def filterDate(self, start, end):  # noqa: N802
        return self._chain("filterDate", start, end)

    def filterBounds(self, region):  # noqa: N802
        return self._chain("filterBounds", region)

    def filter(self, filt):
        return self._chain("filter", filt)


@pytest.fixture
def fake_ee(monkeypatch):
    """Patch `ee.Filter` so tests don't need an initialised SDK."""
    fake_filter = SimpleNamespace(
        inList=lambda name, values: ("inList", name, tuple(values)),
        lte=lambda name, value: ("lte", name, value),
    )
    monkeypatch.setattr(filters.ee, "Filter", fake_filter)


class TestByYear:
    """Tests for `by_year`."""

    def test_uses_year_bounds(self):
        """`by_year(c, 2024)` issues `filterDate("2024-01-01", "2025-01-01")`."""
        out = by_year(_FakeCollection(), 2024)
        assert out.calls == [("filterDate", ("2024-01-01", "2025-01-01"))]


class TestByBounds:
    """Tests for `by_bounds`."""

    def test_passes_region_to_filter_bounds(self):
        """`by_bounds(c, region)` calls `c.filterBounds(region)`."""
        region = object()
        out = by_bounds(_FakeCollection(), region)
        assert out.calls == [("filterBounds", (region,))]


class TestByPropertyIn:
    """Tests for `by_property_in`."""

    def test_builds_in_list_filter(self, fake_ee):
        """The helper composes `ee.Filter.inList(name, [...values...])`."""
        out = by_property_in(_FakeCollection(), "WRS_PATH", [120, 121, 122])
        assert out.calls == [("filter", (("inList", "WRS_PATH", (120, 121, 122)),))]


class TestByCloudCoverLte:
    """Tests for `by_cloud_cover_lte`."""

    def test_default_property_is_cloud_cover(self, fake_ee):
        """Default `property_name` is the Landsat C2 spelling `CLOUD_COVER`."""
        out = by_cloud_cover_lte(_FakeCollection(), 20)
        assert out.calls == [("filter", (("lte", "CLOUD_COVER", 20),))]

    def test_property_name_overridable(self, fake_ee):
        """Explicit `property_name=` is forwarded — e.g. Sentinel-2."""
        out = by_cloud_cover_lte(
            _FakeCollection(), 10, property_name="CLOUDY_PIXEL_PERCENTAGE",
        )
        assert out.calls == [("filter", (("lte", "CLOUDY_PIXEL_PERCENTAGE", 10),))]

    @pytest.mark.parametrize("pct", [-1, 100.1, 150])
    def test_out_of_range_pct_rejected(self, pct):
        """`max_pct` outside [0, 100] is rejected with `ValueError`."""
        with pytest.raises(ValueError, match="max_pct must be in"):
            by_cloud_cover_lte(_FakeCollection(), pct)


class TestByYearAndBounds:
    """Tests for `by_year_and_bounds`."""

    def test_year_only_when_region_is_none(self):
        """`region=None` returns the year-filtered collection without bounds."""
        out = by_year_and_bounds(_FakeCollection(), 2024)
        assert out.calls == [("filterDate", ("2024-01-01", "2025-01-01"))]

    def test_composes_year_then_bounds(self):
        """With both, the result has filterDate then filterBounds, in that order."""
        region = object()
        out = by_year_and_bounds(_FakeCollection(), 2024, region)
        assert out.calls == [
            ("filterDate", ("2024-01-01", "2025-01-01")),
            ("filterBounds", (region,)),
        ]
