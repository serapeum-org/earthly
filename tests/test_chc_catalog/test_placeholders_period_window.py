"""Lock-in for M5: `_placeholders` resolves `{start_yyyymmdd}` / `{end_yyyymmdd}` from pandas_freq."""

from __future__ import annotations

import pandas as pd
import pytest

from earthlens.chc import Catalog
from earthlens.chc.backend import CHIRPS

pytestmark = [pytest.mark.chc]


class TestPlaceholdersWithoutFreq:
    """Calling `_placeholders(date)` without `pandas_freq` omits the M5 keys."""

    def test_no_freq_omits_start_and_end(self):
        """The M5 keys are absent so a pattern without them is unaffected."""
        ph = CHIRPS._placeholders(pd.Timestamp("2020-01-15"))
        assert "start_yyyymmdd" not in ph
        assert "end_yyyymmdd" not in ph

    def test_no_freq_keeps_the_original_seven_keys(self):
        """The pre-M5 placeholder set is unchanged when `pandas_freq` is None."""
        ph = CHIRPS._placeholders(pd.Timestamp("2020-03-15"))
        assert set(ph) == {"year", "month", "day", "dekad", "pentad", "hour", "doy"}


class TestPlaceholdersMonthly:
    """`pandas_freq='MS'` resolves the period to `[YYYYMM01, YYYYMM<last>]`."""

    def test_january_2020(self):
        """January 2020 starts 20200101 and ends 20200131."""
        ph = CHIRPS._placeholders(pd.Timestamp("2020-01-01"), pandas_freq="MS")
        assert ph["start_yyyymmdd"] == "20200101"
        assert ph["end_yyyymmdd"] == "20200131"

    def test_february_2020_leap_year(self):
        """February 2020 ends 20200229 (leap year, 29 days)."""
        ph = CHIRPS._placeholders(pd.Timestamp("2020-02-01"), pandas_freq="MS")
        assert ph["end_yyyymmdd"] == "20200229"

    def test_february_2021_non_leap(self):
        """February 2021 ends 20210228."""
        ph = CHIRPS._placeholders(pd.Timestamp("2021-02-01"), pandas_freq="MS")
        assert ph["end_yyyymmdd"] == "20210228"

    def test_december_end_of_year(self):
        """December rolls cleanly to 31."""
        ph = CHIRPS._placeholders(pd.Timestamp("2020-12-01"), pandas_freq="MS")
        assert ph["start_yyyymmdd"] == "20201201"
        assert ph["end_yyyymmdd"] == "20201231"


class TestPlaceholdersDekadal:
    """`pandas_freq='10D'` resolves the period to a 10-day inclusive window."""

    def test_first_dekad(self):
        """The first dekad of January 2020 is 20200101 - 20200110."""
        ph = CHIRPS._placeholders(pd.Timestamp("2020-01-01"), pandas_freq="10D")
        assert ph["start_yyyymmdd"] == "20200101"
        assert ph["end_yyyymmdd"] == "20200110"

    def test_second_dekad(self):
        """The second dekad of January 2020 is 20200111 - 20200120."""
        ph = CHIRPS._placeholders(pd.Timestamp("2020-01-11"), pandas_freq="10D")
        assert ph["start_yyyymmdd"] == "20200111"
        assert ph["end_yyyymmdd"] == "20200120"


class TestPatternExpansionForWbgt:
    """End-to-end: the WBGT pattern format-string round-trips with the M5 placeholders."""

    def test_wbgt_monthly_pattern_expands(self):
        """`data_{start_yyyymmdd}_{end_yyyymmdd}.tif` expands for `MS` cadence."""
        pat = "data_{start_yyyymmdd}_{end_yyyymmdd}.tif"
        out = pat.format(
            **CHIRPS._placeholders(pd.Timestamp("2020-01-01"), pandas_freq="MS")
        )
        assert out == "data_20200101_20200131.tif"

    def test_wbgt_dekad_pattern_expands(self):
        """`data_{start_yyyymmdd}_{end_yyyymmdd}.tif` expands for `10D` cadence."""
        pat = "data_{start_yyyymmdd}_{end_yyyymmdd}.tif"
        out = pat.format(
            **CHIRPS._placeholders(pd.Timestamp("2020-01-01"), pandas_freq="10D")
        )
        assert out == "data_20200101_20200110.tif"


class TestBundledWbgtDatasetsResolve:
    """The bundled WBGT rows expand cleanly under the M5 placeholders."""

    @pytest.mark.parametrize("ds_key", ["wbgt-monthly", "wbgt-dekad"])
    def test_wbgt_pattern_expands_for_january_2020(self, ds_key):
        """Both bundled WBGT rows expand without raising KeyError on January 2020."""
        ds = Catalog().get_dataset(ds_key)
        fmt = ds.default_format
        pat = ds.file_patterns[fmt]
        # The pre-M5 contract would have KeyError'd here because
        # `start_yyyymmdd` / `end_yyyymmdd` weren't in the placeholders.
        out = pat.format(
            **CHIRPS._placeholders(
                pd.Timestamp("2020-01-01"), pandas_freq=ds.pandas_freq
            )
        )
        # Both rows use the same template; sanity-check the shape.
        assert out.startswith("data_20200101_")
        assert out.endswith(".tif")
