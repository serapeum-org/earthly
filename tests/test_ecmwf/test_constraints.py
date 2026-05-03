"""Tests for :mod:`earthly.ecmwf.constraints`.

Validates the pre-flight check that catches mismatched
request / constraints.json combinations before they hit CDS.
"""

from __future__ import annotations

import io
import json
from urllib.error import HTTPError

import pytest

from earthly.ecmwf import constraints as constraints_module
from earthly.ecmwf.constraints import (
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

    def test_feb_30_raises(self):
        with pytest.raises(ValueError, match="not a real date"):
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
