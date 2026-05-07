"""Unit tests for `earthly.aggregate`.

Covers `AggregationConfig` validation, `_read_time_axis` (the
candidate-loop and KeyError fallback), `_find_level_dim`, the
four-cell decision matrix in `_resolve_pressure_level`,
`_window_groups`, `_reduce` (op dispatch + skipna + min_count),
`_resolve_op` (auto-routing from `Variable.is_flux`), and round-trip
runs of `aggregate_netcdf` against synthetic NetCDFs (H7).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from earthly.aggregate import (
    _LEVEL_DIM_CANDIDATES,
    _REDUCERS_SKIPNA,
    _REDUCERS_STRICT,
    _TIME_VAR_CANDIDATES,
    AggregationConfig,
    _find_level_dim,
    _read_time_axis,
    _reduce,
    _resolve_op,
    _resolve_pressure_level,
    _window_groups,
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
        """`freq` has no default — omitting it raises ValidationError."""
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
        """When both `valid_time` and `time` are present, `valid_time` wins."""
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
        """`time` is used when `valid_time` returns None."""
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
        """The helper passes each candidate as a keyword argument."""
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
        """3-D NetCDF + no `level` → pass-through (same instance)."""
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


class TestWindowGroups:
    """Tests for the private `_window_groups` helper (H3)."""

    def test_daily_grouping_six_hourly_input_yields_one_window(self):
        """Four 6-hourly slots in one day collapse to one daily window."""
        idx = pd.date_range("2022-01-01", periods=4, freq="6h")
        windows = list(_window_groups(idx, "1D"))
        assert len(windows) == 1, f"Expected 1 daily window, got {len(windows)}"
        label, mask = windows[0]
        assert label == pd.Timestamp("2022-01-01"), (
            f"Expected window label 2022-01-01, got {label}"
        )
        assert mask.tolist() == [True, True, True, True], (
            f"Expected all four samples in window, got {mask.tolist()}"
        )

    def test_daily_grouping_two_days_yields_two_windows(self):
        """Eight 6-hourly slots over two days produce two daily windows."""
        idx = pd.date_range("2022-01-01", periods=8, freq="6h")
        labels = [label for label, _ in _window_groups(idx, "1D")]
        assert labels == [pd.Timestamp("2022-01-01"), pd.Timestamp("2022-01-02")], (
            f"Expected two consecutive day labels, got {labels}"
        )

    def test_weekly_grouping_collapses_seven_days(self):
        """Daily samples over a week reduce to one `"7D"` window."""
        idx = pd.date_range("2022-01-01", periods=7, freq="D")
        windows = list(_window_groups(idx, "7D"))
        assert len(windows) == 1, (
            f"Expected 1 weekly window, got {len(windows)}"
        )
        _, mask = windows[0]
        assert mask.sum() == 7, f"Expected 7 samples in window, got {mask.sum()}"

    def test_monthly_ms_grouping_collapses_january(self):
        """31 daily samples in January produce one `"1MS"` window."""
        idx = pd.date_range("2022-01-01", periods=31, freq="D")
        windows = list(_window_groups(idx, "1MS"))
        assert len(windows) == 1, f"Expected 1 monthly window, got {len(windows)}"
        label, mask = windows[0]
        assert label == pd.Timestamp("2022-01-01"), (
            f"Expected month-start label, got {label}"
        )
        assert mask.sum() == 31, f"Expected 31 samples, got {mask.sum()}"

    def test_monthly_ms_two_months_yields_two_windows(self):
        """A 32-day range across Jan/Feb yields two month-start windows."""
        idx = pd.date_range("2022-01-01", periods=32, freq="D")
        labels = [label for label, _ in _window_groups(idx, "1MS")]
        assert labels == [pd.Timestamp("2022-01-01"), pd.Timestamp("2022-02-01")], (
            f"Expected two month-start labels, got {labels}"
        )

    def test_seasonal_grouping_qs_dec_yields_three_aligned_seasons(self):
        """`QS-DEC` aligns seasons on Dec/Mar/Jun/Sep starts."""
        idx = pd.date_range("2022-03-01", periods=9, freq="MS")
        labels = [label for label, _ in _window_groups(idx, "QS-DEC")]
        assert labels == [
            pd.Timestamp("2022-03-01"),
            pd.Timestamp("2022-06-01"),
            pd.Timestamp("2022-09-01"),
        ], f"Expected three quarter starts, got {labels}"

    def test_window_label_is_left_edge(self):
        """Group keys returned are the windows' left-edge timestamps."""
        idx = pd.date_range("2022-06-15", periods=4, freq="6h")
        label, _ = next(iter(_window_groups(idx, "1D")))
        assert label == pd.Timestamp("2022-06-15"), (
            f"Expected left-edge 2022-06-15, got {label}"
        )

    def test_mask_length_matches_input(self):
        """Each emitted mask has length equal to the time axis."""
        idx = pd.date_range("2022-01-01", periods=12, freq="6h")
        for _, mask in _window_groups(idx, "1D"):
            assert len(mask) == 12, (
                f"Mask length {len(mask)} should match time-axis length 12"
            )

    def test_mask_dtype_is_bool(self):
        """Mask is a numpy bool array — drop-in for ndarray indexing."""
        idx = pd.date_range("2022-01-01", periods=4, freq="6h")
        _, mask = next(iter(_window_groups(idx, "1D")))
        assert isinstance(mask, np.ndarray), (
            f"Expected numpy ndarray, got {type(mask).__name__}"
        )
        assert mask.dtype == np.bool_, f"Expected bool dtype, got {mask.dtype}"

    def test_masks_isolate_correct_indices(self):
        """Each mask selects exactly the samples in its window."""
        idx = pd.date_range("2022-01-01", periods=8, freq="6h")
        windows = list(_window_groups(idx, "1D"))
        first_mask = windows[0][1]
        second_mask = windows[1][1]
        assert first_mask.tolist() == [True] * 4 + [False] * 4, (
            f"First daily mask wrong: {first_mask.tolist()}"
        )
        assert second_mask.tolist() == [False] * 4 + [True] * 4, (
            f"Second daily mask wrong: {second_mask.tolist()}"
        )
        assert (first_mask & second_mask).sum() == 0, (
            "Daily masks must be disjoint"
        )

    def test_empty_time_axis_yields_nothing(self):
        """An empty index produces no windows."""
        idx = pd.DatetimeIndex([])
        windows = list(_window_groups(idx, "1D"))
        assert windows == [], f"Expected no windows, got {windows}"

    def test_single_sample_yields_single_window(self):
        """One timestamp → one window with one true bit."""
        idx = pd.DatetimeIndex(["2022-06-15"])
        windows = list(_window_groups(idx, "1D"))
        assert len(windows) == 1, f"Expected 1 window, got {len(windows)}"
        _, mask = windows[0]
        assert mask.tolist() == [True], f"Expected [True], got {mask.tolist()}"

    def test_invalid_freq_raises(self):
        """An unparseable `freq` string surfaces a pandas error."""
        idx = pd.date_range("2022-01-01", periods=4, freq="6h")
        with pytest.raises(ValueError):
            list(_window_groups(idx, "not-a-real-freq"))


class TestReduce:
    """Tests for the private `_reduce` helper (H4)."""

    @pytest.fixture(scope="class")
    def cube(self) -> np.ndarray:
        """A 3-D `(time=4, lat=2, lon=2)` array with known values per pixel.

        Pixel `(0, 0)` = [1, 2, 3, 4]; `(0, 1)` = [10, 20, 30, 40];
        `(1, 0)` = [-1, -2, -3, -4]; `(1, 1)` = [0.1, 0.2, 0.3, 0.4].
        """
        return np.array(
            [
                [[1.0, 10.0], [-1.0, 0.1]],
                [[2.0, 20.0], [-2.0, 0.2]],
                [[3.0, 30.0], [-3.0, 0.3]],
                [[4.0, 40.0], [-4.0, 0.4]],
            ]
        )

    @pytest.mark.parametrize(
        "op, expected_pixel_00",
        [
            ("mean", 2.5),
            ("sum", 10.0),
            ("min", 1.0),
            ("max", 4.0),
        ],
    )
    def test_each_op_dispatches_to_correct_reducer(self, cube, op, expected_pixel_00):
        """Each named op produces the expected reduction at one known pixel.

        Args:
            cube: Class fixture providing a `(4, 2, 2)` test array.
            op: Reduction operator under test.
            expected_pixel_00: Known result at pixel `(0, 0)`.
        """
        result = _reduce(cube, op=op, skipna=True, min_count=None)
        assert result.shape == (2, 2), f"Expected (2, 2), got {result.shape}"
        assert result[0, 0] == pytest.approx(expected_pixel_00), (
            f"Op {op!r} at (0, 0): expected {expected_pixel_00}, got {result[0, 0]}"
        )

    def test_std_op_returns_nonzero(self, cube):
        """`std` over a non-constant series produces a positive value."""
        result = _reduce(cube, op="std", skipna=True, min_count=None)
        assert result[0, 0] > 0, (
            f"std should be positive for non-constant series, got {result[0, 0]}"
        )

    def test_skipna_true_excludes_nan_from_mean(self):
        """NaN-aware mean ignores NaN samples in the window."""
        arr = np.array([[[1.0]], [[2.0]], [[np.nan]], [[3.0]]])
        result = _reduce(arr, op="mean", skipna=True, min_count=None)
        assert result[0, 0] == pytest.approx(2.0), (
            f"Expected NaN-skipped mean 2.0, got {result[0, 0]}"
        )

    def test_skipna_false_propagates_nan_to_output(self):
        """Strict mean propagates any NaN to the result."""
        arr = np.array([[[1.0, 2.0]], [[np.nan, 3.0]]])
        result = _reduce(arr, op="mean", skipna=False, min_count=None)
        assert np.isnan(result[0, 0]), (
            f"Pixel (0, 0) should be NaN under strict mode, got {result[0, 0]}"
        )
        assert result[0, 1] == pytest.approx(2.5), (
            f"Pixel (0, 1) had no NaN; expected 2.5, got {result[0, 1]}"
        )

    @pytest.mark.parametrize("op", ["mean", "sum", "min", "max", "std"])
    def test_skipna_true_uses_nan_aware_table(self, op):
        """Every op routes through the NaN-aware table when `skipna=True`."""
        assert op in _REDUCERS_SKIPNA, (
            f"_REDUCERS_SKIPNA missing op {op!r}: {sorted(_REDUCERS_SKIPNA)}"
        )

    @pytest.mark.parametrize("op", ["mean", "sum", "min", "max", "std"])
    def test_skipna_false_uses_strict_table(self, op):
        """Every op routes through the strict table when `skipna=False`."""
        assert op in _REDUCERS_STRICT, (
            f"_REDUCERS_STRICT missing op {op!r}: {sorted(_REDUCERS_STRICT)}"
        )

    def test_min_count_masks_under_sampled_pixel(self):
        """Pixels with fewer non-NaN samples than `min_count` emit NaN."""
        arr = np.array([[[1.0, 2.0]], [[np.nan, 3.0]]])
        result = _reduce(arr, op="mean", skipna=True, min_count=2)
        assert np.isnan(result[0, 0]), (
            f"Under-sampled pixel should be NaN, got {result[0, 0]}"
        )
        assert result[0, 1] == pytest.approx(2.5), (
            f"Fully-sampled pixel should survive: expected 2.5, got {result[0, 1]}"
        )

    def test_min_count_none_disables_floor(self):
        """`min_count=None` lets every reduction reach the output as-is."""
        arr = np.array([[[1.0]], [[np.nan]], [[3.0]]])
        result = _reduce(arr, op="mean", skipna=True, min_count=None)
        assert result[0, 0] == pytest.approx(2.0), (
            f"Expected 2.0 with min_count=None, got {result[0, 0]}"
        )

    def test_keyerror_on_auto(self):
        """`op="auto"` is rejected — caller must resolve it first."""
        arr = np.zeros((2, 2, 2))
        with pytest.raises(KeyError, match="auto"):
            _reduce(arr, op="auto", skipna=True, min_count=None)

    def test_keyerror_on_unknown_op(self):
        """An unknown op raises `KeyError` listing the valid choices."""
        arr = np.zeros((2, 2, 2))
        with pytest.raises(KeyError) as excinfo:
            _reduce(arr, op="median", skipna=True, min_count=None)
        msg = str(excinfo.value)
        for valid in ("mean", "sum", "min", "max", "std"):
            assert valid in msg, (
                f"Error message should list valid op {valid!r}, got: {msg}"
            )

    def test_collapses_axis_zero_only(self):
        """Reduction collapses axis 0; the remaining shape passes through."""
        arr = np.zeros((4, 3, 5))
        result = _reduce(arr, op="mean", skipna=True, min_count=None)
        assert result.shape == (3, 5), (
            f"Expected (3, 5), got {result.shape}"
        )


class TestResolveOp:
    """Tests for `_resolve_op` (M2 — `op="auto"` routing)."""

    def test_auto_with_flux_returns_sum(self):
        """`op="auto"` + `is_flux=True` resolves to `"sum"`."""
        result = _resolve_op("auto", SimpleNamespace(is_flux=True))
        assert result == "sum", f"Expected 'sum', got {result!r}"

    def test_auto_with_state_returns_mean(self):
        """`op="auto"` + `is_flux=False` resolves to `"mean"`."""
        result = _resolve_op("auto", SimpleNamespace(is_flux=False))
        assert result == "mean", f"Expected 'mean', got {result!r}"

    @pytest.mark.parametrize("explicit_op", ["mean", "sum", "min", "max", "std"])
    def test_explicit_op_passthrough(self, explicit_op):
        """Any non-`auto` op is returned verbatim regardless of `is_flux`.

        Args:
            explicit_op: The op literal under test.
        """
        result = _resolve_op(explicit_op, SimpleNamespace(is_flux=True))
        assert result == explicit_op, (
            f"Expected {explicit_op!r} passthrough, got {result!r}"
        )

    def test_explicit_op_does_not_consult_is_flux(self):
        """Explicit ops do not read `var_info.is_flux`."""

        class TrackedVar:
            """Var stub that records access to `is_flux`."""

            def __init__(self):
                self.accessed = False

            @property
            def is_flux(self) -> bool:
                """Track property access then return False."""
                self.accessed = True
                return False

        var = TrackedVar()
        _resolve_op("max", var)
        assert var.accessed is False, (
            "Explicit op should not consult var_info.is_flux"
        )


class TestAggregateNetcdf:
    """Smoke tests for the public entry point.

    Heavy round-trip behaviour against a synthetic NetCDF lives in
    :class:`TestAggregateNetcdfRoundTrip` (H7). These checks only
    verify the function reaches the pyramids layer.
    """

    def test_missing_file_raises_at_pyramids_layer(self, tmp_path):
        """A non-existent path surfaces an OS-level error from pyramids."""
        missing = tmp_path / "definitely-not-here.nc"
        with pytest.raises(Exception):
            aggregate_netcdf(
                missing,
                MagicMock(),
                AggregationConfig(freq="1D"),
            )


class _FakeNetCDF:
    """Minimal `pyramids.netcdf.NetCDF` stand-in for round-trip tests.

    Implements the four surfaces `aggregate_netcdf` consumes —
    `read_array`, `get_time_variable`, `dimension_names`,
    `geotransform`, and (optionally) `sel`. Lets tests exercise the
    body of `aggregate_netcdf` without writing a real on-disk
    NetCDF (the test environment has no NetCDF writer).
    """

    def __init__(
        self,
        *,
        array: np.ndarray,
        time_strs_by_var: dict[str, list[str] | None],
        dimension_names: list[str] | None = None,
        geotransform: tuple = (0.0, 1.0, 0.0, 1.0, 0.0, -1.0),
        on_sel: object | None = None,
    ):
        self._array = array
        self._times = time_strs_by_var
        self.dimension_names = dimension_names
        self.geotransform = geotransform
        self._on_sel = on_sel

    def read_array(self, variable: str) -> np.ndarray:
        """Return the stored array regardless of variable name (test stub)."""
        return self._array

    def get_time_variable(self, var_name: str) -> list[str] | None:
        """Look up the time strings registered for `var_name`."""
        return self._times.get(var_name)

    def sel(self, **kwargs):
        """Return the configured `_on_sel` instance to simulate level pinning."""
        return self._on_sel


class _RealVariable(SimpleNamespace):
    """Lightweight stand-in for `earthly.ecmwf.Variable` in tests.

    Exposes only the four attributes `aggregate_netcdf` reads
    (`is_flux`, `cds_variable`, `nc_variable`, `units`) so the
    round-trip tests don't have to construct a full pydantic model.
    """


def _patch_netcdf_read(monkeypatch, fake_nc):
    """Patch `pyramids.netcdf.NetCDF.read_file` to return `fake_nc`."""
    from pyramids.netcdf import NetCDF as RealNetCDF

    monkeypatch.setattr(RealNetCDF, "read_file", staticmethod(lambda *_a, **_kw: fake_nc))


def _patch_geotiff_write(monkeypatch):
    """Patch `pyramids.dataset.Dataset.create_from_array(...).to_file(...)` to a no-op recorder.

    Returns the list of `(arr_shape, geo, epsg, target)` tuples that
    were "written" so tests can inspect call sites without hitting
    GDAL / disk.
    """
    from pyramids.dataset import Dataset as RealDataset

    writes: list[tuple] = []

    class _StubGeoTiff:
        def __init__(self, arr, geo, epsg):
            self.arr = arr
            self.geo = geo
            self.epsg = epsg

        def to_file(self, path):
            writes.append((self.arr.shape, self.geo, self.epsg, path))

    monkeypatch.setattr(
        RealDataset,
        "create_from_array",
        staticmethod(lambda arr, geo, epsg: _StubGeoTiff(arr, geo, epsg)),
    )
    return writes


class TestAggregateNetcdfRoundTrip:
    """End-to-end body runs against a synthetic in-memory NetCDF (H7)."""

    @pytest.fixture
    def state_var(self):
        """A state-flagged variable (`is_flux=False`).

        Returns:
            _RealVariable: stand-in carrying the four attributes
            `aggregate_netcdf` consumes.
        """
        return _RealVariable(
            is_flux=False,
            cds_variable="2m_temperature",
            nc_variable="t2m",
            units="K",
        )

    @pytest.fixture
    def flux_var(self):
        """A flux-flagged variable (`is_flux=True`).

        Returns:
            _RealVariable: stand-in for `total_precipitation`.
        """
        return _RealVariable(
            is_flux=True,
            cds_variable="total_precipitation",
            nc_variable="tp",
            units="m",
        )

    def _daily_six_hourly_array(self, n_days: int = 2) -> np.ndarray:
        """Build `(n_days * 4, 2, 2)` increasing values, four slots per day."""
        n_slots = n_days * 4
        cube = np.zeros((n_slots, 2, 2), dtype=float)
        for i in range(n_slots):
            cube[i, :, :] = float(i + 1)
        return cube

    def _date_strings_six_hourly(self, n_days: int = 2) -> list[str]:
        """Build `n_days * 4` six-hourly date strings starting Jan 1."""
        idx = pd.date_range("2022-01-01", periods=n_days * 4, freq="6h")
        return [t.strftime("%Y-%m-%d %H:%M:%S") for t in idx]

    def test_daily_mean_collapses_to_one_slice_per_day(
        self, monkeypatch, tmp_path, state_var
    ):
        """Eight 6-hourly slots → 2 daily slices, each = mean of 4."""
        cube = self._daily_six_hourly_array(n_days=2)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(2)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        writes = _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=tmp_path),
        )

        assert len(results) == 2, f"Expected 2 daily windows, got {len(results)}"
        first_label, first_arr, _ = results[0]
        assert first_label == pd.Timestamp("2022-01-01"), (
            f"First label should be 2022-01-01, got {first_label}"
        )
        assert first_arr[0, 0] == pytest.approx(2.5), (
            f"Day 1 mean should be (1+2+3+4)/4 = 2.5, got {first_arr[0, 0]}"
        )
        second_arr = results[1][1]
        assert second_arr[0, 0] == pytest.approx(6.5), (
            f"Day 2 mean should be (5+6+7+8)/4 = 6.5, got {second_arr[0, 0]}"
        )
        assert len(writes) == 2, (
            f"Expected 2 GeoTIFFs to be written, got {len(writes)}"
        )

    def test_op_auto_routes_state_to_mean(self, monkeypatch, tmp_path, state_var):
        """`op="auto"` + `is_flux=False` → mean over the window."""
        cube = self._daily_six_hourly_array(n_days=1)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="auto", out_dir=None),
        )
        _, arr, _ = results[0]
        assert arr[0, 0] == pytest.approx(2.5), (
            f"Auto on state var should mean to 2.5, got {arr[0, 0]}"
        )

    def test_op_auto_routes_flux_to_sum(self, monkeypatch, tmp_path, flux_var):
        """`op="auto"` + `is_flux=True` → sum over the window."""
        cube = self._daily_six_hourly_array(n_days=1)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            flux_var,
            AggregationConfig(freq="1D", op="auto", out_dir=None),
        )
        _, arr, _ = results[0]
        assert arr[0, 0] == pytest.approx(10.0), (
            f"Auto on flux var should sum to 1+2+3+4=10.0, got {arr[0, 0]}"
        )

    def test_min_count_emits_nan_for_partial_windows(
        self, monkeypatch, tmp_path, state_var
    ):
        """A window with fewer non-NaN samples than `min_count` emits NaN."""
        import warnings

        cube = np.full((4, 2, 2), np.nan)
        cube[0, 0, 0] = 1.0
        cube[1, 0, 0] = 2.0
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        # numpy emits "Mean of empty slice" for the three pixels that are
        # all-NaN before `min_count` masks them. Behaviour is correct;
        # silence the incidental warning so test output stays clean.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            results = aggregate_netcdf(
                tmp_path / "fake.nc",
                state_var,
                AggregationConfig(
                    freq="1D", op="mean", out_dir=None, min_count=4,
                ),
            )
        _, arr, _ = results[0]
        assert np.isnan(arr[0, 0]), (
            f"Pixel with only 2 non-NaN samples and min_count=4 should be NaN, "
            f"got {arr[0, 0]}"
        )

    def test_pressure_level_without_level_raises(
        self, monkeypatch, tmp_path, state_var
    ):
        """A 4-D NetCDF with no `level` set raises ValueError."""
        cube = np.zeros((4, 1, 2, 2))
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "pressure_level", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        with pytest.raises(ValueError, match="pressure_level"):
            aggregate_netcdf(
                tmp_path / "fake.nc",
                state_var,
                AggregationConfig(freq="1D", op="mean", out_dir=None),
            )

    def test_pressure_level_with_level_pins_via_sel(
        self, monkeypatch, tmp_path, state_var
    ):
        """`level=1000` calls `nc.sel(pressure_level=1000)` and aggregates the result."""
        cube_3d = self._daily_six_hourly_array(n_days=1)
        pinned = _FakeNetCDF(
            array=cube_3d,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
        )
        outer = _FakeNetCDF(
            array=np.zeros((4, 1, 2, 2)),
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "pressure_level", "lat", "lon"],
            on_sel=pinned,
        )
        _patch_netcdf_read(monkeypatch, outer)
        _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=None, level=1000),
        )
        _, arr, _ = results[0]
        assert arr[0, 0] == pytest.approx(2.5), (
            f"After level pin, daily mean should be 2.5, got {arr[0, 0]}"
        )

    def test_out_dir_none_skips_writes(self, monkeypatch, tmp_path, state_var):
        """`out_dir=None` returns arrays in memory and writes no files."""
        cube = self._daily_six_hourly_array(n_days=1)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        writes = _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=None),
        )
        assert results[0][2] is None, (
            f"Third tuple element should be None, got {results[0][2]!r}"
        )
        assert writes == [], (
            f"No GeoTIFF writes should occur when out_dir=None; got {writes!r}"
        )

    def test_geotiff_filename_carries_variable_freq_and_window(
        self, monkeypatch, tmp_path, state_var
    ):
        """Output GeoTIFF filename matches `<cds_variable>_<freq>_<YYYYMMDD>.tif`."""
        cube = self._daily_six_hourly_array(n_days=1)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        writes = _patch_geotiff_write(monkeypatch)

        out_dir = tmp_path / "agg"
        aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=out_dir),
        )
        target_path = writes[0][3]
        assert target_path.endswith("2m_temperature_1D_20220101.tif"), (
            f"Filename should match `<var>_<freq>_<window>.tif` shape, "
            f"got {target_path!r}"
        )

    def test_valid_time_variable_is_picked_over_time(
        self, monkeypatch, tmp_path, state_var
    ):
        """A NetCDF carrying both `valid_time` and `time` uses `valid_time`."""
        cube = self._daily_six_hourly_array(n_days=1)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={
                "valid_time": self._date_strings_six_hourly(1),
                "time": ["1900-01-01"] * 4,
            },
            dimension_names=["valid_time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=None),
        )
        label, _, _ = results[0]
        assert label == pd.Timestamp("2022-01-01"), (
            f"`valid_time` should drive the time axis (2022-01-01); "
            f"got {label}"
        )

    def test_out_dir_created_if_missing(self, monkeypatch, tmp_path, state_var):
        """A non-existent `out_dir` is created with parents."""
        cube = self._daily_six_hourly_array(n_days=1)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        out_dir = tmp_path / "deeply" / "nested" / "out"
        assert not out_dir.exists()
        aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=out_dir),
        )
        assert out_dir.exists(), (
            f"`out_dir` should be created with parents; missing at {out_dir}"
        )

    def test_skipna_false_propagates_nan_through_body(
        self, monkeypatch, tmp_path, state_var
    ):
        """`skipna=False` must propagate end-to-end through `aggregate_netcdf`."""
        cube = self._daily_six_hourly_array(n_days=2)
        cube[1, 0, 0] = np.nan
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(2)},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=None, skipna=False),
        )
        day1 = results[0][1]
        day2 = results[1][1]
        assert np.isnan(day1[0, 0]), (
            f"Day 1 pixel (0, 0) was NaN-tainted; with skipna=False it "
            f"should propagate NaN, got {day1[0, 0]}"
        )
        assert day2[0, 0] == pytest.approx(6.5), (
            f"Day 2 was clean; mean should be 6.5, got {day2[0, 0]}"
        )

    def test_monthly_grouping_runs_end_to_end(
        self, monkeypatch, tmp_path, state_var
    ):
        """A 32-day cube + `freq="1MS"` produces 2 monthly windows."""
        n_days = 32
        cube = np.zeros((n_days, 2, 2), dtype=float)
        cube[:31, :, :] = 1.0  # January: 31 days of 1.0
        cube[31, :, :] = 2.0  # February 1
        idx = pd.date_range("2022-01-01", periods=n_days, freq="D")
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": [t.strftime("%Y-%m-%d") for t in idx]},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        _patch_geotiff_write(monkeypatch)

        results = aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1MS", op="mean", out_dir=None),
        )
        labels = [label for label, _, _ in results]
        assert labels == [pd.Timestamp("2022-01-01"), pd.Timestamp("2022-02-01")], (
            f"Expected monthly labels [Jan-1, Feb-1], got {labels}"
        )
        assert results[0][1][0, 0] == pytest.approx(1.0), (
            f"January mean should be 1.0, got {results[0][1][0, 0]}"
        )
        assert results[1][1][0, 0] == pytest.approx(2.0), (
            f"February mean (1 sample) should be 2.0, got {results[1][1][0, 0]}"
        )

    def test_empty_time_axis_returns_empty_results(
        self, monkeypatch, tmp_path, state_var
    ):
        """A NetCDF with zero time samples returns an empty result list, not an error."""
        nc = _FakeNetCDF(
            array=np.zeros((0, 2, 2)),
            time_strs_by_var={"time": []},
            dimension_names=["time", "lat", "lon"],
        )
        _patch_netcdf_read(monkeypatch, nc)
        writes = _patch_geotiff_write(monkeypatch)

        with pytest.raises(KeyError):
            aggregate_netcdf(
                tmp_path / "fake.nc",
                state_var,
                AggregationConfig(freq="1D", op="mean", out_dir=tmp_path),
            )
        assert writes == [], (
            f"No writes should occur on empty time axis, got {writes!r}"
        )

    def test_cell_size_does_not_affect_geotransform(
        self, monkeypatch, tmp_path, state_var
    ):
        """`cell_size` is informational; the GeoTIFF geotransform comes from `nc.geotransform`."""
        cube = self._daily_six_hourly_array(n_days=1)
        source_geo = (-75.0, 0.5, 0.0, 5.0, 0.0, -0.5)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
            geotransform=source_geo,
        )
        _patch_netcdf_read(monkeypatch, nc)
        writes = _patch_geotiff_write(monkeypatch)

        aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=tmp_path, cell_size=0.25),
        )
        _, geo, _, _ = writes[0]
        assert geo == source_geo, (
            f"Output geotransform should equal nc.geotransform regardless "
            f"of config.cell_size; got {geo}"
        )

    def test_geotransform_forwarded_to_geotiff_writer(
        self, monkeypatch, tmp_path, state_var
    ):
        """`nc.geotransform` reaches `Dataset.create_from_array(geo=...)` verbatim."""
        cube = self._daily_six_hourly_array(n_days=1)
        source_geo = (-75.0, 0.125, 0.0, 5.0, 0.0, -0.125)
        nc = _FakeNetCDF(
            array=cube,
            time_strs_by_var={"time": self._date_strings_six_hourly(1)},
            dimension_names=["time", "lat", "lon"],
            geotransform=source_geo,
        )
        _patch_netcdf_read(monkeypatch, nc)
        writes = _patch_geotiff_write(monkeypatch)

        aggregate_netcdf(
            tmp_path / "fake.nc",
            state_var,
            AggregationConfig(freq="1D", op="mean", out_dir=tmp_path),
        )
        assert len(writes) == 1, f"Expected 1 write, got {len(writes)}"
        _shape, geo, epsg, _path = writes[0]
        assert geo == source_geo, (
            f"Geotransform should be forwarded verbatim; "
            f"expected {source_geo}, got {geo}"
        )
        assert epsg == 4326, f"EPSG should be 4326 (WGS84); got {epsg}"
