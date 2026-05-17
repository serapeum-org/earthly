"""Lock-in for the YAML-driven provider field after the C2 fix."""

from __future__ import annotations

from pathlib import Path

import pytest

from earthlens.chc import Catalog
from earthlens.chc.catalog import _build_chc_dataset, clear_catalog_cache

pytestmark = [pytest.mark.chc]


_BASE_BODY: dict = {
    "ftp_bases": {"tif": "pub/org/chc/products/CHIRPS-2.0/global_daily/tifs/p05/"},
    "file_patterns": {"tif": "{year}/foo.{year}.{month}.{day}.tif"},
    "region": "global",
    "temporal_resolution": "daily",
    "pandas_freq": "D",
    "spatial_resolution": [0.05],
    "formats": ["tif"],
    "start_date": "1981-01-01",
    "variables": {
        "precipitation": {
            "description": "synthetic",
            "units": "mm/day",
            "types": "flux",
        }
    },
}

_REGIONS: dict = {
    "global": {"lat_boundaries": [-50, 50], "lon_boundaries": [-180, 180]}
}


def _body_with(**overrides: object) -> dict:
    """Shallow-copy `_BASE_BODY` with one or more keys replaced."""
    body = dict(_BASE_BODY)
    body.update(overrides)
    return body


def _write_catalog(tmp_path: Path, provider_value: str) -> Path:
    """Write a tiny single-file CHC catalog with the given provider value."""
    path = tmp_path / "catalog.yaml"
    path.write_text(
        "available_datasets:\n"
        "  - synth-daily\n"
        "regions:\n"
        "  global:\n"
        "    lat_boundaries: [-50, 50]\n"
        "    lon_boundaries: [-180, 180]\n"
        "datasets:\n"
        "  synth-daily:\n"
        "    ftp_bases:\n"
        "      tif: 'pub/foo/'\n"
        "    file_patterns:\n"
        "      tif: '{year}/foo.{year}.tif'\n"
        "    region: global\n"
        "    temporal_resolution: daily\n"
        "    pandas_freq: D\n"
        "    spatial_resolution: [0.05]\n"
        "    formats: [tif]\n"
        "    start_date: '2020-01-01'\n"
        f"    provider: {provider_value}\n"
        "    variables:\n"
        "      precipitation:\n"
        "        description: synthetic\n"
        "        units: mm/day\n"
        "        types: flux\n",
        encoding="utf-8",
    )
    return path


def _write_providers(tmp_path: Path, *slugs: str) -> Path:
    """Write a tiny providers registry with the given slugs."""
    path = tmp_path / "providers.yaml"
    body = "providers:\n"
    for slug in slugs:
        body += f"  {slug}:\n    display_name: '{slug}'\n"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    """Bundled catalog, loaded once per module via the disk-mtime cache."""
    return Catalog()


class TestBuildChcDatasetProvider:
    """The provider field threading inside `_build_chc_dataset`."""

    def test_default_when_omitted_is_ucsb_chc(self):
        """A dataset body without `provider:` falls back to ucsb-chc."""
        ds, _ = _build_chc_dataset("synth", _body_with(), _REGIONS, Path("synth.yaml"))
        assert ds.provider == "ucsb-chc"

    def test_explicit_override_is_honoured(self):
        """A dataset body with `provider: nasa-lance` reaches the Dataset unchanged."""
        body = _body_with(provider="nasa-lance")
        ds, _ = _build_chc_dataset("synth", body, _REGIONS, Path("synth.yaml"))
        assert ds.provider == "nasa-lance"

    def test_arbitrary_slug_is_honoured_at_helper_level(self):
        """Slug validation lives in Catalog.load, not in the per-dataset builder."""
        body = _body_with(provider="not-a-real-publisher")
        ds, _ = _build_chc_dataset("synth", body, _REGIONS, Path("synth.yaml"))
        assert ds.provider == "not-a-real-publisher"

    def test_explicit_ucsb_chc_is_pass_through(self):
        """Setting the default value explicitly is indistinguishable from omission."""
        body = _body_with(provider="ucsb-chc")
        ds, _ = _build_chc_dataset("synth", body, _REGIONS, Path("synth.yaml"))
        assert ds.provider == "ucsb-chc"


class TestCatalogLoadProviderValidation:
    """`Catalog.load` enforces every Dataset.provider exists in providers.yaml."""

    def test_unregistered_slug_raises_with_clear_message(self, tmp_path: Path):
        """An unregistered provider slug raises ValueError naming the slug + providers.yaml."""
        catalog_yaml = _write_catalog(tmp_path, "unknown-slug")
        providers_yaml = _write_providers(tmp_path, "ucsb-chc")
        clear_catalog_cache()
        with pytest.raises(ValueError, match=r"unknown-slug") as exc:
            Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        message = str(exc.value)
        assert "providers.yaml" in message
        assert "unknown-slug" in message

    def test_known_slug_loads_clean(self, tmp_path: Path):
        """A provider matching providers.yaml loads without error and is preserved."""
        catalog_yaml = _write_catalog(tmp_path, "nasa-lance")
        providers_yaml = _write_providers(tmp_path, "ucsb-chc", "nasa-lance")
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml, providers_path=providers_yaml)
        assert "synth-daily" in cat.datasets
        assert cat.datasets["synth-daily"].provider == "nasa-lance"

    def test_default_ucsb_chc_loads_when_provider_field_omitted(self, tmp_path: Path):
        """A YAML without `provider:` resolves to the ucsb-chc default and passes validation."""
        # _write_catalog always writes a `provider:` field, so synthesise one without it.
        path = tmp_path / "catalog.yaml"
        path.write_text(
            "available_datasets:\n"
            "  - synth-daily\n"
            "regions:\n"
            "  global:\n"
            "    lat_boundaries: [-50, 50]\n"
            "    lon_boundaries: [-180, 180]\n"
            "datasets:\n"
            "  synth-daily:\n"
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
        providers_yaml = _write_providers(tmp_path, "ucsb-chc")
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=path, providers_path=providers_yaml)
        assert cat.datasets["synth-daily"].provider == "ucsb-chc"


class TestBundledCatalogProviderDefaults:
    """The shipped CHC catalog uses the ucsb-chc default catalog-wide."""

    def test_every_dataset_resolves_to_ucsb_chc(self, catalog: Catalog):
        """No per-family YAML sets `provider:` today, so the default fires for every entry."""
        offenders = [
            (key, ds.provider)
            for key, ds in catalog.datasets.items()
            if ds.provider != "ucsb-chc"
        ]
        assert not offenders, offenders
