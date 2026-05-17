"""Tests for `earthlens.base.leaves.FluxableLeaf`."""

from __future__ import annotations

import pytest

from earthlens.base.leaves import FluxableLeaf


class TestFluxableLeaf:
    """Tests for the `FluxableLeaf` mixin."""

    def test_default_types_is_none(self):
        """`types` defaults to `None` for state / instantaneous quantities."""
        leaf = FluxableLeaf()
        assert leaf.types is None
        assert leaf.is_flux is False

    def test_types_flux_marks_is_flux_true(self):
        """`types="flux"` flips `is_flux` to `True`."""
        assert FluxableLeaf(types="flux").is_flux is True

    @pytest.mark.parametrize("types", ["state", "instant", "accumulated", "FLUX"])
    def test_non_flux_string_keeps_is_flux_false(self, types):
        """Anything other than the exact string `"flux"` leaves `is_flux` False."""
        assert FluxableLeaf(types=types).is_flux is False

    def test_frozen_model_rejects_mutation(self):
        """The leaf is a frozen pydantic model — assignment raises."""
        leaf = FluxableLeaf(types="flux")
        with pytest.raises(Exception):
            leaf.types = "state"

    def test_extra_fields_rejected(self):
        """`extra="forbid"` blocks unknown fields at construction."""
        with pytest.raises(Exception):
            FluxableLeaf(types="flux", surprise=1)
