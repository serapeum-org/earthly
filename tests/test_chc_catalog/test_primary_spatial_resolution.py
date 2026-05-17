"""Lock-in for the L4 `Dataset.primary_spatial_resolution` convenience property."""

from __future__ import annotations

import pytest

from earthlens.chc import Catalog

pytestmark = [pytest.mark.chc]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    """Bundled catalog, loaded once per module."""
    return Catalog()


class TestPrimarySpatialResolution:
    """The L4 convenience property returns the first element of `spatial_resolution`."""

    def test_returns_first_element_for_every_dataset(self, catalog: Catalog):
        """For every dataset, primary == spatial_resolution[0]."""
        mismatches = [
            (key, ds.primary_spatial_resolution, ds.spatial_resolution[0])
            for key, ds in catalog.datasets.items()
            if ds.primary_spatial_resolution != ds.spatial_resolution[0]
        ]
        assert not mismatches, mismatches

    def test_returns_float(self, catalog: Catalog):
        """The property is typed as a float and returns one."""
        ds = catalog.get_dataset("global-daily")
        value = ds.primary_spatial_resolution
        assert isinstance(value, float)
        assert value == 0.05

    def test_named_examples_pin_known_values(self, catalog: Catalog):
        """Pin the property's value on three datasets with different pixel sizes."""
        assert catalog.get_dataset("global-daily").primary_spatial_resolution == 0.05
        assert catalog.get_dataset("wbgt-monthly").primary_spatial_resolution == 1.0
        assert (
            catalog.get_dataset(
                "chc-cmip6-precip-daily-delta-2030-ssp245"
            ).primary_spatial_resolution
            == 0.1
        )

    def test_property_does_not_mutate_underlying_list(self, catalog: Catalog):
        """Accessing the property must not modify `spatial_resolution`."""
        ds = catalog.get_dataset("global-daily")
        before = list(ds.spatial_resolution)
        _ = ds.primary_spatial_resolution
        assert list(ds.spatial_resolution) == before
