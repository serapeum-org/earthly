"""Unit tests for :class:`earthly.ecmwf.Catalog`.

Covers the H2 / H5 rewiring (the catalog reads
`cds_data_catalog.yaml` and exposes per-variable
:class:`Variable` instances), the M2 fail-loud behaviour on
malformed YAML, and the no-MARS-keys invariant on the schema.
"""

from __future__ import annotations

import pytest

from earthly.ecmwf import Catalog, Variable

pytestmark = [pytest.mark.unit]


class TestCatalog:
    """Tests for :class:`Catalog` after the H2 / H5 / M1 / M2 work."""

    @pytest.mark.parametrize(
        "dataset_name, var_code, expected_variable",
        [
            (
                "reanalysis-era5-single-levels",
                "2m-temperature",
                "2m_temperature",
            ),
            (
                "reanalysis-era5-single-levels",
                "total-precipitation",
                "total_precipitation",
            ),
            (
                "reanalysis-era5-single-levels",
                "surface-pressure",
                "surface_pressure",
            ),
            (
                "reanalysis-era5-single-levels",
                "evaporation",
                "evaporation",
            ),
            (
                "reanalysis-era5-pressure-levels",
                "temperature",
                "temperature",
            ),
        ],
    )
    def test_get_variable_returns_new_schema(
        self, dataset_name, var_code, expected_variable
    ):
        """`get_variable(dataset, code)` returns the row for that pair."""
        spec = Catalog().get_variable(dataset_name, var_code)
        assert spec.cds_dataset == dataset_name
        assert spec.cds_variable == expected_variable

    def test_get_variable_returns_raw_era5_units(self):
        """2m-temperature carries the raw ERA5 unit (Kelvin)."""
        spec = Catalog().get_variable(
            "reanalysis-era5-single-levels", "2m-temperature"
        )
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

    def test_pressure_level_var_carries_cds_pressure_level(self):
        """Pressure-level variables expose `cds_pressure_level`.

        T, Q, R live on reanalysis-era5-pressure-levels; their
        catalog entries must carry the `cds_pressure_level`
        attribute so :meth:`ECMWF.api` can forward it to CDS.
        """
        spec = Catalog().get_variable(
            "reanalysis-era5-pressure-levels", "temperature"
        )
        assert spec.cds_pressure_level == ["1000"]

    def test_get_variable_raises_key_error_for_unknown_dataset(self):
        """Unknown dataset names raise `KeyError`."""
        with pytest.raises(KeyError):
            Catalog().get_variable("bogus-dataset", "2m-temperature")

    def test_get_variable_raises_key_error_for_unknown_code(self):
        """Unknown variable codes (under a real dataset) raise `KeyError`."""
        with pytest.raises(KeyError):
            Catalog().get_variable(
                "reanalysis-era5-single-levels", "DEFINITELY_NOT_A_REAL_CODE"
            )

    def test_same_code_under_different_datasets_is_distinct(self):
        """`(dataset, code)` is the identity; same code, different datasets."""
        cat = Catalog()
        single = cat.get_variable(
            "reanalysis-era5-single-levels", "2m-temperature"
        )
        land = cat.get_variable("reanalysis-era5-land", "2m-temperature")
        assert single.cds_dataset == "reanalysis-era5-single-levels"
        assert land.cds_dataset == "reanalysis-era5-land"
        assert single is not land

    def test_get_dataset_returns_dataset_object(self):
        """`get_dataset(name)` returns the structural :class:`Dataset`."""
        cat = Catalog()
        ds = cat.get_dataset("reanalysis-era5-pressure-levels")
        assert ds.monthly == "reanalysis-era5-pressure-levels-monthly-means"
        assert "temperature" in ds.variables

    def test_get_dataset_raises_key_error_for_unknown_dataset(self):
        """Unknown dataset names raise `KeyError`."""
        with pytest.raises(KeyError):
            Catalog().get_dataset("definitely-not-a-dataset")

    def test_no_mars_schema_keys_remain(self):
        """No Variable field is a stale MARS-style key."""
        forbidden = {"number_para", "download type", "var_name"}
        present = set(Variable.model_fields)
        assert not (forbidden & present)

    @pytest.mark.parametrize("mars_key", ["number_para", "download type", "var_name"])
    def test_no_mars_schema_keys_in_extras(self, monkeypatch, tmp_path, mars_key):
        """Legacy MARS keys are rejected inside `extras`."""
        from earthly.ecmwf import catalog as catalog_module

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
        """Parent `Dataset.extras` propagates into each child Variable."""
        from earthly.ecmwf import catalog as catalog_module

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
        spec = cat.get_variable(
            "reanalysis-carra-single-levels", "2m-temperature"
        )
        assert spec.extras == {"domain": "east", "leadtime_hour": "1"}
        assert cat.datasets["reanalysis-carra-single-levels"].extras == {
            "domain": "east",
            "leadtime_hour": "1",
        }

    def test_row_extras_override_parent_extras(self, monkeypatch, tmp_path):
        """A per-row `extras:` key wins over the parent default."""
        from earthly.ecmwf import catalog as catalog_module

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
        spec = cat.get_variable(
            "reanalysis-carra-single-levels", "2m-temperature"
        )
        assert spec.extras == {"domain": "west", "leadtime_hour": "1"}

    def test_era5_land_loads(self):
        """ERA5-Land block round-trips through `Catalog`.

        Asserts the dataset is exposed under `datasets`, carries the
        correct monthly-aggregate variant, and that one of its
        unique-to-ERA5-Land rows resolves to the expected metadata.
        """
        cat = Catalog()
        ds = cat.datasets["reanalysis-era5-land"]
        assert ds.monthly == "reanalysis-era5-land-monthly-means"
        assert "evaporation-from-bare-soil" in ds.variables
        spec = ds.variables["evaporation-from-bare-soil"]
        assert spec.cds_dataset == "reanalysis-era5-land"
        assert spec.cds_variable == "evaporation_from_bare_soil"
        assert spec.nc_variable == "evabs"
        assert spec.units == "m of water equivalent"
        assert spec.types == "flux"

    def test_era5_land_carries_60_variables(self):
        """ERA5-Land covers all 60 variables CDS reports for the dataset."""
        ds = Catalog().datasets["reanalysis-era5-land"]
        assert len(ds.variables) == 60

    def test_derived_era5_land_daily_statistics_loads(self):
        """derived-era5-land-daily-statistics block round-trips through `Catalog`."""
        cat = Catalog()
        ds = cat.datasets["derived-era5-land-daily-statistics"]
        assert ds.monthly is None
        assert len(ds.variables) == 31
        # Parent extras carry the request defaults required by the dataset.
        assert ds.extras == {
            "daily_statistic": "daily_mean",
            "frequency": "1_hourly",
            "time_zone": "utc+00:00",
        }
        spec = ds.variables["2m-temperature-daily"]
        assert spec.cds_dataset == "derived-era5-land-daily-statistics"
        assert spec.cds_variable == "2m_temperature"
        assert spec.nc_variable == "t2m"
        assert spec.units == "K"
        # Per-variable extras inherit the parent defaults.
        assert spec.extras == {
            "daily_statistic": "daily_mean",
            "frequency": "1_hourly",
            "time_zone": "utc+00:00",
        }

    def test_era5_daily_statistics_load(self):
        """Both ERA5 daily-statistics datasets round-trip through `Catalog`."""
        cat = Catalog()
        single = cat.datasets["derived-era5-single-levels-daily-statistics"]
        press = cat.datasets["derived-era5-pressure-levels-daily-statistics"]
        assert len(single.variables) == 262
        assert len(press.variables) == 16
        assert single.extras["daily_statistic"] == "daily_mean"
        assert press.extras["frequency"] == "1_hourly"
        assert press.pressure_level == ["1000"]
        # Spot-check a known mapping in each
        spec = single.variables["2m-temperature-daily"]
        assert spec.cds_variable == "2m_temperature"
        assert spec.nc_variable == "t2m"
        assert spec.units == "K"
        spec = press.variables["temperature-daily"]
        assert spec.cds_variable == "temperature"
        assert spec.nc_variable == "t"
        assert spec.cds_pressure_level == ["1000"]

    def test_minimal_valid_request_picks_entry_with_variable(self, monkeypatch):
        """`minimal_valid_request` returns a known-valid request dict."""
        from earthly.ecmwf import constraints as constraints_module

        constraints_module._CACHE.clear()

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                import json
                return json.dumps([
                    # Entry without variables — should be skipped
                    {"experiment": ["historical"], "year": ["2000"]},
                    # Entry with variables — should be picked
                    {
                        "variable": ["2m_temperature", "skin_temperature"],
                        "year": ["2022"],
                        "month": ["01"],
                        "level_type": ["surface_or_atmosphere"],
                    },
                ]).encode("utf-8")

        monkeypatch.setattr(
            constraints_module.urllib.request,
            "urlopen",
            lambda *_a, **_kw: _Resp(),
        )
        request = Catalog().minimal_valid_request("ds")
        assert request["data_format"] == "netcdf"
        assert request["variable"] == ["2m_temperature"]
        assert request["year"] == ["2022"]
        assert request["month"] == ["01"]
        assert request["level_type"] == ["surface_or_atmosphere"]

    def test_minimal_valid_request_falls_back_for_no_variable_datasets(
        self, monkeypatch
    ):
        """For datasets without a `variable` field, return the first entry."""
        from earthly.ecmwf import constraints as constraints_module

        constraints_module._CACHE.clear()

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                import json
                # No entry has `variable` — caller gets first entry expanded
                return json.dumps([
                    {"cdr_type": ["esa_cci"], "region": ["nh"]},
                    {"cdr_type": ["osi_saf"], "region": ["sh"]},
                ]).encode("utf-8")

        monkeypatch.setattr(
            constraints_module.urllib.request,
            "urlopen",
            lambda *_a, **_kw: _Resp(),
        )
        request = Catalog().minimal_valid_request("satellite-no-variable-ds")
        assert request["cdr_type"] == ["esa_cci"]
        assert request["region"] == ["nh"]

    def test_minimal_valid_request_empty_constraints(self, monkeypatch):
        """Empty constraints return a near-empty request (just data_format)."""
        from earthly.ecmwf import constraints as constraints_module

        constraints_module._CACHE.clear()

        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                return b"[]"

        monkeypatch.setattr(
            constraints_module.urllib.request,
            "urlopen",
            lambda *_a, **_kw: _Resp(),
        )
        request = Catalog().minimal_valid_request("non-addressable")
        assert request == {"data_format": "netcdf"}

    def test_list_recent_jobs_filters_by_age_and_status(
        self, monkeypatch, tmp_path
    ):
        """`list_recent_jobs` returns jobs within `max_age_min` only."""
        import datetime

        rc = tmp_path / ".cdsapirc"
        rc.write_text(
            "url: https://example.invalid/api\nkey: tok\n", encoding="utf-8"
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        recent = (now - datetime.timedelta(minutes=10)).isoformat()
        old = (now - datetime.timedelta(minutes=120)).isoformat()
        payload = {
            "jobs": [
                {"jobID": "abc", "processID": "ds-a", "status": "successful", "created": recent},
                {"jobID": "def", "processID": "ds-b", "status": "successful", "created": old},
            ]
        }

        class _Resp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return payload

        import earthly.ecmwf.catalog as cat_mod
        captured = {}

        def _fake_get(url, headers=None, params=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            return _Resp()

        import requests as _req
        monkeypatch.setattr(_req, "get", _fake_get)
        jobs = Catalog().list_recent_jobs(status="successful", max_age_min=60)
        assert len(jobs) == 1
        assert jobs[0]["jobID"] == "abc"
        assert captured["params"]["status"] == "successful"

    def test_download_job_skips_if_target_exists(
        self, monkeypatch, tmp_path
    ):
        """`download_job` is idempotent when the target file is already there."""
        rc = tmp_path / ".cdsapirc"
        rc.write_text(
            "url: https://example.invalid/api\nkey: tok\n", encoding="utf-8"
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        target = tmp_path / "x.nc"
        target.write_bytes(b"already here")
        # No mocked HTTP — proves we don't reach for the network.
        result = Catalog().download_job("any-job-id", target)
        assert result == target
        assert target.read_bytes() == b"already here"

    def test_download_job_raises_when_no_asset_href(
        self, monkeypatch, tmp_path
    ):
        """`download_job` raises ValueError when results lack an asset href."""
        rc = tmp_path / ".cdsapirc"
        rc.write_text(
            "url: https://example.invalid/api\nkey: tok\n", encoding="utf-8"
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

        class _Resp:
            def raise_for_status(self): pass
            def json(self): return {"asset": {"value": {}}}  # no href

        import requests as _req
        monkeypatch.setattr(_req, "get", lambda *a, **kw: _Resp())
        with pytest.raises(ValueError, match="no downloadable asset href"):
            Catalog().download_job("xyz", tmp_path / "out.nc")

    def test_describe_returns_dataset_metadata(self):
        """`Catalog.describe` returns a structured introspection record."""
        cat = Catalog()
        info = cat.describe("reanalysis-era5-land")
        assert info["dataset"] == "reanalysis-era5-land"
        assert info["monthly"] == "reanalysis-era5-land-monthly-means"
        assert info["pressure_level"] is None
        assert info["extras"] == {}
        assert "2m-temperature" in info["variables"]
        assert len(info["variables"]) == 60

    def test_describe_includes_parent_extras(self):
        """`describe` surfaces the dataset-level extras (e.g. ORAS5)."""
        info = Catalog().describe("reanalysis-oras5")
        assert info["extras"] == {"product_type": ["consolidated"]}
        assert len(info["variables"]) == 27

    def test_describe_raises_for_unknown_dataset(self):
        """Unknown dataset names raise `KeyError`."""
        with pytest.raises(KeyError):
            Catalog().describe("definitely-not-a-dataset")

    def test_era5_land_monthly_means_routing(self):
        """ERA5-Land's `monthly:` link routes to the monthly-means dataset.

        M23: confirms the parent dataset's `monthly:` field
        propagates into each Variable's `cds_dataset_monthly`,
        so `Variable.dataset_for("monthly")` returns the
        `-monthly-means` variant.
        """
        cat = Catalog()
        ds = cat.datasets["reanalysis-era5-land"]
        assert ds.monthly == "reanalysis-era5-land-monthly-means"
        spec = ds.variables["2m-temperature"]
        assert spec.cds_dataset_monthly == "reanalysis-era5-land-monthly-means"
        assert spec.dataset_for("daily") == "reanalysis-era5-land"
        assert spec.dataset_for("monthly") == "reanalysis-era5-land-monthly-means"

    def test_era5_pressure_levels_monthly_means_routing(self):
        """M24: ERA5 pressure-levels routes monthly to its -monthly-means variant."""
        cat = Catalog()
        ds = cat.datasets["reanalysis-era5-pressure-levels"]
        assert ds.monthly == "reanalysis-era5-pressure-levels-monthly-means"
        spec = ds.variables["temperature"]
        assert spec.dataset_for("daily") == "reanalysis-era5-pressure-levels"
        assert spec.dataset_for("monthly") == "reanalysis-era5-pressure-levels-monthly-means"

    def test_era5_single_levels_monthly_means_routing(self):
        """M25: ERA5 single-levels routes monthly to its -monthly-means variant."""
        cat = Catalog()
        ds = cat.datasets["reanalysis-era5-single-levels"]
        assert ds.monthly == "reanalysis-era5-single-levels-monthly-means"
        spec = ds.variables["2m-temperature"]
        assert spec.dataset_for("daily") == "reanalysis-era5-single-levels"
        assert spec.dataset_for("monthly") == "reanalysis-era5-single-levels-monthly-means"

    def test_carra_means_partial_loads(self):
        """CARRA-means partial block (6 forecast-based single-level vars + 1 analysis_based override)."""
        cat = Catalog()
        ds = cat.datasets["reanalysis-carra-means"]
        assert ds.request_kind == "carra_means"
        assert ds.extras["product_type"] == ["forecast_based"]
        assert ds.extras["time_aggregation"] == "daily"
        assert len(ds.variables) == 112
        spec = ds.variables["maximum-2m-temperature-carra-means"]
        assert spec.cds_variable == "maximum_2m_temperature_since_previous_post_processing"
        assert spec.nc_variable == "mx2t"
        assert spec.units == "K"
        # Per-row extras override: analysis_based var flips product_type.
        analysis_spec = ds.variables["2m-specific-humidity-carra-means"]
        assert analysis_spec.extras["product_type"] == ["analysis_based"]

    def test_seasonal_monthly_single_partial_loads(self):
        """Seasonal monthly-single partial block (11 of 38 vars)."""
        cat = Catalog()
        ds = cat.datasets["seasonal-monthly-single-levels"]
        assert ds.extras["originating_centre"] == "ecmwf"
        assert ds.extras["system"] == "5"
        assert len(ds.variables) == 38
        spec = ds.variables["2m-temperature-seasonal"]
        assert spec.cds_variable == "2m_temperature"
        assert spec.nc_variable == "t2m"

    def test_carra_loads(self):
        """CARRA family: pressure, height, model, single-levels round-trip."""
        cat = Catalog()
        press = cat.datasets["reanalysis-carra-pressure-levels"]
        height = cat.datasets["reanalysis-carra-height-levels"]
        model = cat.datasets["reanalysis-carra-model-levels"]
        single = cat.datasets["reanalysis-carra-single-levels"]
        assert len(press.variables) == 14
        assert len(height.variables) == 7
        assert len(model.variables) == 11
        assert len(single.variables) == 67
        # Parent extras propagate to every variable.
        for ds in [press, height, model, single]:
            assert ds.extras["domain"] == "east_domain"
            assert ds.extras["product_type"] == ["analysis"]
        # Spot-check: CARRA pressure-levels temperature -> t (K)
        spec = press.variables["temperature-carra"]
        assert spec.cds_variable == "temperature"
        assert spec.nc_variable == "t"
        assert spec.cds_pressure_level == ["1000"]
        # Height-levels variables carry the height_level extra.
        spec = height.variables["temperature-carra-h"]
        assert spec.extras["height_level"] == ["100_m"]
        # Model-levels carry the model_level extra.
        spec = model.variables["temperature-carra-m"]
        assert spec.extras["model_level"] == ["1"]

    def test_cmip5_monthly_loads(self):
        """CMIP5 monthly single-levels + pressure-levels round-trip."""
        cat = Catalog()
        single = cat.datasets["projections-cmip5-monthly-single-levels"]
        press = cat.datasets["projections-cmip5-monthly-pressure-levels"]
        assert len(single.variables) == 9
        assert len(press.variables) == 5
        assert single.extras["model"] == "ec_earth"
        assert single.extras["experiment"] == "historical"
        spec = single.variables["2m-temperature-cmip5m"]
        assert spec.cds_variable == "2m_temperature"
        assert spec.nc_variable == "tas"
        spec = press.variables["temperature-cmip5m"]
        assert spec.cds_variable == "temperature"
        assert spec.nc_variable == "ta"
        assert spec.cds_pressure_level == ["1000"]

    def test_cordex_loads(self):
        """CORDEX block round-trips through `Catalog`.

        Asserts the dataset is exposed, the parent extras carry the
        EURO-CORDEX EC-Earth/RACMO22E historical defaults, and a
        sample variable resolves to its CMOR short name.
        """
        cat = Catalog()
        ds = cat.datasets["projections-cordex-domains-single-levels"]
        assert ds.extras["domain"] == "europe"
        assert ds.extras["gcm_model"] == "ichec_ec_earth"
        assert ds.extras["rcm_model"] == "knmi_racmo22e"
        assert ds.extras["experiment"] == "historical"
        spec = ds.variables["2m-air-temperature-cordex"]
        assert spec.cds_dataset == "projections-cordex-domains-single-levels"
        assert spec.cds_variable == "2m_air_temperature"
        assert spec.nc_variable == "tas"
        assert spec.units == "K"
        # Parent extras propagate to every variable row.
        assert spec.extras["gcm_model"] == "ichec_ec_earth"

    def test_cordex_carries_16_confirmed_variables(self):
        """CORDEX ships 16 of 25 catalogued variables (probe-confirmed)."""
        ds = Catalog().datasets["projections-cordex-domains-single-levels"]
        assert len(ds.variables) == 16
        # All variable keys end with the `-cordex` suffix to avoid
        # colliding with the same-named ERA5 single-levels rows.
        for code in ds.variables:
            assert code.endswith("-cordex")

    def test_oras5_loads(self):
        """ORAS5 ocean reanalysis block round-trips through `Catalog`.

        Asserts the dataset is exposed under `datasets`, has no
        monthly variant (it is monthly-only by design), and a known
        single-level variable resolves to the expected NEMO short name.
        """
        cat = Catalog()
        ds = cat.datasets["reanalysis-oras5"]
        assert ds.monthly is None
        assert len(ds.variables) == 27
        spec = ds.variables["sea-ice-thickness"]
        assert spec.cds_dataset == "reanalysis-oras5"
        assert spec.cds_variable == "sea_ice_thickness"
        assert spec.nc_variable == "iicethic"
        assert spec.units == "m"
        assert spec.extras["vertical_resolution"] == "single_level"
        assert spec.extras["product_type"] == ["consolidated"]

    def test_oras5_carries_oceanic_monthly_request_kind(self):
        """ORAS5 declares `request_kind=oceanic_monthly` so api() can strip
        ERA5-specific defaults at retrieve time."""
        cat = Catalog()
        ds = cat.datasets["reanalysis-oras5"]
        assert ds.request_kind == "oceanic_monthly"
        # Propagated to every variable row.
        for var in ds.variables.values():
            assert var.request_kind == "oceanic_monthly"

    def test_default_request_kind_is_form(self):
        """Datasets that don't set `request_kind` default to `form`."""
        cat = Catalog()
        ds = cat.datasets["reanalysis-era5-single-levels"]
        assert ds.request_kind == "form"
        spec = ds.variables["2m-temperature"]
        assert spec.request_kind == "form"

    def test_oras5_all_levels_variables(self):
        """ORAS5's six 3-D fields override the parent default with all_levels."""
        ds = Catalog().datasets["reanalysis-oras5"]
        all_levels_vars = {
            code: var
            for code, var in ds.variables.items()
            if var.extras["vertical_resolution"] == "all_levels"
        }
        assert set(all_levels_vars) == {
            "meridional-velocity",
            "potential-temperature",
            "rotated-meridional-velocity",
            "rotated-zonal-velocity",
            "salinity",
            "zonal-velocity",
        }
        assert all_levels_vars["potential-temperature"].nc_variable == "votemper"
        assert all_levels_vars["salinity"].nc_variable == "vosaline"

    def test_era5_land_snow_depth_uses_sde_not_sd(self):
        """ERA5-Land's snow_depth maps to `sde` (m), not `sd` (m water equiv).

        ERA5-Land returns physical snow thickness as `sde` while
        single-levels uses `sd` for the water-equivalent depth. The
        two are distinct fields and must not collide in the catalog.
        """
        cat = Catalog()
        land_sd = cat.datasets["reanalysis-era5-land"].variables["snow-depth"]
        assert land_sd.nc_variable == "sde"
        assert land_sd.units == "m"
        land_sdwe = cat.datasets["reanalysis-era5-land"].variables[
            "snow-depth-water-equivalent"
        ]
        assert land_sdwe.nc_variable == "sd"
        assert land_sdwe.units == "m of water equivalent"

    def test_extras_roundtrip_through_yaml(self, monkeypatch, tmp_path):
        """Arbitrary extras survive a YAML load-and-read round trip."""
        from earthly.ecmwf import catalog as catalog_module

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
        spec = Catalog().get_variable(
            "projections-cmip6", "near-surface-air-temperature"
        )
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
        from earthly.ecmwf import catalog as catalog_module

        monkeypatch.setattr(catalog_module, "CATALOG_PATH", empty_yaml)
        with pytest.raises(ValueError, match="datasets"):
            Catalog()

    def test_get_catalog_raises_on_null_datasets(self, monkeypatch, tmp_path):
        """A YAML with datasets: null also raises ValueError."""
        null_yaml = tmp_path / "cds_data_catalog.yaml"
        null_yaml.write_text("datasets:\n", encoding="utf-8")
        from earthly.ecmwf import catalog as catalog_module

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
        from earthly.ecmwf import catalog as catalog_module

        monkeypatch.setattr(catalog_module, "CATALOG_PATH", no_vars)
        with pytest.raises(ValueError, match="no variables"):
            Catalog()
