"""Tests for :mod:`earthlens.ecmwf.constraints`.

Validates the pre-flight check that catches mismatched
request / constraints.json combinations before they hit CDS.
"""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from earthlens.ecmwf import constraints as constraints_module
from earthlens.ecmwf.constraints import (
    Area,
    Dates,
    RequestValidator,
    fetch_constraints,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    """Reset the module-level cache between tests."""
    constraints_module._CACHE.clear()
    yield
    constraints_module._CACHE.clear()


def _stub_urlopen(monkeypatch, payload):
    """Replace :func:`urllib.request.urlopen` with a static fake."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else b"x"

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return body

    monkeypatch.setattr(
        constraints_module.urllib.request,
        "urlopen",
        lambda *_a, **_kw: _Resp(),
    )


def _stub_urlopen_raise(monkeypatch, exc):
    """Replace urlopen with one that raises the given exception."""

    def _raise(*_a, **_kw):
        raise exc

    monkeypatch.setattr(
        constraints_module.urllib.request,
        "urlopen",
        _raise,
    )


class TestValidateRequest:
    """Tests for :class:`RequestValidator`."""

    def test_valid_request_passes(self, monkeypatch):
        """A request that matches a constraint entry returns silently."""
        _stub_urlopen(
            monkeypatch,
            [
                {
                    "variable": ["2m_temperature"],
                    "year": ["2022"],
                    "product_type": ["reanalysis"],
                }
            ],
        )
        RequestValidator(
            "reanalysis-era5-single-levels",
            {
                "variable": ["2m_temperature"],
                "year": ["2022"],
                "product_type": ["reanalysis"],
            },
        ).check()

    def test_invalid_value_raises(self, monkeypatch):
        """Mismatched request keys raise ValueError naming offenders."""
        _stub_urlopen(
            monkeypatch,
            [
                {
                    "variable": ["2m_temperature"],
                    "year": ["2022"],
                    "product_type": ["analysis"],
                }
            ],
        )
        with pytest.raises(ValueError, match="product_type"):
            RequestValidator(
                "reanalysis-cerra-land",
                {
                    "variable": ["2m_temperature"],
                    "year": ["2022"],
                    "product_type": ["forecast"],
                },
            ).check()

    def test_universal_keys_skip_validation(self, monkeypatch):
        """`area` / `data_format` are not validated (CDS accepts globally)."""
        _stub_urlopen(
            monkeypatch,
            [{"variable": ["2m_temperature"], "year": ["2022"]}],
        )
        # `area` is not in any constraint entry — must still pass.
        RequestValidator(
            "reanalysis-era5-single-levels",
            {
                "variable": ["2m_temperature"],
                "year": ["2022"],
                "area": [60, -20, 50, 0],
                "data_format": "netcdf",
            },
        ).check()

    def test_unknown_keys_skip_validation(self, monkeypatch):
        """Keys the constraints document does not enumerate are ignored."""
        _stub_urlopen(
            monkeypatch,
            [{"variable": ["2m_temperature"], "year": ["2022"]}],
        )
        # `custom_key` is not in constraints — should be ignored.
        RequestValidator(
            "reanalysis-era5-single-levels",
            {
                "variable": ["2m_temperature"],
                "year": ["2022"],
                "custom_key": "anything",
            },
        ).check()

    def test_empty_constraints_skip_validation(self, monkeypatch):
        """An empty constraints document silently allows anything."""
        _stub_urlopen(monkeypatch, [])
        RequestValidator(
            "reanalysis-era5-complete",
            {"variable": ["any"], "year": ["2099"]},
        ).check()

    def test_missing_endpoint_skip_validation(self, monkeypatch):
        """A 404 / network error treats validation as a no-op."""
        _stub_urlopen_raise(
            monkeypatch,
            HTTPError("u", 404, "Not Found", None, io.BytesIO(b"")),
        )
        # No exception expected — validation falls back to allow.
        RequestValidator(
            "provider-c3s-data-rescue-without",
            {"variable": ["x"]},
        ).check()

    def test_skip_flag_bypasses_fetch(self, monkeypatch):
        """`skip=True` short-circuits validation entirely."""

        def _fail(*_a, **_kw):
            raise AssertionError("urlopen must not be called when skip is True")

        monkeypatch.setattr(constraints_module.urllib.request, "urlopen", _fail)
        RequestValidator(
            "reanalysis-era5-single-levels",
            {"variable": ["nonsense"], "year": ["1492"]},
            skip=True,
        ).check()

    def test_constraints_cached_between_calls(self, monkeypatch):
        """Two validations against the same dataset hit the network once."""
        call_count = {"n": 0}

        def _counting_urlopen(*_a, **_kw):
            call_count["n"] += 1
            return _Resp()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps([{"variable": ["x"], "year": ["2022"]}]).encode(
                    "utf-8"
                )

        monkeypatch.setattr(
            constraints_module.urllib.request,
            "urlopen",
            _counting_urlopen,
        )
        request = {"variable": ["x"], "year": ["2022"]}
        RequestValidator("reanalysis-era5-single-levels", request).check()
        RequestValidator("reanalysis-era5-single-levels", request).check()
        assert call_count["n"] == 1

    def test_partial_match_in_request_list_raises(self, monkeypatch):
        """A request listing a value not in *any* constraint entry raises.

        The constraint entry has `year=[2022]`; if the request asks
        for both `[2022, 2099]`, validation must reject because no
        entry covers `2099`.
        """
        _stub_urlopen(
            monkeypatch,
            [{"variable": ["2m_temperature"], "year": ["2022"]}],
        )
        with pytest.raises(ValueError, match="year"):
            RequestValidator(
                "reanalysis-era5-single-levels",
                {"variable": ["2m_temperature"], "year": ["2022", "2099"]},
            ).check()


class TestCombinatorialPartitionUnion:
    """Tests for the time-partition-union branch of `_check_combinatorial`.

    Real datasets (notably ``reanalysis-era5-land-monthly-means``) publish
    constraints as multiple structurally identical entries that differ only
    in the time partition (e.g. ``month=['01'..'04']`` vs ``['05'..'12']``).
    CDS itself accepts requests spanning the partition boundary by silently
    splitting the retrieval, so the validator must too. These tests pin
    the union semantics: non-time keys still require single-entry cover,
    time-partition keys (`year`, `month`, `day`) get unioned across the
    entries that satisfy the non-time keys.
    """

    @staticmethod
    def _partitioned_constraints():
        """Return a two-entry stub modelled on ERA5-Land monthly-means.

        Both entries share `variable` and `product_type` but partition
        on `month` (Jan-Apr vs May-Dec) and `year` (entry A includes
        2026 but entry B stops at 2025 — mirrors the real `constraints.json`).
        """
        return [
            {
                "variable": ["2m_temperature", "total_precipitation"],
                "product_type": ["monthly_averaged_reanalysis"],
                "month": ["01", "02", "03", "04"],
                "year": ["2022", "2023", "2024", "2025", "2026"],
            },
            {
                "variable": ["2m_temperature", "total_precipitation"],
                "product_type": ["monthly_averaged_reanalysis"],
                "month": ["05", "06", "07", "08", "09", "10", "11", "12"],
                "year": ["2022", "2023", "2024", "2025"],
            },
        ]

    def test_cross_month_partition_request_passes(self, monkeypatch):
        """A request whose months span the partition boundary now passes.

        The pre-fix validator rejected this with "Request does not match
        any constraint entry" because no single entry covered months 04
        and 05 simultaneously. CDS accepts it server-side.
        """
        _stub_urlopen(monkeypatch, self._partitioned_constraints())
        RequestValidator(
            "reanalysis-era5-land-monthly-means",
            {
                "variable": ["2m_temperature"],
                "product_type": ["monthly_averaged_reanalysis"],
                "year": ["2022"],
                "month": ["04", "05", "06"],
            },
        ).check()

    def test_cross_partition_request_with_unknown_variable_rejected(
        self, monkeypatch
    ):
        """Variable typo still raises before the time-partition union runs.

        Phase 3 (variable-typo check) rejects the request before
        `_check_combinatorial` is reached. The error message names the
        offending variable name — not a `month` mismatch — so the user
        sees the real cause.
        """
        _stub_urlopen(monkeypatch, self._partitioned_constraints())
        with pytest.raises(ValueError, match="unknown variable"):
            RequestValidator(
                "reanalysis-era5-land-monthly-means",
                {
                    "variable": ["bogus_typo_var"],
                    "product_type": ["monthly_averaged_reanalysis"],
                    "year": ["2022"],
                    "month": ["04", "05"],
                },
            ).check()

    def test_cross_partition_request_with_year_outside_union_rejected(
        self, monkeypatch
    ):
        """Year outside the union of every partition still rejected.

        Both entries cover years 2022–2025/26; requesting year 1850
        means the time-partition union check fails after the non-time
        keys are satisfied.
        """
        _stub_urlopen(monkeypatch, self._partitioned_constraints())
        with pytest.raises(ValueError, match="year"):
            RequestValidator(
                "reanalysis-era5-land-monthly-means",
                {
                    "variable": ["2m_temperature"],
                    "product_type": ["monthly_averaged_reanalysis"],
                    "year": ["1850"],
                    "month": ["04", "05"],
                },
            ).check()

    def test_cross_partition_year_only_in_one_partition_passes(
        self, monkeypatch
    ):
        """Year present in only one partition is still OK if some entry
        covers it.

        Year 2026 is in the Jan-Apr entry but not the May-Dec entry. If
        the user requests month=['02', '03'] with year=['2026'], the
        Jan-Apr entry alone covers it — request must pass even though
        the other entry would reject the year.
        """
        _stub_urlopen(monkeypatch, self._partitioned_constraints())
        RequestValidator(
            "reanalysis-era5-land-monthly-means",
            {
                "variable": ["2m_temperature"],
                "product_type": ["monthly_averaged_reanalysis"],
                "year": ["2026"],
                "month": ["02", "03"],
            },
        ).check()

    def test_pure_non_time_cross_entry_request_rejected(self, monkeypatch):
        """Crossing a NON-time key across two entries still rejected.

        Variable A is only in entry A; variable B is only in entry B.
        A request asking for both can never be satisfied by a single
        entry's non-time-key cover, so the union path does not apply
        and the validator raises.
        """
        _stub_urlopen(
            monkeypatch,
            [
                {
                    "variable": ["var_a"],
                    "product_type": ["pt_a"],
                    "year": ["2022"],
                },
                {
                    "variable": ["var_b"],
                    "product_type": ["pt_b"],
                    "year": ["2022"],
                },
            ],
        )
        with pytest.raises(ValueError):
            RequestValidator(
                "fake-dataset-with-non-time-split",
                {
                    "variable": ["var_a", "var_b"],
                    "product_type": ["pt_a"],
                    "year": ["2022"],
                },
            ).check()

    def test_single_entry_request_still_passes(self, monkeypatch):
        """Regression: a non-partition dataset with a single entry still
        validates the same way as before the partition-union rewrite.
        """
        _stub_urlopen(
            monkeypatch,
            [
                {
                    "variable": ["2m_temperature"],
                    "product_type": ["reanalysis"],
                    "year": ["2022"],
                    "month": ["01", "02", "03"],
                }
            ],
        )
        RequestValidator(
            "reanalysis-era5-single-levels",
            {
                "variable": ["2m_temperature"],
                "product_type": ["reanalysis"],
                "year": ["2022"],
                "month": ["01", "02"],
            },
        ).check()

    def test_cross_partition_combination_only_partially_served_rejected(
        self, monkeypatch
    ):
        """A request whose individual values exist in the per-key union
        but whose specific tuple is not in any entry must be rejected.

        Models the partition where year 2026 is only on the Jan-Apr
        side: a request asking for both `month=06` (Jan-Apr partition
        does not have it) and `year=2026` (May-Dec partition does
        not have it) cannot be served by any single entry, even
        though `2026` and `06` each appear in *some* entry's values.

        The earlier per-key union check (with hardcoded
        `_TIME_PARTITION_KEYS`) over-accepted this because it
        unioned `month` and `year` independently. The cross-product
        check catches it because the tuple `(year=2026, month=06)`
        lands in no entry.
        """
        _stub_urlopen(monkeypatch, self._partitioned_constraints())
        with pytest.raises(ValueError, match=r"(?i)does not match"):
            RequestValidator(
                "reanalysis-era5-land-monthly-means",
                {
                    "variable": ["2m_temperature"],
                    "product_type": ["monthly_averaged_reanalysis"],
                    "year": ["2026"],
                    "month": ["02", "06"],
                },
            ).check()

    def test_cross_key_partition_on_non_time_dimension(self, monkeypatch):
        """The cross-product check accepts non-time partitioning too.

        A hypothetical dataset partitioning on `level_type` (one
        entry per surface / pressure / model levels with otherwise
        identical structural keys) is handled the same way as the
        time-partition case — no special-casing required. Each
        tuple in the request lands in exactly one entry.
        """
        _stub_urlopen(
            monkeypatch,
            [
                {
                    "variable": ["temperature"],
                    "level_type": ["surface_levels"],
                    "year": ["2022"],
                },
                {
                    "variable": ["temperature"],
                    "level_type": ["pressure_levels"],
                    "year": ["2022"],
                },
            ],
        )
        RequestValidator(
            "reanalysis-cerra-fictional",
            {
                "variable": ["temperature"],
                "level_type": ["surface_levels", "pressure_levels"],
                "year": ["2022"],
            },
        ).check()


class TestDateValidity:
    """Tests for the M17 date sanity check."""

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError, match="month=.*must be 01-12"):
            Dates.check({"month": ["13"]})

    def test_invalid_day_raises(self):
        with pytest.raises(ValueError, match="day=.*must be 01-31"):
            Dates.check({"day": ["32"]})

    def test_year_out_of_range_raises(self):
        with pytest.raises(ValueError, match="year=.*plausible"):
            Dates.check({"year": ["1492"]})

    def test_cross_month_day_combo_passes(self):
        """Day=30 with month=02 passes: CDS accepts exhaustive day enumerations."""
        Dates.check({"year": ["2022"], "month": ["02"], "day": ["30"]})

    def test_valid_date_passes(self):
        Dates.check({"year": ["2022"], "month": ["02"], "day": ["28"]})

    def test_non_integer_values_skipped(self):
        """Datasets that use `year=['all']` or non-numeric forms pass."""
        Dates.check({"year": ["all"], "month": ["any"]})


class TestAreaSanity:
    """Tests for the M17 area bbox sanity check."""

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="4-element list"):
            Area.check({"area": [60, -10, 50]})

    def test_swapped_north_south_raises(self):
        with pytest.raises(ValueError, match="south.*<=.*north"):
            Area.check({"area": [40, -10, 60, 0]})

    def test_latitude_out_of_range_raises(self):
        with pytest.raises(ValueError, match="latitudes must be in"):
            Area.check({"area": [95, -10, 50, 0]})

    def test_valid_area_passes(self):
        Area.check({"area": [60, -10, 50, 0]})

    def test_no_area_passes(self):
        Area.check({})


class TestVariableTypos:
    """Tests for the M17 variable typo detector."""

    def test_typo_suggests_close_match(self, monkeypatch):
        """A near-miss variable name surfaces a `did you mean` hint."""
        _stub_urlopen(
            monkeypatch,
            [
                {
                    "variable": [
                        "2m_temperature",
                        "total_precipitation",
                    ],
                    "year": ["2022"],
                }
            ],
        )
        with pytest.raises(ValueError, match="did you mean"):
            RequestValidator(
                "reanalysis-era5-single-levels",
                {
                    "variable": ["2m_temprature"],
                    "year": ["2022"],
                },
            ).check()

    def test_completely_unknown_variable_raises_without_suggestion(self, monkeypatch):
        _stub_urlopen(
            monkeypatch,
            [{"variable": ["a", "b"], "year": ["2022"]}],
        )
        with pytest.raises(ValueError, match="unknown variable"):
            RequestValidator(
                "ds",
                {"variable": ["zzzzz_completely_off"], "year": ["2022"]},
            ).check()


class TestRequiredFields:
    """Tests for the M17 required-field detector."""

    def test_missing_required_extra_raises(self, monkeypatch):
        """Every constraint entry has `experiment` => it's required."""
        _stub_urlopen(
            monkeypatch,
            [
                {
                    "variable": ["tas"],
                    "year": ["2000"],
                    "experiment": ["historical"],
                },
                {
                    "variable": ["tas"],
                    "year": ["2020"],
                    "experiment": ["ssp585"],
                },
            ],
        )
        with pytest.raises(ValueError, match="missing required key"):
            RequestValidator(
                "projections-cmip6",
                {"variable": ["tas"], "year": ["2000"]},
            ).check()

    def test_optional_extra_not_flagged(self, monkeypatch):
        """A key absent from at least one entry is treated as optional."""
        _stub_urlopen(
            monkeypatch,
            [
                {"variable": ["tas"], "year": ["2000"], "level": ["1"]},
                {"variable": ["tas"], "year": ["2000"]},  # no level
            ],
        )
        RequestValidator(
            "ds",
            {"variable": ["tas"], "year": ["2000"]},
        ).check()


class TestFetchConstraints:
    """Tests for :func:`fetch_constraints`."""

    def test_returns_payload_on_success(self, monkeypatch):
        _stub_urlopen(monkeypatch, [{"variable": ["x"]}])
        assert fetch_constraints("ds") == [{"variable": ["x"]}]

    def test_returns_empty_list_on_404(self, monkeypatch):
        _stub_urlopen_raise(
            monkeypatch,
            HTTPError("u", 404, "Not Found", None, io.BytesIO(b"")),
        )
        assert fetch_constraints("ds") == []

    def test_returns_empty_list_on_non_list_payload(self, monkeypatch):
        _stub_urlopen(monkeypatch, {"unexpected": "object"})
        assert fetch_constraints("ds") == []

    def test_returns_empty_list_on_malformed_json(self, monkeypatch):
        """Broken JSON syntax falls through the ValueError except branch."""
        _stub_urlopen(monkeypatch, None)
        assert fetch_constraints("ds-malformed") == []

    def test_non_https_url_template_rejected(self, monkeypatch):
        """A URL template not starting with `https://` raises before urlopen."""
        monkeypatch.setattr(
            constraints_module,
            "CONSTRAINTS_URL_TEMPLATE",
            "http://malicious.example/constraints/{dataset}.json",
        )

        def _fail(*_a, **_kw):
            raise AssertionError("urlopen must not run for non-https URLs")

        monkeypatch.setattr(constraints_module.urllib.request, "urlopen", _fail)
        with pytest.raises(ValueError, match="non-https URL"):
            fetch_constraints("any-dataset")
