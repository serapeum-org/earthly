"""Lock-in for the H4 collision-free cache fingerprint."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from earthlens.chc import Catalog
from earthlens.chc.catalog import clear_catalog_cache

pytestmark = [pytest.mark.chc]


_DATASET_BLOCK_TEMPLATE = (
    "  {key}:\n"
    "    ftp_bases: {{tif: 'pub/foo/'}}\n"
    "    file_patterns: {{tif: 'foo.tif'}}\n"
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
    "        types: flux\n"
)

_INDEX_HEAD = (
    "available_datasets:\n"
    "  - alpha\n"
    "  - beta\n"
    "regions:\n"
    "  global:\n"
    "    lat_boundaries: [-50, 50]\n"
    "    lon_boundaries: [-180, 180]\n"
)


def _write_split_catalog(tmp_path: Path) -> Path:
    """Build a directory-style catalog with `_index.yaml` + two family files."""
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    (catalog_dir / "_index.yaml").write_text(_INDEX_HEAD, encoding="utf-8")
    (catalog_dir / "alpha.yaml").write_text(
        "datasets:\n" + _DATASET_BLOCK_TEMPLATE.format(key="alpha"),
        encoding="utf-8",
    )
    (catalog_dir / "beta.yaml").write_text(
        "datasets:\n" + _DATASET_BLOCK_TEMPLATE.format(key="beta"),
        encoding="utf-8",
    )
    return catalog_dir


def _write_single_file_catalog(tmp_path: Path) -> Path:
    """Build a legacy single-file catalog with one dataset."""
    catalog_yaml = tmp_path / "catalog.yaml"
    catalog_yaml.write_text(
        "available_datasets:\n  - alpha\n"
        + _INDEX_HEAD.partition("regions:")[1].partition("regions:")[0]  # noop guard
        + "regions:\n"
        + "  global:\n"
        + "    lat_boundaries: [-50, 50]\n"
        + "    lon_boundaries: [-180, 180]\n"
        + "datasets:\n"
        + _DATASET_BLOCK_TEMPLATE.format(key="alpha"),
        encoding="utf-8",
    )
    return catalog_yaml


class TestCatalogCache:
    """The `_load_catalog_data` cache invalidates on real changes only."""

    def test_directory_identical_bytes_reuses_cached_datasets(self, tmp_path: Path):
        """Two loads with no file changes reuse the same Dataset instances."""
        catalog_dir = _write_split_catalog(tmp_path)
        clear_catalog_cache()
        cat1 = Catalog.load(catalog_path=catalog_dir)
        cat2 = Catalog.load(catalog_path=catalog_dir)
        assert cat1.datasets["alpha"] is cat2.datasets["alpha"]
        assert cat1.datasets["beta"] is cat2.datasets["beta"]

    def test_directory_single_file_touch_invalidates_cache(self, tmp_path: Path):
        """Bumping one per-family file's mtime invalidates the cache."""
        catalog_dir = _write_split_catalog(tmp_path)
        clear_catalog_cache()
        cat1 = Catalog.load(catalog_path=catalog_dir)
        # Bump alpha.yaml mtime; beta.yaml and _index.yaml untouched.
        alpha = catalog_dir / "alpha.yaml"
        bumped = alpha.stat().st_mtime + 60.0
        os.utime(alpha, (bumped, bumped))
        cat2 = Catalog.load(catalog_path=catalog_dir)
        assert cat1.datasets["alpha"] is not cat2.datasets["alpha"], (
            "cache failed to invalidate after one file mtime changed"
        )

    def test_single_file_cache_hits_and_misses(self, tmp_path: Path):
        """The legacy single-file branch caches and invalidates on touch."""
        catalog_yaml = _write_single_file_catalog(tmp_path)
        clear_catalog_cache()
        cat1 = Catalog.load(catalog_path=catalog_yaml)
        cat2 = Catalog.load(catalog_path=catalog_yaml)
        assert cat1.datasets["alpha"] is cat2.datasets["alpha"]
        bumped = catalog_yaml.stat().st_mtime + 60.0
        os.utime(catalog_yaml, (bumped, bumped))
        cat3 = Catalog.load(catalog_path=catalog_yaml)
        assert cat1.datasets["alpha"] is not cat3.datasets["alpha"]

    def test_directory_mtime_permutation_invalidates_cache(self, tmp_path: Path):
        """The H4 regression: permuting mtimes (sum unchanged) still triggers a cache miss."""
        catalog_dir = _write_split_catalog(tmp_path)
        # Set deterministic mtimes so the swap below has a definite "sum unchanged" outcome.
        alpha = catalog_dir / "alpha.yaml"
        beta = catalog_dir / "beta.yaml"
        base = alpha.stat().st_mtime
        os.utime(alpha, (base + 10.0, base + 10.0))
        os.utime(beta, (base + 20.0, base + 20.0))
        clear_catalog_cache()
        cat1 = Catalog.load(catalog_path=catalog_dir)
        # Swap mtimes: alpha gets beta's old value, beta gets alpha's. Sum is identical.
        os.utime(alpha, (base + 20.0, base + 20.0))
        os.utime(beta, (base + 10.0, base + 10.0))
        cat2 = Catalog.load(catalog_path=catalog_dir)
        assert cat1.datasets["alpha"] is not cat2.datasets["alpha"], (
            "permuted-mtime collision (H4 regression) -- "
            "fingerprint must include per-file (name, mtime), not the sum"
        )
