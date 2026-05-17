"""Tests for `earthlens.base.providers` — `Provider` + `load_providers`."""

from __future__ import annotations

from pathlib import Path

import pytest

from earthlens.base.providers import (
    Provider,
    clear_providers_cache,
    load_providers,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with an empty providers cache."""
    clear_providers_cache()


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestProviderModel:
    """Tests for the `Provider` pydantic value object."""

    def test_minimal_construction(self):
        """A bare `slug` + `display_name` constructs cleanly with no parent."""
        p = Provider(slug="ucsb-chc", display_name="UCSB CHC")
        assert p.parent is None
        assert p.display_name == "UCSB CHC"

    def test_extra_fields_rejected(self):
        """`extra="forbid"` blocks unknown keys."""
        with pytest.raises(Exception):
            Provider(slug="x", display_name="X", surprise=1)


class TestLoadProviders:
    """Tests for `load_providers`."""

    def test_loads_a_simple_flat_registry(self, tmp_path):
        """A two-entry providers file parses to a `slug -> Provider` mapping."""
        path = _write(
            tmp_path / "providers.yaml",
            "providers:\n"
            "  ucsb-chc:\n"
            "    display_name: UCSB CHC\n"
            "  ecmwf:\n"
            "    display_name: ECMWF\n",
        )
        out = load_providers(path)
        assert set(out) == {"ucsb-chc", "ecmwf"}
        assert out["ucsb-chc"].display_name == "UCSB CHC"

    def test_parent_must_be_registered_slug(self, tmp_path):
        """A `parent:` pointing at an unknown slug raises `ValueError`."""
        path = _write(
            tmp_path / "providers.yaml",
            "providers:\n"
            "  daac-x:\n"
            "    display_name: DAAC X\n"
            "    parent: nasa\n",
        )
        with pytest.raises(ValueError, match="parent='nasa'"):
            load_providers(path)

    def test_invalid_provider_body_raises(self, tmp_path):
        """A row missing the required `display_name` raises `ValueError`."""
        path = _write(
            tmp_path / "providers.yaml",
            "providers:\n"
            "  ucsb-chc: {}\n",
        )
        with pytest.raises(ValueError, match="invalid provider 'ucsb-chc'"):
            load_providers(path)

    def test_missing_file_raises_clear_error(self, tmp_path):
        """A non-existent path raises `ValueError` (not raw `FileNotFoundError`)."""
        with pytest.raises(ValueError, match="not found"):
            load_providers(tmp_path / "absent.yaml")

    def test_cache_avoids_reparsing_unchanged_file(self, tmp_path, monkeypatch):
        """Same `(path, mtime_ns)` returns the cached dict without re-parsing."""
        from earthlens.base import providers as providers_module

        calls: list[Path] = []

        def _spy_loader(p):
            calls.append(p)
            return {"providers": {"x": {"display_name": "X"}}}

        path = _write(tmp_path / "providers.yaml", "providers:\n  x:\n    display_name: X\n")
        monkeypatch.setattr(providers_module, "load_yaml_strict", _spy_loader)
        load_providers(path)
        load_providers(path)
        assert len(calls) == 1

    def test_parent_pointing_to_registered_sibling_resolves(self, tmp_path):
        """A `parent:` slug that IS in the same file is accepted."""
        path = _write(
            tmp_path / "providers.yaml",
            "providers:\n"
            "  nasa:\n"
            "    display_name: NASA\n"
            "  nasa-lp-daac:\n"
            "    display_name: NASA LP DAAC\n"
            "    parent: nasa\n",
        )
        out = load_providers(path)
        assert out["nasa-lp-daac"].parent == "nasa"
