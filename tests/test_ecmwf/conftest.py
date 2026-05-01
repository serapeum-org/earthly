"""Shared pytest fixtures for the ECMWF test suite.

Holds the four pieces every test in this directory needs:

* :func:`_block_real_cdsapi` — autouse safeguard that prevents any
  test (other than `TestApiE2E`) from constructing a real
  :class:`cdsapi.Client`.
* :func:`single_level_var_info` and :func:`pressure_level_var_info`
  — :class:`Variable` fixtures used across the api / post_download
  tests.
* :func:`ecmwf_stub` — a hand-constructed :class:`ECMWF` instance with
  the four attributes `api()` / `post_download()` consume
  (`self.client`, `self.root_dir`, `self.time`, `self.space`)
  set by hand. Bypasses :meth:`AbstractDataSource.__init__` so unit
  tests can run without going through cdsapi or the file system.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import cdsapi
import pandas as pd
import pytest

from earth2observe.base import SpatialExtent, TemporalExtent
from earth2observe.ecmwf import ECMWF, Variable


_LIVE_CDS_TEST_CLASSES = frozenset({"TestApiE2E", "TestFacadeE2E"})


@pytest.fixture(autouse=True)
def _block_real_cdsapi(request, monkeypatch):
    """Fail fast if a test reaches a live :class:`cdsapi.Client`.

    Any test outside the explicit live-CDS allow-list gets a
    :class:`cdsapi.Client` replacement that raises immediately —
    even before the constructor reads `~/.cdsapirc`. Tests that
    need a fake client still call `monkeypatch.setattr(cdsapi,
    "Client", ...)` themselves; that later setattr wins because
    monkeypatch applies fixture-scoped overrides in order.

    Also sets `E2O_SKIP_CONSTRAINTS=1` so the api() pre-flight
    validator does not hit the live CDS catalogue endpoint during
    unit tests that build synthetic requests. Tests targeting the
    validator itself (in `test_constraints.py`) override this
    via `monkeypatch.delenv`.
    """
    monkeypatch.setenv("E2O_SKIP_CONSTRAINTS", "1")
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
    )


@pytest.fixture
def ecmwf_stub(tmp_path):
    """Minimal `ECMWF` instance with the attributes `api()` consumes.

    Skips the full parent `__init__` chain (which would still call
    :meth:`cdsapi.Client` for real) and instead constructs the
    instance via `ECMWF.__new__` and wires up the four attributes
    :meth:`ECMWF.api` reads — `self.client`, `self.root_dir`,
    `self.time` and `self.space` — by hand.

    Args:
        tmp_path: Per-test temp directory provided by pytest, used as
            `self.root_dir` so target paths land on the test fs.

    Returns:
        ECMWF: An `ECMWF` instance ready for `api()` invocation.
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
    return ecmwf
