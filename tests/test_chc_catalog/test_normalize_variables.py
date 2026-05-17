"""Lock-in for N2: `_normalize_variables` raises `ValueError`, not `KeyError`, on bad temporal_resolution."""

from __future__ import annotations

import pytest

from earthlens.chc.backend import CHIRPS, _LEGACY_DATASET_KEY

pytestmark = [pytest.mark.chc]


class TestNormalizeVariables:
    """`CHIRPS._normalize_variables` (and the constructor it backs) signal value errors with `ValueError`."""

    def test_list_shape_with_unknown_temporal_resolution_raises_value_error(self):
        """A list-shape `variables` paired with an unsupported `temporal_resolution` raises ValueError."""
        with pytest.raises(ValueError, match=r"not supported") as exc:
            CHIRPS._normalize_variables(
                ["precipitation"], temporal_resolution="pentadal"
            )
        message = str(exc.value)
        assert "pentadal" in message
        # The error must list the supported legacy keys.
        for legacy in _LEGACY_DATASET_KEY:
            assert legacy in message, message

    def test_list_shape_with_known_temporal_resolution_returns_dict(self):
        """A list-shape `variables` paired with a legacy `temporal_resolution` returns the dict-shape mapping."""
        for legacy_key, dataset_key in _LEGACY_DATASET_KEY.items():
            result = CHIRPS._normalize_variables(
                ["precipitation"], temporal_resolution=legacy_key
            )
            assert result == {dataset_key: ["precipitation"]}

    def test_dict_shape_pass_through_ignores_temporal_resolution(self):
        """A dict-shape `variables` is returned unchanged regardless of `temporal_resolution`."""
        result = CHIRPS._normalize_variables(
            {"africa-pentad": ["precipitation"]},
            temporal_resolution="not-a-resolution",
        )
        assert result == {"africa-pentad": ["precipitation"]}

    def test_none_variables_defaults_to_precipitation(self):
        """`variables=None` resolves to `["precipitation"]` and then through the legacy lookup."""
        result = CHIRPS._normalize_variables(
            None, temporal_resolution="daily"
        )
        assert result == {"global-daily": ["precipitation"]}

    def test_constructor_propagates_value_error_for_bad_temporal_resolution(self):
        """The public `CHIRPS(...)` ctor surfaces the same ValueError, not KeyError."""
        with pytest.raises(ValueError, match=r"not supported"):
            CHIRPS(
                variables=["precipitation"],
                temporal_resolution="dekadal",
                start="2020-01-01",
                end="2020-01-02",
                lat_lim=[0.0, 1.0],
                lon_lim=[0.0, 1.0],
            )
