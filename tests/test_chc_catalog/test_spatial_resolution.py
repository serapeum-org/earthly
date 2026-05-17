"""Lock-in for the over-promised-resolution invariant after the C3 fix."""

from __future__ import annotations

import pytest

from earthlens.chc import Catalog

pytestmark = [pytest.mark.chc]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    """Bundled catalog, loaded once per module via the disk-mtime cache."""
    return Catalog()


class TestSpatialResolution:
    """Contract: `spatial_resolution` only advertises what `ftp_bases` can reach."""

    def test_no_dataset_over_promises_resolutions(self, catalog: Catalog):
        """For every dataset, len(spatial_resolution) <= len(ftp_bases)."""
        offenders = [
            (key, ds.spatial_resolution, sorted(ds.ftp_bases))
            for key, ds in catalog.datasets.items()
            if len(ds.spatial_resolution) > len(ds.ftp_bases)
        ]
        assert not offenders, (
            f"datasets advertising more resolutions than addressable formats "
            f"(C3 regression): {offenders}"
        )

    def test_global_daily_named_example(self, catalog: Catalog):
        """global-daily was the only over-promising row pre-C3; pin its trimmed value."""
        ds = catalog.get_dataset("global-daily")
        assert ds.spatial_resolution == [0.05]
        assert set(ds.ftp_bases) == {"tif"}

    def test_every_spatial_resolution_is_non_empty_positive_floats(self, catalog: Catalog):
        """Every value in `spatial_resolution` is a real, strictly-positive pixel size."""
        bad: list[tuple[str, list[float]]] = []
        for key, ds in catalog.datasets.items():
            values = ds.spatial_resolution
            if not values:
                bad.append((key, values))
                continue
            if any(not isinstance(v, float) or v <= 0.0 for v in values):
                bad.append((key, values))
        assert not bad, (
            f"datasets with empty or non-positive spatial_resolution: {bad}"
        )
