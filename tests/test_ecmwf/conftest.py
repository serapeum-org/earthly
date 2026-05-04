"""Shared pytest fixtures for the ECMWF test suite.

Holds the four pieces every test in this directory needs:

* :func:`_block_real_cdsapi` — autouse safeguard that prevents any
  test (other than `TestApiE2E`) from constructing a real
  :class:`cdsapi.Client`.
* :func:`single_level_var_info` and :func:`pressure_level_var_info`
  — :class:`Variable` fixtures used across the api tests.
* :func:`ecmwf_stub` — a hand-constructed :class:`ECMWF` instance with
  the four attributes `_api()` consumes (`self.client`,
  `self.root_dir`, `self.time`, `self.space`) set by hand.
  Bypasses :meth:`AbstractDataSource.__init__` so unit tests can run
  without going through cdsapi or the file system.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import cdsapi
import pandas as pd
import pytest

from earthly.base import SpatialExtent, TemporalExtent
from earthly.ecmwf import ECMWF, Variable

_LIVE_CDS_TEST_CLASSES = frozenset({"TestApiE2E", "TestFacadeE2E"})


def pytest_collection_modifyitems(items):
    """Tag every test in this subtree with `@pytest.mark.ecmwf`.

    Lets the suite be filtered with `-m ecmwf` and lets the
    `test-ecmwf` pixi task / GitHub workflow step run only the
    ECMWF backend's tests.

    Pytest delivers the FULL item list to every conftest hook,
    not just items from this subtree, so we filter by path.
    """
    here = Path(__file__).parent.resolve()
    for item in items:
        try:
            if Path(item.fspath).resolve().is_relative_to(here):
                item.add_marker(pytest.mark.ecmwf)
        except (OSError, ValueError):
            continue


@pytest.fixture(autouse=True)
def _block_real_cdsapi(request, monkeypatch):
    """Fail fast if a test reaches a live :class:`cdsapi.Client`.

    Any test outside the explicit live-CDS allow-list gets a
    :class:`cdsapi.Client` replacement that raises immediately —
    even before the constructor reads `~/.cdsapirc`. Tests that
    need a fake client still call `monkeypatch.setattr(cdsapi,
    "Client", ...)` themselves; that later setattr wins because
    monkeypatch applies fixture-scoped overrides in order.

    The :func:`ecmwf_stub` fixture sets `skip_constraints=True` on
    the synthetic instance so tests that build synthetic requests
    via `_api()` bypass the pre-flight validator (which would
    otherwise hit the live CDS catalogue endpoint). Tests targeting
    the validator itself (in `test_constraints.py`) construct
    :class:`RequestValidator` directly so this default doesn't
    interfere.
    """
    if request.cls is not None and request.cls.__name__ in _LIVE_CDS_TEST_CLASSES:
        return

    def _no_live_client(*args, **kwargs):
        raise AssertionError(
            "A unit test attempted to construct a real cdsapi.Client. "
            'Add `monkeypatch.setattr(cdsapi, "Client", lambda: ...)` '
            "to the test (replacing the lambda with the fake your test "
            "needs), or move the test into a live class (TestApiE2E / "
            "TestFacadeE2E) selected via `pytest -m e2e`."
        )

    monkeypatch.setattr(cdsapi, "Client", _no_live_client)


@pytest.fixture
def single_level_var_info():
    """CDS catalog entry for a single-level ERA5 variable.

    Returns:
        Variable: Catalog metadata for `2m_temperature` on
        `reanalysis-era5-single-levels`.
    """
    return Variable(
        cds_dataset="reanalysis-era5-single-levels",
        cds_variable="2m_temperature",
        nc_variable="t2m",
        units="K",
        product_type=["reanalysis"],
    )


@pytest.fixture
def pressure_level_var_info():
    """CDS catalog entry for a pressure-level ERA5 variable.

    Returns:
        Variable: Catalog metadata for `temperature` on
        `reanalysis-era5-pressure-levels` at 1000 hPa.
    """
    return Variable(
        cds_dataset="reanalysis-era5-pressure-levels",
        cds_variable="temperature",
        cds_pressure_level=["1000"],
        nc_variable="t",
        units="K",
        product_type=["reanalysis"],
    )


@pytest.fixture
def ecmwf_stub(tmp_path):
    """Minimal `ECMWF` instance with the attributes `_api()` consumes.

    Skips the full parent `__init__` chain (which would still call
    :meth:`cdsapi.Client` for real) and instead constructs the
    instance via `ECMWF.__new__` and wires up the four attributes
    :meth:`ECMWF._api` reads — `self.client`, `self.root_dir`,
    `self.time` and `self.space` — by hand.

    Args:
        tmp_path: Per-test temp directory provided by pytest, used as
            `self.root_dir` so target paths land on the test fs.

    Returns:
        ECMWF: An `ECMWF` instance ready for `_api()` invocation.
    """
    ecmwf = ECMWF.__new__(ECMWF)
    ecmwf.client = MagicMock()
    ecmwf.root_dir = tmp_path
    ecmwf.time = TemporalExtent(
        start_date=pd.Timestamp("2022-01-01"),
        end_date=pd.Timestamp("2022-01-03"),
        resolution="D",
        dates=pd.date_range("2022-01-01", "2022-01-03", freq="D"),
    )
    ecmwf.space = SpatialExtent(
        latitude_min=4.19,
        latitude_max=4.64,
        longitude_min=-75.65,
        longitude_max=-74.73,
        resolution=0.125,
    )
    ecmwf.temporal_resolution = "daily"
    ecmwf.skip_constraints = True
    return ecmwf
