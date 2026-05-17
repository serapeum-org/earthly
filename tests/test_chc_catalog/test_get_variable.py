"""Lock-in for M4: `Catalog.get_variable` no longer defaults `variable_name`."""

from __future__ import annotations

import inspect

import pytest

from earthlens.chc import Catalog

pytestmark = [pytest.mark.chc]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    """Bundled catalog, loaded once per module."""
    return Catalog()


class TestGetVariable:
    """`Catalog.get_variable` requires an explicit variable_name (post-M4)."""

    def test_signature_has_no_default_for_variable_name(self):
        """The 2nd positional arg has no default value (M4 removed the pre-M4 'precipitation')."""
        sig = inspect.signature(Catalog.get_variable)
        param = sig.parameters["variable_name"]
        assert param.default is inspect.Parameter.empty, (
            f"variable_name should be required after M4; got default={param.default!r}"
        )

    def test_explicit_variable_name_returns_variable(self, catalog: Catalog):
        """Explicit (dataset_key, variable_name) still resolves to the Variable."""
        var = catalog.get_variable("global-daily", "precipitation")
        assert var.units == "mm/day"
        assert var.is_flux is True

    def test_works_for_non_precipitation_variables(self, catalog: Catalog):
        """Non-precipitation variables (the M4 motivating case) resolve too."""
        # chirtsdaily-tmax exposes `tmax`, not `precipitation`
        tmax = catalog.get_variable("chirtsdaily-tmax", "tmax")
        assert tmax.units == "degC"
        # wbgt-monthly exposes `wbgt`
        wbgt = catalog.get_variable("wbgt-monthly", "wbgt")
        assert wbgt.units == "degC"

    def test_zero_arg_call_raises_type_error(self, catalog: Catalog):
        """Calling get_variable(dataset_key) without variable_name now raises."""
        with pytest.raises(TypeError):
            catalog.get_variable("global-daily")  # type: ignore[call-arg]

    def test_unknown_variable_raises_key_error(self, catalog: Catalog):
        """A wrong variable name still raises KeyError (unchanged contract)."""
        with pytest.raises(KeyError):
            catalog.get_variable("global-daily", "not-a-variable")
