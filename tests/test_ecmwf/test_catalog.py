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
        assert "2m-temperature" in cat.catalog
        assert isinstance(cat.catalog["2m-temperature"], Variable)
        assert cat.catalog["2m-temperature"].cds_dataset

    @pytest.mark.parametrize(
        "var_code, expected_dataset, expected_variable",
        [
            ("2m-temperature", "reanalysis-era5-single-levels", "2m_temperature"),
            ("total-precipitation", "reanalysis-era5-single-levels", "total_precipitation"),
            ("surface-pressure", "reanalysis-era5-single-levels", "surface_pressure"),
            ("evaporation", "reanalysis-era5-single-levels", "evaporation"),
            ("temperature", "reanalysis-era5-pressure-levels", "temperature"),
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

    def test_get_dataset_returns_raw_era5_units(self):
        """2m-temperature carries the raw ERA5 unit (Kelvin)."""
        spec = Catalog().get_dataset("2m-temperature")
        assert spec.units == "K"

    def test_available_datasets_lists_cds_collection(self):
        """available_datasets exposes the informational dataset list."""
        cat = Catalog()
        assert isinstance(cat.available_datasets, list)
        assert "reanalysis-era5-single-levels" in cat.available_datasets
        assert len(cat.available_datasets) > 100

    def test_datasets_groups_variables_under_their_cds_dataset(self):
        """datasets nests each Variable under its parent CDS dataset."""
        cat = Catalog()
        single = cat.datasets["reanalysis-era5-single-levels"]
        assert single.monthly == "reanalysis-era5-single-levels-monthly-means"
        assert "2m-temperature" in single.variables
        assert single.variables["2m-temperature"].cds_dataset == (
            "reanalysis-era5-single-levels"
        )

    def test_flat_and_structural_views_share_variable_instances(self):
        """catalog and datasets[ds].variables point at the same Variable."""
        cat = Catalog()
        flat = cat.catalog["2m-temperature"]
        nested = cat.datasets["reanalysis-era5-single-levels"].variables[
            "2m-temperature"
        ]
        assert flat is nested

    def test_pressure_level_var_carries_cds_pressure_level(self):
        """Pressure-level variables expose ``cds_pressure_level``.

        Test scenario:
            T, Q, R live on reanalysis-era5-pressure-levels; their
            catalog entries must carry the ``cds_pressure_level``
            attribute so :meth:`ECMWF.api` can forward it to CDS.
        """
        spec = Catalog().get_dataset("temperature")
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
        assert cat.get_variable("2m-temperature") == cat.get_dataset("2m-temperature")

    def test_no_mars_schema_keys_remain(self):
        """No Variable field is a stale MARS-style key."""
        forbidden = {"number_para", "download type", "var_name"}
        present = set(Variable.model_fields)
        assert not (forbidden & present)

    @pytest.mark.parametrize("mars_key", ["number_para", "download type", "var_name"])
    def test_no_mars_schema_keys_in_extras(self, monkeypatch, tmp_path, mars_key):
        """Legacy MARS keys are rejected inside ``extras``."""
        from earth2observe.ecmwf import catalog as catalog_module

        catalog_yaml = tmp_path / "cds_data_catalog.yaml"
        catalog_yaml.write_text(
            "datasets:\n"
            "  reanalysis-era5-single-levels:\n"
            "    monthly: x\n"
            "    variables:\n"
            "      2m-temperature:\n"
            "        nc_variable: t2m\n"
            "        units: K\n"
            "        extras:\n"
            f"          {mars_key!r}: '1'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog_yaml)
        with pytest.raises(ValueError, match="legacy MARS keys"):
            Catalog()

    def test_unknown_top_level_key_still_fails_validation(self):
        """An unknown key on a Variable row still fails pydantic validation."""
        with pytest.raises(ValueError):
            Variable.from_dict(
                "x",
                {
                    "cds_dataset": "ds",
                    "cds_variable": "v",
                    "nc_variable": "n",
                    "units": "K",
                    "totally_unknown": "boom",
                },
            )

    def test_extras_propagate_from_parent_dataset(self, monkeypatch, tmp_path):
        """Parent ``Dataset.extras`` propagates into each child Variable."""
        from earth2observe.ecmwf import catalog as catalog_module

        catalog_yaml = tmp_path / "cds_data_catalog.yaml"
        catalog_yaml.write_text(
            "datasets:\n"
            "  reanalysis-carra-single-levels:\n"
            "    extras:\n"
            "      domain: east\n"
            "      leadtime_hour: '1'\n"
            "    variables:\n"
            "      2m-temperature:\n"
            "        nc_variable: t2m\n"
            "        units: K\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog_yaml)
        cat = Catalog()
        spec = cat.get_dataset("2m-temperature")
        assert spec.extras == {"domain": "east", "leadtime_hour": "1"}
        assert cat.datasets["reanalysis-carra-single-levels"].extras == {
            "domain": "east",
            "leadtime_hour": "1",
        }

    def test_row_extras_override_parent_extras(self, monkeypatch, tmp_path):
        """A per-row ``extras:`` key wins over the parent default."""
        from earth2observe.ecmwf import catalog as catalog_module

        catalog_yaml = tmp_path / "cds_data_catalog.yaml"
        catalog_yaml.write_text(
            "datasets:\n"
            "  reanalysis-carra-single-levels:\n"
            "    extras:\n"
            "      domain: east\n"
            "      leadtime_hour: '1'\n"
            "    variables:\n"
            "      2m-temperature:\n"
            "        nc_variable: t2m\n"
            "        units: K\n"
            "        extras:\n"
            "          domain: west\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog_yaml)
        cat = Catalog()
        spec = cat.get_dataset("2m-temperature")
        assert spec.extras == {"domain": "west", "leadtime_hour": "1"}

    def test_extras_roundtrip_through_yaml(self, monkeypatch, tmp_path):
        """Arbitrary extras survive a YAML load-and-read round trip."""
        from earth2observe.ecmwf import catalog as catalog_module

        catalog_yaml = tmp_path / "cds_data_catalog.yaml"
        catalog_yaml.write_text(
            "datasets:\n"
            "  projections-cmip6:\n"
            "    extras:\n"
            "      experiment: ssp585\n"
            "      model: ec_earth3\n"
            "      temporal_resolution: monthly\n"
            "    variables:\n"
            "      near-surface-air-temperature:\n"
            "        nc_variable: tas\n"
            "        units: K\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", catalog_yaml)
        spec = Catalog().get_dataset("near-surface-air-temperature")
        assert spec.extras == {
            "experiment": "ssp585",
            "model": "ec_earth3",
            "temporal_resolution": "monthly",
        }

    def test_get_catalog_raises_on_empty_datasets(self, monkeypatch, tmp_path):
        """A YAML with no datasets raises ValueError."""
        empty_yaml = tmp_path / "cds_data_catalog.yaml"
        empty_yaml.write_text(
            "version: 3\navailable_datasets: []\n", encoding="utf-8"
        )
        from earth2observe.ecmwf import catalog as catalog_module

        monkeypatch.setattr(catalog_module, "CATALOG_PATH", empty_yaml)
        with pytest.raises(ValueError, match="datasets"):
            Catalog()

    def test_get_catalog_raises_on_null_datasets(self, monkeypatch, tmp_path):
        """A YAML with datasets: null also raises ValueError."""
        null_yaml = tmp_path / "cds_data_catalog.yaml"
        null_yaml.write_text("datasets:\n", encoding="utf-8")
        from earth2observe.ecmwf import catalog as catalog_module

        monkeypatch.setattr(catalog_module, "CATALOG_PATH", null_yaml)
        with pytest.raises(ValueError, match="datasets"):
            Catalog()

    def test_get_catalog_raises_when_no_variables_anywhere(self, monkeypatch, tmp_path):
        """A YAML with datasets but no variables under any of them raises."""
        no_vars = tmp_path / "cds_data_catalog.yaml"
        no_vars.write_text(
            "datasets:\n  reanalysis-era5-single-levels:\n    monthly: x\n    variables:\n",
            encoding="utf-8",
        )
        from earth2observe.ecmwf import catalog as catalog_module

        monkeypatch.setattr(catalog_module, "CATALOG_PATH", no_vars)
        with pytest.raises(ValueError, match="no variables"):
            Catalog()
