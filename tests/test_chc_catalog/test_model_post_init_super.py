"""Lock-in for M7: `Catalog.model_post_init` calls `super().model_post_init`."""

from __future__ import annotations

import pytest

from earthlens.chc import Catalog
from earthlens.chc.catalog import clear_catalog_cache

pytestmark = [pytest.mark.chc]


class TestModelPostInitSuper:
    """`Catalog.model_post_init` honours the `AbstractCatalog` contract (M7)."""

    def test_auto_load_path_populates_self_catalog(self):
        """`Catalog()` (no args) sets `self.catalog` to the same dict as `self.datasets`."""
        clear_catalog_cache()
        cat = Catalog()
        assert cat.catalog is cat.datasets, (
            "AbstractCatalog.model_post_init must set `self.catalog = self.get_catalog()`, "
            "and `get_catalog()` returns the same dict object as `self.datasets`."
        )
        assert len(cat.catalog) == len(cat.datasets) > 0

    def test_pre_seeded_path_also_populates_self_catalog(self, tmp_path):
        """`Catalog(datasets=...)` (test path) still routes through super and populates `catalog`."""
        # Build a one-dataset catalog by routing through the loader (the only path that
        # produces validated Dataset objects without us hand-constructing them).
        catalog_yaml = tmp_path / "catalog.yaml"
        catalog_yaml.write_text(
            "available_datasets:\n  - synth\n"
            "regions:\n  global:\n    lat_boundaries: [-50, 50]\n    lon_boundaries: [-180, 180]\n"
            "datasets:\n"
            "  synth:\n"
            "    ftp_bases: {tif: 'pub/foo/'}\n"
            "    file_patterns: {tif: '{year}/foo.{year}.tif'}\n"
            "    region: global\n"
            "    temporal_resolution: daily\n"
            "    pandas_freq: D\n"
            "    spatial_resolution: [0.05]\n"
            "    formats: [tif]\n"
            "    start_date: '2020-01-01'\n"
            "    variables:\n"
            "      precipitation:\n"
            "        description: synthetic\n"
            "        units: mm/day\n"
            "        types: flux\n",
            encoding="utf-8",
        )
        clear_catalog_cache()
        loaded = Catalog.load(catalog_path=catalog_yaml)
        # Constructing a SECOND `Catalog` with `datasets=` from the first should
        # still get `self.catalog == self.datasets` via the super call (the
        # `if not self.datasets:` branch is skipped, but super still runs).
        passthrough = Catalog(
            datasets=loaded.datasets,
            available_datasets=loaded.available_datasets,
            available_regions=loaded.available_regions,
        )
        assert passthrough.catalog is passthrough.datasets
        assert "synth" in passthrough.catalog

    def test_self_catalog_is_not_an_empty_default_dict(self):
        """Regression guard: pre-M7 `self.catalog` defaulted to `{}` because super was never called."""
        clear_catalog_cache()
        cat = Catalog()
        assert cat.catalog != {}, (
            "M7 regression: `self.catalog` is an empty dict -- "
            "`Catalog.model_post_init` is no longer calling `super().model_post_init()`."
        )
