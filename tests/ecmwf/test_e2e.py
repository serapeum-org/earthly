"""End-to-end tests against the live Copernicus Climate Data Store.

These tests are opt-in: they run only when ``RUN_CDS_E2E=1`` is set
in the environment. They require:

* A ``~/.cdsapirc`` file with ``url`` and a Personal Access Token,
  see ``docs/authentication.md`` and
  <https://cds.climate.copernicus.eu/how-to-api>.
* Accepted licences for the ERA5 single-levels dataset on the user's
  CDS profile.

Each request can take several minutes due to CDS queue times. The
request below is intentionally tiny (one day, one variable, ~1°×1°)
to keep the wall clock and quota footprint small.

The autouse ``_block_real_cdsapi`` safeguard from ``conftest.py``
recognises ``TestApiE2E`` by class name and steps aside, so this
file is the only place that may construct a real cdsapi.Client.
"""

from __future__ import annotations

import os

import cdsapi
import pandas as pd
import pytest

from earth2observe.abstractdatasource import SpatialBounds, TimeWindow
from earth2observe.ecmwf import ECMWF, VariableSpec

pytestmark = [pytest.mark.e2e]


@pytest.mark.skipif(
    os.environ.get("RUN_CDS_E2E") != "1",
    reason=(
        "Set RUN_CDS_E2E=1 to run live CDS end-to-end tests "
        "(requires ~/.cdsapirc and accepted ERA5 licences)."
    ),
)
class TestApiE2E:
    """End-to-end tests against the live Copernicus Climate Data Store."""

    def test_live_single_level_download(self, tmp_path):
        """Submit a tiny ERA5 single-levels request to the real CDS.

        Test scenario:
            One day (2022-01-01), one variable (2m_temperature), one
            degree square area centred on Colombia. Asserts that
            ``api()`` returns a path that exists and is non-empty
            after the live retrieve.
        """
        ecmwf = ECMWF.__new__(ECMWF)
        ecmwf.client = cdsapi.Client()
        ecmwf.root_dir = tmp_path
        ecmwf.time = TimeWindow(
            start_date=pd.Timestamp("2022-01-01"),
            end_date=pd.Timestamp("2022-01-01"),
            time_freq="D",
            dates=pd.date_range("2022-01-01", "2022-01-01", freq="D"),
        )
        ecmwf.space = SpatialBounds(
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
        )
        ecmwf.temporal_resolution = "daily"

        target = ecmwf.api(
            VariableSpec(
                cds_dataset="reanalysis-era5-single-levels",
                cds_variable="2m_temperature",
                nc_variable="t2m",
                file_name="Tair",
                units="C",
                factors_add=0,
                factors_mul=1,
            )
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"
