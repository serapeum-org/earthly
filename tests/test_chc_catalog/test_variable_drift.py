"""Lock-in for the H3 `variable_metadata_drift` health() key."""

from __future__ import annotations

from pathlib import Path

import pytest

from earthlens.chc import Catalog
from earthlens.chc.catalog import clear_catalog_cache

pytestmark = [pytest.mark.chc]


def _dataset_block(
    key: str,
    *,
    temporal_resolution: str = "daily",
    var_name: str = "precipitation",
    var_description: str = "synthetic",
    var_units: str = "mm/day",
    var_types: str = "flux",
) -> str:
    """Render a `datasets.<key>:` body with controllable variable metadata."""
    pandas_freq = {"daily": "D", "monthly": "MS"}.get(temporal_resolution, "D")
    return (
        f"  {key}:\n"
        "    ftp_bases: {tif: 'pub/foo/'}\n"
        "    file_patterns: {tif: 'foo.tif'}\n"
        "    region: global\n"
        f"    temporal_resolution: {temporal_resolution}\n"
        f"    pandas_freq: {pandas_freq}\n"
        "    spatial_resolution: [0.05]\n"
        "    formats: [tif]\n"
        "    start_date: '2020-01-01'\n"
        "    variables:\n"
        f"      {var_name}:\n"
        f"        description: {var_description!r}\n"
        f"        units: {var_units!r}\n"
        f"        types: {var_types}\n"
    )


def _write_catalog(tmp_path: Path, dataset_blocks: list[str]) -> Path:
    """Write a single-file catalog containing the given dataset blocks."""
    catalog_yaml = tmp_path / "catalog.yaml"
    # Extract dataset keys from each block ("  <key>:\n..." -> "<key>")
    keys = [
        block.splitlines()[0].strip().rstrip(":")
        for block in dataset_blocks
    ]
    body = "available_datasets:\n"
    for key in keys:
        body += f"  - {key}\n"
    body += (
        "regions:\n"
        "  global:\n"
        "    lat_boundaries: [-50, 50]\n"
        "    lon_boundaries: [-180, 180]\n"
        "datasets:\n"
    )
    for block in dataset_blocks:
        body += block
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


class TestVariableMetadataDrift:
    """`health()['variable_metadata_drift']` flags groups with mismatched (units, types)."""

    def test_bundled_catalog_reports_known_precipitation_daily_drift(self, bundled_catalog: Catalog):
        """The shipped catalog has exactly one drift: precipitation/daily (gefs-v12-16day outlier)."""
        report = bundled_catalog.health()
        assert report["variable_metadata_drift"] == ["precipitation/daily"]

    def test_matching_units_and_types_report_no_drift(self, tmp_path: Path):
        """Two rows in the same (variable, cadence) group with identical metadata are clean."""
        blocks = [
            _dataset_block("alpha-daily"),
            _dataset_block("beta-daily"),
        ]
        catalog_yaml = _write_catalog(tmp_path, blocks)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        assert cat.health()["variable_metadata_drift"] == []

    def test_mismatched_units_surface_as_drift(self, tmp_path: Path):
        """Same (variable, cadence) but different units -> drift key in the list."""
        blocks = [
            _dataset_block("alpha-daily", var_units="mm/day"),
            _dataset_block("beta-daily", var_units="mm"),  # outlier
        ]
        catalog_yaml = _write_catalog(tmp_path, blocks)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        assert cat.health()["variable_metadata_drift"] == ["precipitation/daily"]

    def test_mismatched_types_surface_as_drift(self, tmp_path: Path):
        """Same (variable, cadence) but different `types` (flux vs state) -> drift."""
        blocks = [
            _dataset_block("alpha-daily", var_types="flux"),
            _dataset_block("beta-daily", var_types="state"),
        ]
        catalog_yaml = _write_catalog(tmp_path, blocks)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        assert cat.health()["variable_metadata_drift"] == ["precipitation/daily"]

    def test_mismatched_description_alone_does_not_count(self, tmp_path: Path):
        """Description is intentionally ignored (CMIP6 SSP rows legitimately vary it)."""
        blocks = [
            _dataset_block(
                "alpha-daily",
                var_description="Daily rainfall under SSP2-4.5",
            ),
            _dataset_block(
                "beta-daily",
                var_description="Daily rainfall under SSP5-8.5",
            ),
        ]
        catalog_yaml = _write_catalog(tmp_path, blocks)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        assert cat.health()["variable_metadata_drift"] == []

    def test_two_independent_drifts_are_reported_sorted(self, tmp_path: Path):
        """Two drifts across two groups appear together, sorted alphabetically."""
        blocks = [
            # Group 1: (precipitation, daily) drifts on units
            _dataset_block("alpha-daily", var_units="mm/day"),
            _dataset_block("beta-daily", var_units="mm"),
            # Group 2: (precipitation, monthly) drifts on types
            _dataset_block(
                "alpha-monthly",
                temporal_resolution="monthly",
                var_units="mm/month",
                var_types="flux",
            ),
            _dataset_block(
                "beta-monthly",
                temporal_resolution="monthly",
                var_units="mm/month",
                var_types="state",
            ),
        ]
        catalog_yaml = _write_catalog(tmp_path, blocks)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        assert cat.health()["variable_metadata_drift"] == [
            "precipitation/daily",
            "precipitation/monthly",
        ]
