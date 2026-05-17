"""Lock-in for the two new `Catalog.health()` checks added by C5."""

from __future__ import annotations

from pathlib import Path

import pytest

from earthlens.chc import Catalog
from earthlens.chc.catalog import clear_catalog_cache

pytestmark = [pytest.mark.chc]


def _dataset_block(key: str) -> str:
    """Render a minimal-valid `datasets.<key>:` YAML body for synthetic tests."""
    return (
        f"  {key}:\n"
        "    ftp_bases: {tif: 'pub/foo/'}\n"
        "    file_patterns: {tif: 'foo.tif'}\n"
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


def _write_catalog(
    tmp_path: Path,
    available: list[str],
    dataset_keys: list[str],
) -> Path:
    """Write a single-file catalog with controlled `available_datasets` / `datasets` sets."""
    catalog_yaml = tmp_path / "catalog.yaml"
    body = "available_datasets:\n"
    for key in available:
        body += f"  - {key}\n"
    body += (
        "regions:\n"
        "  global:\n"
        "    lat_boundaries: [-50, 50]\n"
        "    lon_boundaries: [-180, 180]\n"
        "datasets:\n"
    )
    for key in dataset_keys:
        body += _dataset_block(key)
    catalog_yaml.write_text(body, encoding="utf-8")
    return catalog_yaml


def _write_providers(tmp_path: Path) -> Path:
    """Write a providers.yaml with the single ucsb-chc slug."""
    path = tmp_path / "providers.yaml"
    path.write_text(
        "providers:\n  ucsb-chc:\n    display_name: 'UCSB CHC'\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def bundled_catalog() -> Catalog:
    """Bundled catalog, loaded once per module."""
    return Catalog()


class TestHealthIndexConsistency:
    """`Catalog.health()` reports drift between `available_datasets` and `datasets`."""

    def test_bundled_catalog_has_no_drift(self, bundled_catalog: Catalog):
        """The shipped catalog matches `available_datasets` 1:1 to `datasets`."""
        report = bundled_catalog.health()
        assert report["index_missing_in_datasets"] == []
        assert report["datasets_missing_in_index"] == []

    def test_index_missing_in_datasets_lists_only_index_orphans(self, tmp_path: Path):
        """A key in available_datasets but not in datasets surfaces only on the index side."""
        catalog_yaml = _write_catalog(
            tmp_path,
            available=["alpha", "beta"],
            dataset_keys=["alpha"],
        )
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        report = cat.health()
        assert report["index_missing_in_datasets"] == ["beta"]
        assert report["datasets_missing_in_index"] == []

    def test_datasets_missing_in_index_lists_only_dataset_orphans(self, tmp_path: Path):
        """A key in datasets but not in available_datasets surfaces only on the dataset side."""
        catalog_yaml = _write_catalog(
            tmp_path,
            available=["alpha"],
            dataset_keys=["alpha", "beta"],
        )
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        report = cat.health()
        assert report["index_missing_in_datasets"] == []
        assert report["datasets_missing_in_index"] == ["beta"]

    def test_both_drifts_at_once_are_reported_separately(self, tmp_path: Path):
        """Keys missing on either side appear on the correct list, never the other."""
        catalog_yaml = _write_catalog(
            tmp_path,
            available=["alpha", "only-in-index"],
            dataset_keys=["alpha", "only-in-datasets"],
        )
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        report = cat.health()
        assert report["index_missing_in_datasets"] == ["only-in-index"]
        assert report["datasets_missing_in_index"] == ["only-in-datasets"]

    def test_perfectly_aligned_catalog_reports_no_drift(self, tmp_path: Path):
        """Two-entry catalog with equal sets reports clean on both new keys."""
        catalog_yaml = _write_catalog(
            tmp_path,
            available=["alpha", "beta"],
            dataset_keys=["alpha", "beta"],
        )
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        report = cat.health()
        assert report["index_missing_in_datasets"] == []
        assert report["datasets_missing_in_index"] == []
