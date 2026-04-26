"""Unit tests for :class:`earth2observe.ecmwf.Catalog`.

Covers the H2 / H5 rewiring (the catalog reads
``cds_data_catalog.yaml`` and exposes per-variable
:class:`Variable` instances), the M2 fail-loud behaviour on
malformed YAML, and the no-MARS-keys invariant on the schema.
"""

from __future__ import annotations

import pytest

from earth2observe.ecmwf import Catalog, Variable

pytestmark = [pytest.mark.unit]


class TestCatalog:
    """Tests for :class:`Catalog` after the H2 / H5 / M1 / M2 work."""

    def test_catalog_loads_per_variable_map(self):
        """``catalog`` is a per-variable map of :class:`Variable`.

        Test scenario:
            After M1, ``Catalog`` returns frozen :class:`Variable`
            instances keyed by short variable codes.
        """
        cat = Catalog()
        assert isinstance(cat.catalog, dict)
        assert "2T" in cat.catalog
        assert isinstance(cat.catalog["2T"], Variable)
        assert cat.catalog["2T"].cds_dataset

    @pytest.mark.parametrize(
        "var_code, expected_dataset, expected_variable",
        [
            ("2T", "reanalysis-era5-single-levels", "2m_temperature"),
            ("TP", "reanalysis-era5-single-levels", "total_precipitation"),
            ("SP", "reanalysis-era5-single-levels", "surface_pressure"),
            ("E", "reanalysis-era5-single-levels", "evaporation"),
            ("T", "reanalysis-era5-pressure-levels", "temperature"),
        ],
    )
    def test_get_dataset_returns_new_schema(
        self, var_code, expected_dataset, expected_variable
    ):
        """``get_dataset`` returns a :class:`Variable` per variable.

        Test scenario:
            The five mappings the migration plan calls out explicitly
            (E, T, 2T, TP, SP) must round-trip through the catalog
            and expose the right dataset/variable as attributes.
        """
        spec = Catalog().get_dataset(var_code)
        assert spec.cds_dataset == expected_dataset
        assert spec.cds_variable == expected_variable

    def test_get_dataset_includes_unit_conversion_factors(self):
        """Per-variable metadata carries the K -> C unit conversion."""
        spec = Catalog().get_dataset("2T")
        assert spec.factors_add == -273.15
        assert spec.factors_mul == 1

    def test_pressure_level_var_carries_cds_pressure_level(self):
        """Pressure-level variables expose ``cds_pressure_level``.

        Test scenario:
            T, Q, R live on reanalysis-era5-pressure-levels; their
            catalog entries must carry the ``cds_pressure_level``
            attribute so :meth:`ECMWF.api` can forward it to CDS.
        """
        spec = Catalog().get_dataset("T")
        assert spec.cds_pressure_level == ["1000"]

    def test_get_dataset_raises_key_error_for_unknown_code(self):
        """Unknown variable codes raise ``KeyError``.

        Test scenario:
            Asking for a code that isn't in the catalog must raise
            ``KeyError`` immediately rather than returning ``None`` and
            blowing up later inside ``api()``.
        """
        with pytest.raises(KeyError):
            Catalog().get_dataset("DEFINITELY_NOT_A_REAL_CODE")

    def test_get_variable_aliases_get_dataset(self):
        """``get_variable`` returns the same Variable as ``get_dataset``."""
        cat = Catalog()
        assert cat.get_variable("2T") == cat.get_dataset("2T")

    def test_no_mars_schema_keys_remain(self):
        """No Variable field is a stale MARS-style key."""
        forbidden = {"number_para", "download type", "var_name"}
        present = set(Variable.model_fields)
        assert not (forbidden & present)

    def test_get_catalog_raises_on_empty_variables(self, monkeypatch, tmp_path):
        """A YAML missing ``variables:`` raises ``ValueError``.

        Test scenario:
            Pre-M2, ``Catalog.get_catalog`` returned ``{}`` when the
            top-level ``variables`` key was absent or empty, then
            every subsequent ``get_dataset(code)`` raised ``KeyError``
            — misleading the user about *which* file is broken.
        """
        empty_yaml = tmp_path / "cds_data_catalog.yaml"
        empty_yaml.write_text("version: 2\ndatasets: []\n", encoding="utf-8")
        from earth2observe.ecmwf import catalog as catalog_module

        monkeypatch.setattr(catalog_module, "CATALOG_PATH", empty_yaml)
        with pytest.raises(ValueError, match="variables"):
            Catalog()

    def test_get_catalog_raises_on_null_variables(self, monkeypatch, tmp_path):
        """A YAML with ``variables: null`` also raises ``ValueError``.

        Test scenario:
            yaml.safe_load resolves ``variables:`` (no value) to
            ``None``. The empty-check covers both the missing-key
            and the explicit-null cases.
        """
        null_yaml = tmp_path / "cds_data_catalog.yaml"
        null_yaml.write_text("variables:\n", encoding="utf-8")
        from earth2observe.ecmwf import catalog as catalog_module

        monkeypatch.setattr(catalog_module, "CATALOG_PATH", null_yaml)
        with pytest.raises(ValueError, match="variables"):
            Catalog()
