"""Unit tests for :meth:`ECMWF.post_download`.

Covers the H1+H2+M2 rewrite (read the path threaded from api(), use
the new schema keys), the H3+M5 numerical pipeline assertions on the
flux scaling branch, and the M1 schema validation that rejects the
legacy ``"file name"`` (with space) key.

All tests use the in-memory :class:`_FakeNetCDFDataset` from
``_fakes.py`` so the suite runs without touching the file system or
the real netCDF4 / pyramids libraries.
"""

from __future__ import annotations

from dataclasses import replace as _dataclass_replace

import numpy as np
import pandas as pd
import pytest

from earth2observe.ecmwf import ECMWF, VariableSpec

from tests.ecmwf._fakes import install_fake_netcdf

pytestmark = [pytest.mark.unit]


class TestPostDownload:
    """Tests for the H1+H2+M2 rewrite of :meth:`ECMWF.post_download`."""

    def test_post_download_opens_path_returned_by_api(
        self, ecmwf_stub, single_level_var_info, monkeypatch, tmp_path
    ):
        """``post_download`` opens the path it was given, not a hardcoded one.

        Test scenario:
            After H1, ``post_download`` reads the NetCDF at the path
            argument it receives — no ``os.path.join(self.root_dir,
            f"data_{dataset}.nc")`` reconstruction.
        """
        instances = install_fake_netcdf(monkeypatch)
        nc_path = tmp_path / "Tair_reanalysis-era5-single-levels.nc"

        ecmwf_stub.post_download(
            single_level_var_info, nc_path, progress_bar=False
        )

        assert len(instances) == 1
        opened_path, mode = instances[0]
        assert opened_path == str(nc_path)
        assert mode == "r"

    def test_post_download_uses_nc_variable_not_var_name(
        self, ecmwf_stub, single_level_var_info, monkeypatch, tmp_path
    ):
        """``post_download`` indexes ``fh.variables[var_info.nc_variable]``.

        Test scenario:
            Pre-H2/M1, the function used ``var_info.get("var_name")``
            (a MARS-only key) which always resolved to ``None`` —
            ``fh.variables[None]`` raised. The new code reads
            ``var_info.nc_variable``.
        """
        install_fake_netcdf(monkeypatch)

        ecmwf_stub.post_download(
            single_level_var_info,
            tmp_path / "out.nc",
            progress_bar=False,
        )

    def test_variable_spec_rejects_legacy_spaced_file_name_key(self):
        """:meth:`VariableSpec.from_dict` rejects the legacy ``"file name"`` key.

        Test scenario:
            Pre-M1 the post_download lookup was the only line that
            knew about the typo'd legacy key. M1's
            :meth:`VariableSpec.from_dict` enforces the schema at
            load time: any unknown key (including the spaced
            ``"file name"`` from the MARS catalog) raises
            ``ValueError`` immediately.
        """
        with pytest.raises(ValueError, match="file name"):
            VariableSpec.from_dict(
                "2T",
                {
                    "cds_dataset": "reanalysis-era5-single-levels",
                    "cds_variable": "2m_temperature",
                    "nc_variable": "t2m",
                    "file name": "Tair",  # legacy spaced key
                    "units": "C",
                    "factors_add": -273.15,
                    "factors_mul": 1,
                },
            )

    def test_post_download_raises_on_missing_required_keys(
        self, ecmwf_stub, monkeypatch, tmp_path
    ):
        """Bare-dict callers raise ``AttributeError`` immediately.

        Test scenario:
            With M1, the dataclass guarantees presence of every
            consumed key at construction. The only way to reach
            ``post_download`` with a missing field is to pass a bare
            dict (a legacy-test-style invocation). The function still
            raises ``AttributeError`` on a bare dict, exposing the
            mistake.
        """
        install_fake_netcdf(monkeypatch)
        var_info_missing = {
            "units": "C",
            "file_name": "Tair",
            "factors_add": 0.0,
            "factors_mul": 1.0,
        }

        with pytest.raises(AttributeError, match="nc_variable"):
            ecmwf_stub.post_download(
                var_info_missing,
                tmp_path / "out.nc",
                progress_bar=False,
            )

    def test_post_download_flux_path_multiplies_by_days(
        self, ecmwf_stub, single_level_var_info, monkeypatch, tmp_path
    ):
        """``types='flux'`` triggers the ``Data_end *= days_later`` step.

        Test scenario:
            Half the catalog is flux variables (TP, E, RO, SRO,
            SSRO, SSR). With daily resolution the multiplier is 1
            (no-op for flux), with monthly resolution the multiplier
            equals the days in the month. Compares the flux outputs
            against state outputs to prove the difference is exactly
            ``days_later``.
        """
        spec_state = _dataclass_replace(
            single_level_var_info, factors_add=0, factors_mul=1, types="state"
        )
        spec_flux = _dataclass_replace(spec_state, types="flux")

        install_fake_netcdf(monkeypatch, var_value=10.0)

        ecmwf_stub.temporal_resolution = "daily"
        state_daily = ecmwf_stub.post_download(
            spec_state, tmp_path / "out.nc", progress_bar=False
        )

        install_fake_netcdf(monkeypatch, var_value=10.0)
        flux_daily = ecmwf_stub.post_download(
            spec_flux, tmp_path / "out.nc", progress_bar=False
        )

        for (_d_state, arr_state, _), (_d_flux, arr_flux, _) in zip(
            state_daily, flux_daily
        ):
            assert (arr_flux == arr_state).all(), (
                "daily flux multiplier should be 1; got differing arrays"
            )

        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.time = ecmwf_stub.time.model_copy(
            update={
                "dates": pd.date_range("2022-01-01", "2022-01-01", freq="MS"),
            }
        )
        install_fake_netcdf(monkeypatch, var_value=10.0)
        flux_monthly = ecmwf_stub.post_download(
            spec_flux, tmp_path / "out.nc", progress_bar=False
        )

        assert len(flux_monthly) == 1
        _, arr_jan, _ = flux_monthly[0]
        assert np.allclose(arr_jan, 310.0), (
            f"flux monthly should be base * days_in_month "
            f"(10 * 31 = 310); got mean {float(np.nanmean(arr_jan))}"
        )

    def test_post_download_does_not_carry_legacy_signature(self):
        """The signature is ``post_download(var_info, nc_path, progress_bar)``.

        Test scenario:
            The pre-H1 signature was ``(var_info, out_dir, dataset,
            progress_bar)``. Pin the new shape so a future refactor
            that re-introduces the ``dataset`` positional argument
            fails this test instead of silently working with the
            wrong meaning.
        """
        import inspect

        sig = inspect.signature(ECMWF.post_download)
        params = list(sig.parameters)
        assert params == ["self", "var_info", "nc_path", "progress_bar"]

    def test_download_and_download_dataset_signatures_drop_dataset(self):
        """``download`` / ``download_dataset`` no longer accept ``dataset``.

        Test scenario:
            Pre-M1, both methods carried ``dataset: str = "interim"``
            as a leftover from the MARS flow that no downstream code
            consumed. After M1 the parameter is removed entirely.
        """
        import inspect

        download_params = list(
            inspect.signature(ECMWF.download).parameters
        )
        assert "dataset" not in download_params

        download_dataset_params = list(
            inspect.signature(ECMWF.download_dataset).parameters
        )
        assert download_dataset_params == [
            "self",
            "var_info",
            "progress_bar",
        ]
