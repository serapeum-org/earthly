"""Unit tests for `earthly.aggregate` (H1 / H2 / M1 surface).

Covers `AggregationConfig` validation, `_read_time_axis` (the
candidate-loop and KeyError fallback), `_find_level_dim`, the
four-cell decision matrix in `_resolve_pressure_level`, and the
`aggregate_netcdf` skeleton's `NotImplementedError`. The body of
`aggregate_netcdf` is wired up by H5 and is not exercised here.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest
from pydantic import ValidationError

from earthly.aggregate import (
    _LEVEL_DIM_CANDIDATES,
    _TIME_VAR_CANDIDATES,
    AggregationConfig,
    _find_level_dim,
    _read_time_axis,
    _resolve_pressure_level,
    aggregate_netcdf,
)

pytestmark = [pytest.mark.unit]


def _make_nc(
    *,
    time_strs_by_var: dict[str, list[str] | None] | None = None,
    dimension_names: list[str] | None = None,
    sel_result: object | None = None,
) -> MagicMock:
    """Build a `NetCDF`-shaped MagicMock for the helpers under test.

    Args:
        time_strs_by_var: Map from variable name to the list returned
            by `nc.get_time_variable(var_name=name)`. Names not in
            the map return `None`.
        dimension_names: Value returned by the `dimension_names`
            property.
        sel_result: Object returned by `nc.sel(...)`. Defaults to a
            fresh `MagicMock` so tests can compare identity.
    """
    nc = MagicMock()
    table = time_strs_by_var or {}
    nc.get_time_variable = MagicMock(side_effect=lambda var_name: table.get(var_name))
    nc.dimension_names = dimension_names
    nc.sel = MagicMock(return_value=sel_result if sel_result is not None else MagicMock())
    return nc


class TestAggregationConfig:
    """Tests for :class:`AggregationConfig` (H1 surface)."""

    def test_freq_is_required(self):
        """`freq` has no default — omitting it raises ValidationError.

        Test scenario:
            `AggregationConfig()` with no arguments must fail because
            `freq` carries no default value.
        """
        with pytest.raises(ValidationError) as excinfo:
            AggregationConfig()
        assert "freq" in str(excinfo.value), (
            f"ValidationError should mention `freq`, got: {excinfo.value}"
        )

    def test_default_op_is_auto(self):
        """`op` defaults to `"auto"` so flux/state routing works without
        an explicit choice."""
        cfg = AggregationConfig(freq="1D")
        assert cfg.op == "auto", f"Expected default op 'auto', got {cfg.op!r}"

    def test_default_skipna_is_true(self):
        """`skipna` defaults to `True` (NaN-aware reductions)."""
        cfg = AggregationConfig(freq="1D")
        assert cfg.skipna is True, f"Expected default skipna True, got {cfg.skipna!r}"

    def test_default_min_count_is_none(self):
        """`min_count` defaults to `None` (no minimum)."""
        cfg = AggregationConfig(freq="1D")
        assert cfg.min_count is None, (
            f"Expected default min_count None, got {cfg.min_count!r}"
        )

    def test_default_level_is_none(self):
        """`level` defaults to `None` (3-D NetCDFs assumed)."""
        cfg = AggregationConfig(freq="1D")
        assert cfg.level is None, f"Expected default level None, got {cfg.level!r}"

    def test_default_cell_size_is_era5_native(self):
        """`cell_size` defaults to ERA5's native 0.125° grid."""
        cfg = AggregationConfig(freq="1D")
        assert cfg.cell_size == 0.125, (
            f"Expected default cell_size 0.125, got {cfg.cell_size!r}"
        )

    def test_default_out_dir_is_none(self):
        """`out_dir=None` means in-memory only — no GeoTIFF writes."""
        cfg = AggregationConfig(freq="1D")
        assert cfg.out_dir is None, f"Expected default out_dir None, got {cfg.out_dir!r}"

    def test_frozen_disallows_mutation(self):
        """Mutating an instantiated config raises (frozen)."""
        cfg = AggregationConfig(freq="1D")
        with pytest.raises(ValidationError):
            cfg.freq = "1MS"

    def test_extra_field_typo_rejected(self):
        """`freqency=` (typo) raises ValidationError, not a silent default."""
        with pytest.raises(ValidationError) as excinfo:
            AggregationConfig(freqency="1D")
        assert "freqency" in str(excinfo.value), (
            f"ValidationError should mention the offending key, got: {excinfo.value}"
        )

    def test_invalid_op_rejected(self):
        """`op` outside the `OperationLiteral` set raises."""
        with pytest.raises(ValidationError):
            AggregationConfig(freq="1D", op="median")

    @pytest.mark.parametrize(
        "op_value", ["mean", "sum", "min", "max", "std", "auto"]
    )
    def test_each_valid_op_accepted(self, op_value):
        """Every literal in `OperationLiteral` is accepted as-is.

        Args:
            op_value: One of the valid op literals.

        Test scenario:
            All six operators (`mean`, `sum`, `min`, `max`, `std`,
            `auto`) must round-trip through pydantic validation.
        """
        cfg = AggregationConfig(freq="1D", op=op_value)
        assert cfg.op == op_value, f"Expected op {op_value!r}, got {cfg.op!r}"

    def test_out_dir_path_object_accepted(self, tmp_path):
        """A `Path` instance for `out_dir` is preserved as a `Path`."""
        cfg = AggregationConfig(freq="1D", out_dir=tmp_path)
        assert isinstance(cfg.out_dir, Path), (
            f"Expected Path instance, got {type(cfg.out_dir).__name__}"
        )
        assert cfg.out_dir == tmp_path, (
            f"Expected out_dir {tmp_path}, got {cfg.out_dir}"
        )

    def test_out_dir_string_coerced_to_path(self):
        """A string `out_dir` is coerced to a `pathlib.Path` by pydantic."""
        cfg = AggregationConfig(freq="1D", out_dir="out/monthly")
        assert isinstance(cfg.out_dir, Path), (
            f"Expected Path coercion, got {type(cfg.out_dir).__name__}"
        )

    def test_min_count_int_accepted(self):
        """An integer `min_count` survives validation."""
        cfg = AggregationConfig(freq="1D", min_count=4)
        assert cfg.min_count == 4, f"Expected min_count 4, got {cfg.min_count!r}"

    def test_level_int_accepted(self):
        """A pressure level can be supplied as an integer (e.g., 1000)."""
        cfg = AggregationConfig(freq="1D", level=1000)
        assert cfg.level == 1000, f"Expected level 1000, got {cfg.level!r}"

    def test_level_float_accepted(self):
        """A pressure level can be supplied as a float (e.g., 850.5)."""
        cfg = AggregationConfig(freq="1D", level=850.5)
        assert cfg.level == 850.5, f"Expected level 850.5, got {cfg.level!r}"

    def test_skipna_false_explicit(self):
        """`skipna=False` is preserved — NaN-propagating reductions."""
        cfg = AggregationConfig(freq="1D", skipna=False)
        assert cfg.skipna is False, f"Expected skipna False, got {cfg.skipna!r}"


class TestReadTimeAxis:
    """Tests for the private `_read_time_axis` helper (H2)."""

    def test_valid_time_takes_priority(self):
        """When both `valid_time` and `time` are present, `valid_time` wins.

        Test scenario:
            CDS-Beta uses `valid_time`; the helper must prefer it
            over the legacy `time` to avoid format-string round-trip
            precision loss.
        """
        nc = _make_nc(
            time_strs_by_var={
                "valid_time": ["2022-06-15"],
                "time": ["1970-01-01"],
            }
        )
        result = _read_time_axis(nc)
        assert result[0] == pd.Timestamp("2022-06-15"), (
            f"Expected valid_time to win, got {result[0]}"
        )

    def test_falls_back_to_time_when_valid_time_absent(self):
        """`time` is used when `valid_time` returns None.

        Test scenario:
            Legacy CDS NetCDFs only carry `time`; the helper must
            fall through to the second candidate.
        """
        nc = _make_nc(
            time_strs_by_var={"valid_time": None, "time": ["2020-01-01"]}
        )
        result = _read_time_axis(nc)
        assert result[0] == pd.Timestamp("2020-01-01"), (
            f"Expected time fallback, got {result[0]}"
        )

    def test_falls_back_to_time_when_valid_time_empty_list(self):
        """An empty list for `valid_time` is treated as absence."""
        nc = _make_nc(
            time_strs_by_var={"valid_time": [], "time": ["2021-03-04"]}
        )
        result = _read_time_axis(nc)
        assert result[0] == pd.Timestamp("2021-03-04"), (
            f"Expected time fallback for empty valid_time, got {result[0]}"
        )

    def test_returns_datetimeindex(self):
        """Return type is `pandas.DatetimeIndex`."""
        nc = _make_nc(time_strs_by_var={"time": ["2022-01-01"]})
        result = _read_time_axis(nc)
        assert isinstance(result, pd.DatetimeIndex), (
            f"Expected DatetimeIndex, got {type(result).__name__}"
        )

    def test_parses_multiple_dates_in_order(self):
        """The helper preserves the order of the input strings."""
        dates = ["2022-01-01", "2022-01-02", "2022-01-03"]
        nc = _make_nc(time_strs_by_var={"time": dates})
        result = _read_time_axis(nc)
        assert list(result) == [pd.Timestamp(d) for d in dates], (
            f"Expected dates in order, got {list(result)}"
        )

    def test_keyerror_when_no_candidate_resolves(self):
        """`KeyError` when both candidates return None / empty."""
        nc = _make_nc(time_strs_by_var={"valid_time": None, "time": None})
        with pytest.raises(KeyError) as excinfo:
            _read_time_axis(nc)
        msg = str(excinfo.value)
        for name in _TIME_VAR_CANDIDATES:
            assert name in msg, (
                f"KeyError message should list candidate {name!r}, got: {msg}"
            )

    def test_get_time_variable_called_with_var_name_kwarg(self):
        """The helper passes each candidate as a keyword argument.

        Test scenario:
            pyramids' `get_time_variable(var_name=...)` is keyword-only
            in spirit; the helper must respect that.
        """
        nc = _make_nc(time_strs_by_var={"time": ["2022-01-01"]})
        _read_time_axis(nc)
        call_kwargs = [call.kwargs for call in nc.get_time_variable.call_args_list]
        assert all("var_name" in kwargs for kwargs in call_kwargs), (
            f"All calls should pass var_name as kwarg, got: {call_kwargs}"
        )


class TestFindLevelDim:
    """Tests for `_find_level_dim` (M1 detection)."""

    def test_pressure_level_returned_when_present(self):
        """`pressure_level` matches the first candidate."""
        nc = _make_nc(dimension_names=["time", "pressure_level", "lat", "lon"])
        result = _find_level_dim(nc)
        assert result == "pressure_level", (
            f"Expected 'pressure_level', got {result!r}"
        )

    def test_level_returned_when_no_pressure_level(self):
        """`level` matches the second candidate when `pressure_level` is absent."""
        nc = _make_nc(dimension_names=["time", "level", "lat", "lon"])
        result = _find_level_dim(nc)
        assert result == "level", f"Expected 'level', got {result!r}"

    def test_pressure_level_takes_priority_over_level(self):
        """When both names are present, the first candidate wins."""
        nc = _make_nc(dimension_names=["time", "pressure_level", "level", "lat", "lon"])
        result = _find_level_dim(nc)
        assert result == "pressure_level", (
            f"Expected 'pressure_level' to win over 'level', got {result!r}"
        )

    def test_returns_none_for_3d_netcdf(self):
        """No level dimension → `None`."""
        nc = _make_nc(dimension_names=["time", "lat", "lon"])
        result = _find_level_dim(nc)
        assert result is None, f"Expected None for 3-D NetCDF, got {result!r}"

    def test_returns_none_when_dimension_names_is_none(self):
        """A NetCDF with no root group reports `dimension_names=None`."""
        nc = _make_nc(dimension_names=None)
        result = _find_level_dim(nc)
        assert result is None, (
            f"Expected None when dimension_names is None, got {result!r}"
        )

    def test_candidates_constant_shape(self):
        """Documenting the candidate list as a tuple of two names."""
        assert _LEVEL_DIM_CANDIDATES == ("pressure_level", "level"), (
            f"Unexpected candidate list: {_LEVEL_DIM_CANDIDATES!r}"
        )


class TestResolvePressureLevel:
    """Tests for the four-cell decision matrix in `_resolve_pressure_level`."""

    def test_3d_no_level_returns_input_unchanged(self):
        """3-D NetCDF + no `level` → pass-through (same instance).

        Test scenario:
            The cleanest case: nothing to do, return the input
            untouched and never call `sel`.
        """
        nc = _make_nc(dimension_names=["time", "lat", "lon"])
        result = _resolve_pressure_level(nc, level=None)
        assert result is nc, "Expected input nc returned unchanged"
        nc.sel.assert_not_called()

    def test_3d_with_level_raises_value_error(self):
        """3-D NetCDF + `level` set → ValueError ('no pressure-level dim')."""
        nc = _make_nc(dimension_names=["time", "lat", "lon"])
        with pytest.raises(ValueError) as excinfo:
            _resolve_pressure_level(nc, level=1000)
        msg = str(excinfo.value)
        assert "no" in msg.lower() and "pressure-level dimension" in msg, (
            f"Error should explain the missing dimension, got: {msg}"
        )

    def test_3d_with_level_error_mentions_passed_value(self):
        """The error names the offending `level` so users can find it."""
        nc = _make_nc(dimension_names=["time", "lat", "lon"])
        with pytest.raises(ValueError, match=r"850"):
            _resolve_pressure_level(nc, level=850)

    def test_4d_without_level_raises_value_error(self):
        """4-D NetCDF + no `level` → ValueError ('pass level=...')."""
        nc = _make_nc(dimension_names=["time", "pressure_level", "lat", "lon"])
        with pytest.raises(ValueError) as excinfo:
            _resolve_pressure_level(nc, level=None)
        msg = str(excinfo.value)
        assert "level=" in msg.lower() or "level=" in msg, (
            f"Error should hint at `level=` parameter, got: {msg}"
        )

    def test_4d_without_level_error_mentions_dim_name(self):
        """The error names the actual dimension found."""
        nc = _make_nc(dimension_names=["time", "pressure_level", "lat", "lon"])
        with pytest.raises(ValueError, match=r"pressure_level"):
            _resolve_pressure_level(nc, level=None)

    def test_4d_with_level_calls_sel_with_pressure_level_kwarg(self):
        """4-D `pressure_level` + level → `nc.sel(pressure_level=level)`."""
        nc = _make_nc(dimension_names=["time", "pressure_level", "lat", "lon"])
        _resolve_pressure_level(nc, level=1000)
        nc.sel.assert_called_once_with(pressure_level=1000)

    def test_4d_with_level_calls_sel_with_level_kwarg(self):
        """4-D `level` + level → `nc.sel(level=level)` (alt dim name)."""
        nc = _make_nc(dimension_names=["time", "level", "lat", "lon"])
        _resolve_pressure_level(nc, level=850)
        nc.sel.assert_called_once_with(level=850)

    def test_4d_with_level_returns_sel_result(self):
        """The returned NetCDF is the result of `sel(...)`, not the input."""
        sel_output = MagicMock(name="pinned_nc")
        nc = _make_nc(
            dimension_names=["time", "pressure_level", "lat", "lon"],
            sel_result=sel_output,
        )
        result = _resolve_pressure_level(nc, level=1000)
        assert result is sel_output, (
            f"Expected sel result, got {result!r} (input was {nc!r})"
        )

    def test_4d_with_float_level(self):
        """A float `level` (e.g., 850.5) is forwarded to `sel` verbatim."""
        nc = _make_nc(dimension_names=["time", "pressure_level", "lat", "lon"])
        _resolve_pressure_level(nc, level=850.5)
        nc.sel.assert_called_once_with(pressure_level=850.5)


class TestAggregateNetcdf:
    """Tests for the H1-skeleton entry point."""

    def test_raises_not_implemented_error(self):
        """The H1 skeleton's body raises `NotImplementedError`.

        Test scenario:
            `aggregate_netcdf(...)` must fail loud until H5 wires the
            real body. A silent return would hide an unfinished
            integration.
        """
        with pytest.raises(NotImplementedError):
            aggregate_netcdf(
                Path("/nonexistent.nc"),
                MagicMock(),
                AggregationConfig(freq="1D"),
            )

    def test_error_message_points_at_h5(self):
        """The `NotImplementedError` message names task H5 so users know
        when to expect the real body."""
        with pytest.raises(NotImplementedError, match=r"H5"):
            aggregate_netcdf(
                Path("/nonexistent.nc"),
                MagicMock(),
                AggregationConfig(freq="1D"),
            )
