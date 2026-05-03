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

from earthly.ecmwf import Catalog

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
        """`download()` must not depend on a non-existent `self.variables`.

        Test scenario:
            Even if a future refactor accidentally reintroduces the
            wrong attribute name, this test fails fast: `self.vars`
            is set, `self.variables` is explicitly absent, and
            `download()` must complete without an `AttributeError`.
        """
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": ["2m-temperature"],
        }
        ecmwf_stub._download_dataset = MagicMock()
        assert not hasattr(ecmwf_stub, "variables")

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub._download_dataset.call_count == 1

    def test_download_continues_after_per_variable_failure(self, ecmwf_stub):
        """`download()` collects failures and continues to the next var.

        Test scenario:
            Pre-M3, a single failing variable aborted the whole loop —
            the user lost any minutes of CDS queue time spent on the
            successful variables that came before. M3 wraps each
            iteration in try/except so every variable is attempted,
            failures are logged, and the rest continues.
        """
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
        """`download()` no longer touches the hardcoded `data_interim.nc`.

        Test scenario:
            Pre-H3, `download()` ended with
            `os.remove(os.path.join(self.root_dir, "data_interim.nc"))`
            — a leftover from the MARS flow that always raised
            FileNotFoundError under the cdsapi path.
        """
        removed = []
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": ["2m-temperature"],
        }
        ecmwf_stub._download_dataset = MagicMock()
        monkeypatch.setattr(
            "earthly.ecmwf.backend.os.remove",
            lambda path: removed.append(path),
        )

        ecmwf_stub.download(progress_bar=False)

        assert removed == []


class TestDownloadDataset:
    """Tests for :meth:`ECMWF._download_dataset` after the C1 call-site fix."""

    def test_calls_api_with_var_info_only(self, ecmwf_stub, single_level_var_info):
        """`_download_dataset` invokes `_api(var_info)` with one arg.

        Test scenario:
            The C1 change dropped the `dataset` positional argument
            from `_api()`. `_download_dataset` must therefore pass
            only `var_info`.
        """
        ecmwf_stub._api = MagicMock(return_value=ecmwf_stub.root_dir / "x.nc")

        ecmwf_stub._download_dataset(single_level_var_info, progress_bar=False)

        assert ecmwf_stub._api.call_count == 1
        args, kwargs = ecmwf_stub._api.call_args
        assert kwargs == {}
        assert args == (single_level_var_info,)

    def test_returns_path_from_api(self, ecmwf_stub, single_level_var_info):
        """`_download_dataset` returns the path :meth:`_api` produced.

        Test scenario:
            After post-processing was lifted out of the package,
            `_download_dataset` collapsed to a thin pass-through
            around `_api()`. Callers receive the absolute
            :class:`pathlib.Path` so they can hand it to a
            post-processing script.
        """
        api_target = (
            ecmwf_stub.root_dir / "2m_temperature_reanalysis-era5-single-levels.nc"
        )
        ecmwf_stub._api = MagicMock(return_value=api_target)

        result = ecmwf_stub._download_dataset(single_level_var_info, progress_bar=True)

        assert result == api_target
