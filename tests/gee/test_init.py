"""Tests for `earthlens.gee` — the package's public surface."""

from __future__ import annotations

import importlib
from pathlib import Path

import earthlens.gee as gee_pkg
from earthlens.gee import (
    GEE,
    AuthenticationError,
    Band,
    Cadence,
    CATALOG_PATH,
    Catalog,
    Dataset,
    EarthEngineAuth,
    create_feature,
    create_geometry,
)

_EXPECTED_EXPORTS = {
    "GEE", "AuthenticationError", "Catalog", "Dataset", "Band", "Cadence",
    "Extent", "Provider", "CATALOG_PATH", "PROVIDERS_PATH", "EarthEngineAuth",
    "create_geometry", "create_feature", "sample_points",
    "feature_collection_to_dataframe", "feature_collection_to_gdf",
    "feature_collections_to_dataframe",
}


class TestPublicSurface:
    """Tests for the names re-exported from `earthlens.gee`."""

    def test_all_lists_the_expected_names(self):
        """`__all__` is exactly the documented public surface."""
        assert set(gee_pkg.__all__) == _EXPECTED_EXPORTS, (
            f"__all__ mismatch: {set(gee_pkg.__all__) ^ _EXPECTED_EXPORTS}"
        )

    def test_classes_resolve_to_their_modules(self):
        """The re-exported classes are the canonical ones from their submodules."""
        assert GEE.__module__ == "earthlens.gee.backend"
        assert AuthenticationError.__module__ == "earthlens.gee.auth"
        assert EarthEngineAuth.__module__ == "earthlens.gee.auth"
        for cls in (Catalog, Dataset, Band, Cadence):
            assert cls.__module__ == "earthlens.gee.catalog", cls

    def test_catalog_path_points_at_the_bundled_catalog_dir(self):
        """`CATALOG_PATH` is the bundled `catalog/` directory and exists."""
        assert isinstance(CATALOG_PATH, Path)
        assert CATALOG_PATH.name == "catalog"
        assert CATALOG_PATH.is_dir()
        assert (CATALOG_PATH / "_index.yaml").is_file()

    def test_feature_helpers_are_callable(self):
        """`create_geometry` / `create_feature` are importable callables."""
        assert callable(create_geometry) and create_geometry.__name__ == "create_geometry"
        assert callable(create_feature) and create_feature.__name__ == "create_feature"

    def test_catalog_usable_from_package_root(self):
        """`Catalog` works when imported from `earthlens.gee` directly."""
        assert Catalog().get_dataset("USGS/SRTMGL1_003").title == "NASA SRTM Digital Elevation 30m"

    def test_module_has_a_docstring(self):
        """The package has a module docstring describing the backend."""
        doc = importlib.import_module("earthlens.gee").__doc__ or ""
        assert "Earth Engine" in doc and len(doc) > 100
