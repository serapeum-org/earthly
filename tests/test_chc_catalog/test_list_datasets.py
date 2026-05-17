"""Lock-in for the H2 list_datasets() region-validation behaviour."""

from __future__ import annotations

import pytest

from earthlens.chc import Catalog

pytestmark = [pytest.mark.chc]


_AFRICA_KEYS: list[str] = [
    "africa-2-monthly",
    "africa-3-monthly",
    "africa-6-hourly",
    "africa-daily",
    "africa-dekad",
    "africa-monthly",
    "africa-pentad",
]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    """Bundled catalog, loaded once per module."""
    return Catalog()


class TestListDatasets:
    """`Catalog.list_datasets` filters by region / temporal_resolution and validates region."""

    def test_no_args_returns_every_dataset_sorted(self, catalog: Catalog):
        """list_datasets() with no filter returns all 97 dataset keys, sorted."""
        result = catalog.list_datasets()
        assert result == sorted(catalog.datasets)
        assert len(result) == 97

    def test_region_africa_returns_known_africa_entries(self, catalog: Catalog):
        """The seven africa-* entries are returned for region='africa'."""
        result = catalog.list_datasets(region="africa")
        assert result == _AFRICA_KEYS

    def test_region_typo_raises_value_error(self, catalog: Catalog):
        """A typo ('indonsia' for 'indonesia') raises ValueError naming the typo and known regions."""
        with pytest.raises(ValueError, match=r"indonsia") as exc:
            catalog.list_datasets(region="indonsia")
        message = str(exc.value)
        assert "regions:" in message
        assert "indonesia" in message  # listed among the available regions
        assert "global" in message

    def test_region_unknown_raises_value_error(self, catalog: Catalog):
        """An arbitrary unknown region also raises (validation isn't typo-specific)."""
        with pytest.raises(ValueError, match=r"not-a-region"):
            catalog.list_datasets(region="not-a-region")

    def test_temporal_resolution_daily_filter(self, catalog: Catalog):
        """daily filter returns a sorted non-empty list containing daily keys only."""
        result = catalog.list_datasets(temporal_resolution="daily")
        assert result == sorted(result)
        assert "global-daily" in result
        assert "global-monthly" not in result
        assert len(result) > 0

    def test_combined_region_and_temporal_resolution_filter(self, catalog: Catalog):
        """Filtering by both region and temporal_resolution intersects the matches."""
        result = catalog.list_datasets(region="africa", temporal_resolution="daily")
        assert result == ["africa-daily"]

    def test_unknown_temporal_resolution_raises_value_error(self, catalog: Catalog):
        """M1 tightened temporal_resolution to a known vocabulary; typos now raise."""
        with pytest.raises(ValueError, match=r"not-a-resolution"):
            catalog.list_datasets(temporal_resolution="not-a-resolution")
