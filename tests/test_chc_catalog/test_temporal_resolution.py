"""Lock-in for the M1 `temporal_resolution` vocabulary constraint."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from earthlens.chc import Catalog
from earthlens.chc.catalog import _TEMPORAL_RESOLUTIONS, clear_catalog_cache

pytestmark = [pytest.mark.chc]


def _dataset_block(key: str, temporal_resolution: str = "daily") -> str:
    """Render a minimal-valid `datasets.<key>:` body with a chosen temporal_resolution."""
    pandas_freq = {
        "10-day": "10D",
        "15-day": "15D",
        "2-monthly": "2MS",
        "3-monthly": "QS",
        "5-day": "5D",
        "6-hourly": "6h",
        "annual": "YS",
        "daily": "D",
        "daily-delta": "D",
        "dekadal": "10D",
        "monthly": "MS",
        "monthly-climatology": "MS",
        "pentadal": "5D",
        "seasonal": "QS",
    }.get(temporal_resolution, "D")
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
        "      precipitation:\n"
        "        description: synthetic\n"
        "        units: mm/day\n"
        "        types: flux\n"
    )


def _write_catalog(tmp_path: Path, dataset_block: str) -> Path:
    """Write a single-dataset single-file catalog."""
    catalog_yaml = tmp_path / "catalog.yaml"
    catalog_yaml.write_text(
        "available_datasets:\n  - synth\n"
        "regions:\n"
        "  global:\n"
        "    lat_boundaries: [-50, 50]\n"
        "    lon_boundaries: [-180, 180]\n"
        "datasets:\n"
        + dataset_block,
        encoding="utf-8",
    )
    return catalog_yaml


@pytest.fixture(scope="module")
def bundled_catalog() -> Catalog:
    """Bundled catalog, loaded once per module."""
    return Catalog()


class TestTemporalResolutionVocabulary:
    """`Dataset.temporal_resolution` is constrained to the M1 vocabulary."""

    def test_bundled_catalog_uses_only_known_values(self, bundled_catalog: Catalog):
        """Every dataset's temporal_resolution is in `_TEMPORAL_RESOLUTIONS`."""
        offenders = [
            (key, ds.temporal_resolution)
            for key, ds in bundled_catalog.datasets.items()
            if ds.temporal_resolution not in _TEMPORAL_RESOLUTIONS
        ]
        assert not offenders, offenders

    def test_vocabulary_size_pinned_at_fourteen(self):
        """The vocabulary has exactly 14 entries; future cadences must update this test deliberately."""
        assert len(_TEMPORAL_RESOLUTIONS) == 14
        # Belt-and-braces: assert the tuple is sorted so additions land in a predictable place.
        assert list(_TEMPORAL_RESOLUTIONS) == sorted(_TEMPORAL_RESOLUTIONS)

    def test_dataset_load_rejects_unknown_temporal_resolution(self, tmp_path: Path):
        """A YAML temporal_resolution outside the vocabulary raises at Dataset construction."""
        block = _dataset_block("synth", temporal_resolution="not-a-resolution")
        catalog_yaml = _write_catalog(tmp_path, block)
        clear_catalog_cache()
        with pytest.raises(ValueError, match=r"not-a-resolution|temporal_resolution") as exc:
            Catalog.load(catalog_path=catalog_yaml)
        message = str(exc.value)
        # Either the raw pydantic ValidationError mentions the literal options,
        # or `_build_chc_dataset`'s error wrap names the failing dataset key.
        assert "synth" in message or "Literal" in message or "literal_error" in message

    @pytest.mark.parametrize("temporal_resolution", _TEMPORAL_RESOLUTIONS)
    def test_each_valid_value_loads_clean(
        self, tmp_path: Path, temporal_resolution: str
    ):
        """Every value in `_TEMPORAL_RESOLUTIONS` is accepted on a synthetic dataset."""
        block = _dataset_block("synth", temporal_resolution=temporal_resolution)
        catalog_yaml = _write_catalog(tmp_path, block)
        clear_catalog_cache()
        cat = Catalog.load(catalog_path=catalog_yaml)
        assert cat.datasets["synth"].temporal_resolution == temporal_resolution

    def test_pydantic_validation_error_inside_load(self, tmp_path: Path):
        """The wrapper raises ValueError but the underlying cause is a pydantic ValidationError."""
        block = _dataset_block("synth", temporal_resolution="weekly")
        catalog_yaml = _write_catalog(tmp_path, block)
        clear_catalog_cache()
        with pytest.raises(ValueError) as exc:
            Catalog.load(catalog_path=catalog_yaml)
        # `_build_chc_dataset` wraps the pydantic ValidationError as ValueError;
        # `__cause__` should point at the original pydantic exception.
        assert isinstance(exc.value.__cause__, ValidationError) or "weekly" in str(exc.value)
