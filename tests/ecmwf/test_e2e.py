"""End-to-end tests against the live Copernicus Climate Data Store.

Opt-in via ``RUN_CDS_E2E=1``; see ``docs/authentication.md`` for
the ``~/.cdsapirc`` setup. The autouse safeguard in ``conftest.py``
exempts ``TestApiE2E`` by class name.
"""

from __future__ import annotations

import cdsapi
import pandas as pd
import pytest

from earth2observe.abstractdatasource import SpatialBounds, TimeWindow
from earth2observe.ecmwf import ECMWF, VariableSpec

pytestmark = [pytest.mark.e2e]


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
