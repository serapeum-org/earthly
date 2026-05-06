"""Integration tests for `ECMWF.download(aggregate=...)` (H8).

Exercises the H6 wiring end-to-end against a stub ECMWF backend:
- `_api` is monkey-patched to return a path without hitting CDS.
- `aggregate_netcdf` is monkey-patched to record the calls so we
  can assert what reached it without a real on-disk NetCDF.
- The `aggregated/` default `out_dir` behaviour is validated.
- Failure isolation across variables is validated (one variable's
  aggregate crash does not abort the rest).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from earthly.aggregate import AggregationConfig
from earthly.ecmwf import ECMWF

pytestmark = [pytest.mark.unit]


@pytest.fixture
def aggregate_recorder(monkeypatch):
    """Replace `aggregate_netcdf` in the backend module with a recorder.

    Yields a list that captures `(nc_path, var_info, config)` tuples
    in the order `ECMWF.download` calls into the aggregator. Tests
    inspect the list to verify what was forwarded.

    Yields:
        list[tuple]: Per-call record. Cleared on entry; each call by
        the production code appends one tuple.
    """
    calls: list[tuple] = []

    def _recorder(nc_path, var_info, config):
        """Append the call args; mimic the real return shape."""
        calls.append((nc_path, var_info, config))
        return []

    monkeypatch.setattr(
        "earthly.ecmwf.backend.aggregate_netcdf",
        _recorder,
    )
    return calls


@pytest.fixture
def stubbed_download(ecmwf_stub, monkeypatch, tmp_path):
    """Wire `ecmwf_stub` for a `download()` invocation without hitting CDS.

    Sets `self.vars` to a single (dataset, variable) pair, replaces
    `_download_dataset` with a stub returning a synthetic NetCDF
    path, and silences the loguru summary log.

    Returns:
        ECMWF: The configured stub instance, ready for `download()`.
    """
    ecmwf_stub.vars = {
        "reanalysis-era5-single-levels": ["2m-temperature"],
    }
    nc_path = tmp_path / "2m_temperature_reanalysis-era5-single-levels.nc"
    ecmwf_stub._download_dataset = MagicMock(return_value=nc_path)
    return ecmwf_stub


class TestDownloadAggregateIntegration:
    """Tests for the H6 `aggregate=` parameter on `ECMWF.download`."""

    def test_aggregate_none_skips_aggregator(
        self, stubbed_download, aggregate_recorder
    ):
        """`download(aggregate=None)` does not invoke the aggregator."""
        stubbed_download.download(progress_bar=False)
        assert aggregate_recorder == [], (
            f"Aggregator should not be invoked when aggregate=None; "
            f"got {len(aggregate_recorder)} calls"
        )

    def test_aggregate_config_invokes_aggregator_per_variable(
        self, stubbed_download, aggregate_recorder, tmp_path
    ):
        """Each retrieved variable triggers exactly one aggregator call."""
        cfg = AggregationConfig(freq="1MS", op="mean")
        stubbed_download.download(progress_bar=False, aggregate=cfg)

        assert len(aggregate_recorder) == 1, (
            f"Expected exactly 1 aggregator call, got {len(aggregate_recorder)}"
        )
        nc_path, var_info, eff_cfg = aggregate_recorder[0]
        assert (
            nc_path
            == tmp_path / "2m_temperature_reanalysis-era5-single-levels.nc"
        ), f"Aggregator received wrong nc_path: {nc_path}"
        assert var_info.cds_variable == "2m_temperature", (
            f"Aggregator received wrong var_info: cds_variable="
            f"{var_info.cds_variable!r}"
        )
        assert eff_cfg.freq == "1MS", (
            f"Aggregator received wrong freq: {eff_cfg.freq!r}"
        )
        assert eff_cfg.op == "mean", (
            f"Aggregator received wrong op: {eff_cfg.op!r}"
        )

    def test_default_out_dir_is_root_dir_aggregated(
        self, stubbed_download, aggregate_recorder, tmp_path
    ):
        """When `aggregate.out_dir` is None, it is defaulted to `<root_dir>/aggregated`."""
        cfg = AggregationConfig(freq="1D")
        assert cfg.out_dir is None
        stubbed_download.download(progress_bar=False, aggregate=cfg)

        _, _, eff_cfg = aggregate_recorder[0]
        assert eff_cfg.out_dir == tmp_path / "aggregated", (
            f"Default out_dir should be <root_dir>/aggregated; "
            f"got {eff_cfg.out_dir}"
        )

    def test_explicit_out_dir_is_preserved(
        self, stubbed_download, aggregate_recorder, tmp_path
    ):
        """An explicit `out_dir` survives untouched."""
        explicit = tmp_path / "user_chosen"
        cfg = AggregationConfig(freq="1D", out_dir=explicit)
        stubbed_download.download(progress_bar=False, aggregate=cfg)

        _, _, eff_cfg = aggregate_recorder[0]
        assert eff_cfg.out_dir == explicit, (
            f"Explicit out_dir should be preserved; got {eff_cfg.out_dir}"
        )

    def test_aggregate_failure_does_not_abort_remaining_variables(
        self, ecmwf_stub, monkeypatch, tmp_path
    ):
        """A crash in `aggregate_netcdf` for one variable does not stop the rest."""
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": [
                "2m-temperature",
                "total-precipitation",
            ],
        }
        nc_paths = [
            tmp_path / "2m_temperature_reanalysis-era5-single-levels.nc",
            tmp_path / "total_precipitation_reanalysis-era5-single-levels.nc",
        ]
        ecmwf_stub._download_dataset = MagicMock(side_effect=nc_paths)

        seen = []

        def _flaky(nc_path, var_info, config):
            """Crash on the first variable; succeed on the rest."""
            seen.append(var_info.cds_variable)
            if var_info.cds_variable == "2m_temperature":
                raise RuntimeError("simulated reduce failure")
            return []

        monkeypatch.setattr(
            "earthly.ecmwf.backend.aggregate_netcdf",
            _flaky,
        )
        ecmwf_stub.download(
            progress_bar=False,
            aggregate=AggregationConfig(freq="1D"),
        )

        assert seen == ["2m_temperature", "total_precipitation"], (
            f"Both aggregator calls must still happen despite the first "
            f"crashing; got {seen}"
        )
        assert ecmwf_stub._download_dataset.call_count == 2, (
            f"Both retrieves must still happen; got "
            f"{ecmwf_stub._download_dataset.call_count}"
        )

    def test_multi_variable_happy_path_invokes_aggregator_per_variable(
        self, ecmwf_stub, monkeypatch, tmp_path
    ):
        """Two healthy variables produce two aggregator calls in order."""
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": [
                "2m-temperature",
                "total-precipitation",
            ],
        }
        nc_paths = [
            tmp_path / "2m_temperature_reanalysis-era5-single-levels.nc",
            tmp_path / "total_precipitation_reanalysis-era5-single-levels.nc",
        ]
        ecmwf_stub._download_dataset = MagicMock(side_effect=nc_paths)

        recorder: list = []

        def _record(nc_path, var_info, config):
            recorder.append((nc_path, var_info.cds_variable))
            return []

        monkeypatch.setattr(
            "earthly.ecmwf.backend.aggregate_netcdf",
            _record,
        )
        ecmwf_stub.download(
            progress_bar=False,
            aggregate=AggregationConfig(freq="1D"),
        )

        assert len(recorder) == 2, (
            f"Expected 2 aggregator calls (one per variable); "
            f"got {len(recorder)}"
        )
        assert [name for _, name in recorder] == [
            "2m_temperature",
            "total_precipitation",
        ], f"Aggregator should see variables in iteration order; got {recorder}"
        assert [path for path, _ in recorder] == nc_paths, (
            f"Each aggregator call should receive its variable's NetCDF "
            f"path; got {recorder}"
        )

    def test_retrieve_failure_skips_aggregator_for_that_variable(
        self, ecmwf_stub, monkeypatch, tmp_path
    ):
        """If a variable's retrieve fails, its aggregator call is skipped."""
        ecmwf_stub.vars = {
            "reanalysis-era5-single-levels": ["2m-temperature"],
        }

        def _boom(*_args, **_kwargs):
            raise RuntimeError("CDS 503")

        ecmwf_stub._download_dataset = MagicMock(side_effect=_boom)

        recorder: list = []

        def _record(nc_path, var_info, config):
            recorder.append((nc_path, var_info, config))
            return []

        monkeypatch.setattr(
            "earthly.ecmwf.backend.aggregate_netcdf",
            _record,
        )
        ecmwf_stub.download(
            progress_bar=False,
            aggregate=AggregationConfig(freq="1D"),
        )

        assert recorder == [], (
            f"Aggregator should not be called when retrieve fails; "
            f"got {recorder}"
        )
