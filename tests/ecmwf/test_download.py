"""Unit tests for :meth:`ECMWF.download` and :meth:`ECMWF.download_dataset`.

Covers the C3 fix (iterate ``self.vars`` not ``self.variables``), the
H3 cleanup of the hardcoded ``data_interim.nc`` deletion, the M3
partial-success behaviour on per-variable failure, and the C1 call
site change in ``download_dataset`` (drops the legacy ``dataset``
arg, threads the path returned by ``api()`` into ``post_download``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from earth2observe.ecmwf import Catalog

pytestmark = [pytest.mark.unit]


class TestDownloadIteration:
    """Tests for :meth:`ECMWF.download` iteration (C3, M3, H3)."""

    def test_download_iterates_self_vars_not_self_variables(self, ecmwf_stub):
        """``download()`` iterates ``self.vars`` (the parent's storage).

        Test scenario:
            :class:`AbstractDataSource.__init__` stores the user's
            ``variables`` list as ``self.vars``. Pre-C3, ECMWF.download
            iterated ``self.variables`` instead, which raised
            ``AttributeError`` on the first call.
        """
        ecmwf_stub.vars = ["2T", "TP"]
        ecmwf_stub.download_dataset = MagicMock()

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub.download_dataset.call_count == 2
        called_with = [
            args[0] for args, _kwargs in ecmwf_stub.download_dataset.call_args_list
        ]
        assert called_with == [
            Catalog().get_dataset("2T"),
            Catalog().get_dataset("TP"),
        ]

    def test_download_does_not_read_self_variables(self, ecmwf_stub):
        """``download()`` must not depend on a non-existent ``self.variables``.

        Test scenario:
            Even if a future refactor accidentally reintroduces the
            wrong attribute name, this test fails fast: ``self.vars``
            is set, ``self.variables`` is explicitly absent, and
            ``download()`` must complete without an ``AttributeError``.
        """
        ecmwf_stub.vars = ["2T"]
        ecmwf_stub.download_dataset = MagicMock()
        assert not hasattr(ecmwf_stub, "variables")

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub.download_dataset.call_count == 1

    def test_download_continues_after_per_variable_failure(self, ecmwf_stub):
        """``download()`` collects failures and continues to the next var.

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

        ecmwf_stub.vars = ["2T", "TP", "E"]
        ecmwf_stub.download_dataset = flaky

        ecmwf_stub.download(progress_bar=False)

        assert attempted == [
            "2m_temperature",
            "total_precipitation",
            "evaporation",
        ]

    def test_download_does_not_attempt_to_delete_legacy_files(
        self, ecmwf_stub, monkeypatch
    ):
        """``download()`` no longer touches the hardcoded ``data_interim.nc``.

        Test scenario:
            Pre-H3, ``download()`` ended with
            ``os.remove(os.path.join(self.root_dir, "data_interim.nc"))``
            — a leftover from the MARS flow that always raised
            FileNotFoundError under the cdsapi path.
        """
        removed = []
        ecmwf_stub.vars = ["2T"]
        ecmwf_stub.download_dataset = MagicMock()
        monkeypatch.setattr(
            "earth2observe.ecmwf.os.remove",
            lambda path: removed.append(path),
        )

        ecmwf_stub.download(progress_bar=False)

        assert removed == []


class TestDownloadDataset:
    """Tests for :meth:`ECMWF.download_dataset` after the C1 call-site fix."""

    def test_calls_api_with_var_info_only(
        self, ecmwf_stub, single_level_var_info
    ):
        """``download_dataset`` invokes ``api(var_info)`` with one arg.

        Test scenario:
            The C1 change dropped the ``dataset`` positional argument
            from ``api()``. ``download_dataset`` must therefore pass
            only ``var_info``.
        """
        ecmwf_stub.api = MagicMock(return_value=ecmwf_stub.root_dir / "x.nc")
        ecmwf_stub.post_download = MagicMock()

        ecmwf_stub.download_dataset(single_level_var_info, progress_bar=False)

        assert ecmwf_stub.api.call_count == 1
        args, kwargs = ecmwf_stub.api.call_args
        assert kwargs == {}
        assert args == (single_level_var_info,)

    def test_post_download_receives_path_returned_by_api(
        self, ecmwf_stub, single_level_var_info
    ):
        """``post_download`` is called with the path :meth:`api` returned.

        Test scenario:
            After H1, ``download_dataset`` captures the
            :class:`pathlib.Path` returned by :meth:`api` and threads
            it into :meth:`post_download` so the post-processing step
            opens the very same NetCDF that cdsapi just wrote — not a
            hardcoded ``data_<dataset>.nc`` filename.
        """
        api_target = ecmwf_stub.root_dir / "Tair_reanalysis-era5-single-levels.nc"
        ecmwf_stub.api = MagicMock(return_value=api_target)
        ecmwf_stub.post_download = MagicMock()

        ecmwf_stub.download_dataset(single_level_var_info, progress_bar=True)

        assert ecmwf_stub.post_download.call_count == 1
        args, _ = ecmwf_stub.post_download.call_args
        assert args[0] == single_level_var_info
        assert args[1] == api_target
        assert args[2] is True
