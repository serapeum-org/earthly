"""Unit tests for :meth:`ECMWF.download` and :meth:`ECMWF._download_dataset`.

Covers the C3 fix (iterate `self.vars` not `self.variables`), the
H3 cleanup of the hardcoded `data_interim.nc` deletion, the M3
partial-success behaviour on per-variable failure, and the C1 call
site change in `_download_dataset` (drops the legacy `dataset`
arg, returns the path that `_api()` produces).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from earthlens.ecmwf import Catalog

pytestmark = [pytest.mark.unit]


class TestDownloadIteration:
    """Tests for :meth:`ECMWF.download` iteration (C3, M3, H3)."""

    def test_download_iterates_self_vars_not_self_variables(self, ecmwf_stub):
        """`download()` iterates `self.vars` (the parent's storage).

        :class:`AbstractDataSource.__init__` stores the user's
        `variables` mapping as `self.vars`. `download()` walks the
        `(dataset, [vars])` pairs and calls `_download_dataset` once
        per `(dataset, var_code)` pair.
        """
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": [
                "2m-temperature",
                "total-precipitation",
            ],
        }
        ecmwf_stub._download_dataset = MagicMock()

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub._download_dataset.call_count == 2
        called_with = [
            args[0] for args, _kwargs in ecmwf_stub._download_dataset.call_args_list
        ]
        cat = Catalog()
        assert called_with == [
            cat.get_variable("reanalysis-era5-single-levels", "2m-temperature"),
            cat.get_variable("reanalysis-era5-single-levels", "total-precipitation"),
        ]

    def test_download_does_not_read_self_variables(self, ecmwf_stub):
        """`download()` must not depend on a non-existent `self.variables`."""
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": ["2m-temperature"],
        }
        ecmwf_stub._download_dataset = MagicMock()
        assert not hasattr(ecmwf_stub, "variables")

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub._download_dataset.call_count == 1

    def test_download_continues_after_per_variable_failure(self, ecmwf_stub):
        """`download()` collects failures and continues to the next var."""
        attempted = []

        def flaky(var_info, progress_bar):
            attempted.append(var_info.cds_variable)
            if var_info.cds_variable == "total_precipitation":
                raise RuntimeError("simulated CDS 503")

        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": [
                "2m-temperature",
                "total-precipitation",
                "evaporation",
            ],
        }
        ecmwf_stub._download_dataset = flaky

        ecmwf_stub.download(progress_bar=False)

        assert attempted == [
            "2m_temperature",
            "total_precipitation",
            "evaporation",
        ]

    def test_download_does_not_attempt_to_delete_legacy_files(
        self, ecmwf_stub, monkeypatch
    ):
        """`download()` no longer touches the hardcoded `data_interim.nc`."""
        removed = []
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": ["2m-temperature"],
        }
        ecmwf_stub._download_dataset = MagicMock()
        monkeypatch.setattr(
            "earthlens.ecmwf.backend.os.remove",
            lambda path: removed.append(path),
        )

        ecmwf_stub.download(progress_bar=False)

        assert removed == []

    def test_empty_vars_completes_without_retrieves(self, ecmwf_stub):
        """An empty `self.vars` dict is a clean no-op — no retrieves, no failures."""
        ecmwf_stub.vars = {}
        ecmwf_stub._download_dataset = MagicMock()

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub._download_dataset.call_count == 0, (
            f"No retrieves should occur with empty self.vars; "
            f"got {ecmwf_stub._download_dataset.call_count}"
        )


class TestDownloadDataset:
    """Tests for :meth:`ECMWF._download_dataset` after the C1 call-site fix."""

    def test_calls_api_with_var_info_only(self, ecmwf_stub, single_level_var_info):
        """`_download_dataset` invokes `_api(var_info)` with one arg."""
        ecmwf_stub._api = MagicMock(return_value=ecmwf_stub.root_dir / "x.nc")

        ecmwf_stub._download_dataset(single_level_var_info, progress_bar=False)

        assert ecmwf_stub._api.call_count == 1
        args, kwargs = ecmwf_stub._api.call_args
        assert kwargs == {}
        assert args == (single_level_var_info,)

    def test_returns_path_from_api(self, ecmwf_stub, single_level_var_info):
        """`_download_dataset` returns the path :meth:`_api` produced."""
        api_target = (
            ecmwf_stub.root_dir / "2m_temperature_reanalysis-era5-single-levels.nc"
        )
        ecmwf_stub._api = MagicMock(return_value=api_target)

        result = ecmwf_stub._download_dataset(single_level_var_info, progress_bar=True)

        assert result == api_target
