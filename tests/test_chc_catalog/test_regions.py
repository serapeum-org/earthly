"""Lock-in for the H1 'regions are the single source of truth' invariant."""

from __future__ import annotations

from pathlib import Path

import pytest

from earthlens.chc import Catalog
from earthlens.chc.catalog import clear_catalog_cache

pytestmark = [pytest.mark.chc]


def _dataset_block(
    key: str,
    region: str = "global",
    extra_lines: tuple[str, ...] = (),
) -> str:
    """Render a minimal-valid `datasets.<key>:` body, optionally with extra inline lines."""
    block = (
        f"  {key}:\n"
        "    ftp_bases: {tif: 'pub/foo/'}\n"
        "    file_patterns: {tif: 'foo.tif'}\n"
        f"    region: {region}\n"
        "    temporal_resolution: daily\n"
        "    pandas_freq: D\n"
        "    spatial_resolution: [0.05]\n"
        "    formats: [tif]\n"
        "    start_date: '2020-01-01'\n"
    )
    for line in extra_lines:
        block += f"    {line}\n"
    block += (
        "    variables:\n"
        "      precipitation:\n"
        "        description: synthetic\n"
        "        units: mm/day\n"
        "        types: flux\n"
    )
    return block


def _write_catalog(
    tmp_path: Path,
    dataset_blocks: str,
    extra_regions: str = "",
) -> Path:
    """Write a single-file catalog with the given dataset bodies and optional extra regions."""
    catalog_yaml = tmp_path / "catalog.yaml"
    catalog_yaml.write_text(
        "available_datasets:\n  - synth-daily\n"
        "regions:\n"
        "  global:\n"
        "    lat_boundaries: [-50, 50]\n"
        "    lon_boundaries: [-180, 180]\n"
        f"{extra_regions}"
        "datasets:\n"
        f"{dataset_blocks}",
        encoding="utf-8",
    )
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


class TestRegionAuthoritative:
    """Contract: every dataset's spatial bounds come from `regions[ds.region]`."""

    def test_bundled_catalog_bounds_match_their_region_exactly(self, bundled_catalog: Catalog):
        """Every dataset's lat/lon_boundaries match its region's definition 1:1."""
        offenders: list[tuple[str, str, list, list, list, list]] = []
        for key, ds in bundled_catalog.datasets.items():
            region_def = bundled_catalog.available_regions.get(ds.region)
            assert region_def is not None, (
                f"{key}: region {ds.region!r} is missing from available_regions"
            )
            expected_lat = [float(v) for v in region_def["lat_boundaries"]]
            expected_lon = [float(v) for v in region_def["lon_boundaries"]]
            if ds.lat_boundaries != expected_lat or ds.lon_boundaries != expected_lon:
                offenders.append(
                    (
                        key, ds.region,
                        ds.lat_boundaries, ds.lon_boundaries,
                        expected_lat, expected_lon,
                    )
                )
        assert not offenders, (
            f"datasets whose lat/lon_boundaries no longer match their region "
            f"(H1 regression): {offenders}"
        )

    def test_centennial_trends_uses_the_new_dedicated_region(self, bundled_catalog: Catalog):
        """CenTrends entries point at `east-africa-centennial` with the wider extent."""
        ds = bundled_catalog.get_dataset("centennial-trends-v1-monthly")
        assert ds.region == "east-africa-centennial"
        assert ds.lat_boundaries == [-12.25, 22.25]
        assert ds.lon_boundaries == [21.25, 51.25]


class TestLoaderRejections:
    """Inline lat/lon are now rejected; unknown regions are rejected."""

    def test_inline_lat_boundaries_raises_value_error(self, tmp_path: Path):
        """A YAML with inline `lat_boundaries:` on a dataset raises with an H1 pointer."""
        body = _dataset_block(
            "synth-daily",
            region="global",
            extra_lines=("lat_boundaries: [-10, 10]",),
        )
        catalog_yaml = _write_catalog(tmp_path, body)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        with pytest.raises(ValueError, match=r"H1|regions:") as exc:
            Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        assert "synth-daily" in str(exc.value)
        assert "lat_boundaries" in str(exc.value) or "lon_boundaries" in str(exc.value)

    def test_inline_lon_boundaries_alone_also_raises(self, tmp_path: Path):
        """Either inline field (lat OR lon) is enough to trip the rejection."""
        body = _dataset_block(
            "synth-daily",
            region="global",
            extra_lines=("lon_boundaries: [-100, 100]",),
        )
        catalog_yaml = _write_catalog(tmp_path, body)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        with pytest.raises(ValueError, match=r"H1|regions:"):
            Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)

    def test_unknown_region_raises_value_error_listing_known_regions(self, tmp_path: Path):
        """A dataset with a region not defined in `regions:` raises with the available list."""
        body = _dataset_block("synth-daily", region="not-a-region")
        catalog_yaml = _write_catalog(tmp_path, body)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        with pytest.raises(ValueError, match=r"not defined in") as exc:
            Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        message = str(exc.value)
        assert "not-a-region" in message
        assert "global" in message


class TestLoaderHappyPath:
    """A clean dataset (region only, no inline) loads with bounds resolved from regions."""

    def test_region_only_loads_with_resolved_bounds(self, tmp_path: Path):
        """A dataset with `region: global` and no inline bounds resolves to [-50, 50]."""
        body = _dataset_block("synth-daily", region="global")
        catalog_yaml = _write_catalog(tmp_path, body)
        providers_yaml = _write_providers(tmp_path)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        ds = cat.datasets["synth-daily"]
        assert ds.region == "global"
        assert ds.lat_boundaries == [-50.0, 50.0]
        assert ds.lon_boundaries == [-180.0, 180.0]
