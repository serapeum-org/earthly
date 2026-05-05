"""Unit tests for `earthly.aggregate` (H1 / H2 / M1 / H3 / H4 / M2 surface).

Covers `AggregationConfig` validation, `_read_time_axis` (the
candidate-loop and KeyError fallback), `_find_level_dim`, the
four-cell decision matrix in `_resolve_pressure_level`,
`_window_groups`, `_reduce` (op dispatch + skipna + min_count),
`_resolve_op` (auto-routing from `Variable.is_flux`), and the
`aggregate_netcdf` skeleton's `NotImplementedError`. The body of
`aggregate_netcdf` is wired up by H5 and is not exercised here.
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


class TestWindowGroups:
    """Tests for the private `_window_groups` helper (H3)."""

    def test_daily_grouping_six_hourly_input_yields_one_window(self):
        """Four 6-hourly slots in one day collapse to one daily window.

        Test scenario:
            CDS daily NetCDFs typically carry four sub-daily samples;
            grouping by `"1D"` must produce one window covering all
            four.
        """
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
        """Eight 6-hourly slots over two days produce two daily windows.

        Test scenario:
            Cross-day boundary handling: 8 slots / 4-per-day = 2 windows.
        """
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
        """A 32-day range across Jan/Feb yields two month-start windows.

        Test scenario:
            32 daily samples from Jan 1 land at Jan 1-31 (=> Jan
            window) plus Feb 1 (=> Feb window).
        """
        idx = pd.date_range("2022-01-01", periods=32, freq="D")
        labels = [label for label, _ in _window_groups(idx, "1MS")]
        assert labels == [pd.Timestamp("2022-01-01"), pd.Timestamp("2022-02-01")], (
            f"Expected two month-start labels, got {labels}"
        )

    def test_seasonal_grouping_qs_dec_yields_three_aligned_seasons(self):
        """`QS-DEC` aligns seasons on Dec/Mar/Jun/Sep starts.

        Test scenario:
            Nine monthly samples from Mar→Nov fall into three full
            QS-DEC seasons: MAM (Mar-May), JJA (Jun-Aug), SON
            (Sep-Nov). Excluding Dec/Jan/Feb avoids the partial DJF
            window the alias would otherwise produce.
        """
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
        """An unparseable `freq` string surfaces a pandas error.

        Test scenario:
            pandas owns the freq grammar; bad values should propagate
            its `ValueError` rather than be silently swallowed.
        """
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

        Test scenario:
            Confirms the dispatch table maps each op name to the
            matching numpy reducer.
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
        """NaN-aware mean ignores NaN samples in the window.

        Test scenario:
            `[1, 2, NaN, 3]` with `skipna=True` should average to 2.0.
        """
        arr = np.array([[[1.0]], [[2.0]], [[np.nan]], [[3.0]]])
        result = _reduce(arr, op="mean", skipna=True, min_count=None)
        assert result[0, 0] == pytest.approx(2.0), (
            f"Expected NaN-skipped mean 2.0, got {result[0, 0]}"
        )

    def test_skipna_false_propagates_nan_to_output(self):
        """Strict mean propagates any NaN to the result.

        Test scenario:
            With `skipna=False`, plain `np.mean` is used; one NaN in
            the window forces the output to NaN.
        """
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
        """Pixels with fewer non-NaN samples than `min_count` emit NaN.

        Test scenario:
            A two-sample window where one pixel has 1 valid sample
            and another has 2; with `min_count=2` only the
            fully-sampled pixel survives.
        """
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
        """`op="auto"` + `is_flux=True` resolves to `"sum"`.

        Test scenario:
            Flux variables (precipitation, evaporation, ...) are CDS
            per-timestep accumulations; aggregation must sum them
            within a window.
        """
        result = _resolve_op("auto", SimpleNamespace(is_flux=True))
        assert result == "sum", f"Expected 'sum', got {result!r}"

    def test_auto_with_state_returns_mean(self):
        """`op="auto"` + `is_flux=False` resolves to `"mean"`.

        Test scenario:
            State variables (temperature, pressure, humidity) are
            instantaneous samples; `mean` is the natural per-window
            reduction.
        """
        result = _resolve_op("auto", SimpleNamespace(is_flux=False))
        assert result == "mean", f"Expected 'mean', got {result!r}"

    @pytest.mark.parametrize("explicit_op", ["mean", "sum", "min", "max", "std"])
    def test_explicit_op_passthrough(self, explicit_op):
        """Any non-`auto` op is returned verbatim regardless of `is_flux`.

        Args:
            explicit_op: The op literal under test.

        Test scenario:
            `_resolve_op` must not override an explicit user choice.
        """
        result = _resolve_op(explicit_op, SimpleNamespace(is_flux=True))
        assert result == explicit_op, (
            f"Expected {explicit_op!r} passthrough, got {result!r}"
        )

    def test_explicit_op_does_not_consult_is_flux(self):
        """Explicit ops do not read `var_info.is_flux`.

        Test scenario:
            Use a sentinel that raises if accessed; an explicit op
            must finish without touching the attribute.
        """

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

    Round-trip behaviour against a real on-disk NetCDF lives in
    `H7` / `H8`. These checks only verify the function reaches the
    pyramids layer — i.e., the skeleton is gone.
    """

    def test_missing_file_raises_at_pyramids_layer(self, tmp_path):
        """A non-existent path surfaces an OS-level error from pyramids.

        Test scenario:
            `aggregate_netcdf` must propagate file-open failures
            unmodified. The exact exception type depends on
            pyramids/GDAL — we just assert *something* is raised
            (i.e., the skeleton is gone and the function reaches the
            real I/O layer).
        """
        missing = tmp_path / "definitely-not-here.nc"
        with pytest.raises(Exception):
            aggregate_netcdf(
                missing,
                MagicMock(),
                AggregationConfig(freq="1D"),
            )
