"""End-to-end tests against the live Copernicus Climate Data Store.

Every test in this file submits a tiny request (one day or one
month, ~1°×1° area, one or two variables) so the per-test wall
clock stays in the minutes range and the CDS quota footprint stays
small. CDS queue times are real — expect each test to block.
"""

from __future__ import annotations

import numpy as np
import pytest

from earthly.earthly import Earthly
from earthly.ecmwf import Catalog, ECMWF

pytestmark = [pytest.mark.e2e]

_BBOX_LAT = [4.0, 5.0]   # ~1° square over Colombia, small footprint
_BBOX_LON = [-75.0, -74.0]


class TestApiE2E:
    """End-to-end tests against the live Copernicus Climate Data Store."""

    def test_live_single_level_download(self, tmp_path):
        """Daily 2m_temperature on reanalysis-era5-single-levels.

        Test scenario:
            Exercise the daily single-level path end-to-end. `api()`
            must call cdsapi.Client.retrieve, write a non-empty
            NetCDF, and return its absolute path.
        """
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-single-levels": ["2m-temperature"],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
            temporal_resolution="daily",
        )

        target = ecmwf.api(
            Catalog().get_variable(
                "reanalysis-era5-single-levels", "2m-temperature"
            )
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"

    def test_live_pressure_level_download(self, tmp_path):
        """Daily temperature on reanalysis-era5-pressure-levels at 1000 hPa.

        Test scenario:
            Exercise the pressure-level branch of `api()`. The
            catalog entry for `T` carries
            `cds_pressure_level=['1000']`; the request must
            include that key and the retrieve must succeed.
        """
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-pressure-levels": ["temperature"],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
            temporal_resolution="daily",
        )

        target = ecmwf.api(
            Catalog().get_variable(
                "reanalysis-era5-pressure-levels", "temperature"
            )
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"

    def test_live_monthly_aggregation(self, tmp_path):
        """Monthly 2m_temperature routes to -monthly-means dataset.

        Test scenario:
            Exercise the M5 monthly branch. With
            `temporal_resolution='monthly'` and a 1-month range,
            `api()` must target `cds_dataset_monthly`
            (`reanalysis-era5-single-levels-monthly-means`) and
            send `product_type=['monthly_averaged_reanalysis']`
            without a `time` key.
        """
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-single-levels": ["2m-temperature"],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
            temporal_resolution="monthly",
        )

        target = ecmwf.api(
            Catalog().get_variable(
                "reanalysis-era5-single-levels", "2m-temperature"
            )
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"
        assert (
            "reanalysis-era5-single-levels-monthly-means" in target.name
        ), f"Monthly retrieve should land at -monthly-means; got {target.name}"


class TestFacadeE2E:
    """End-to-end tests for the `Earthly` facade."""

    def test_live_multi_variable_download_through_facade(self, tmp_path):
        """`Earthly(...).download()` chains every stage end-to-end.

        Test scenario:
            Exercise C1+C3+H1+H2+H3+M3 together: facade dispatch,
            `self.vars` iteration, two retrieves (one per
            variable), no spurious `data_interim.nc` deletion,
            and partial-success aggregation. `2T` and `TP` are
            both single-level so the request shape is uniform but
            distinct dataset+variable pairs go to CDS.
        """
        earthly = Earthly(
            data_source="ecmwf",
            temporal_resolution="daily",
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-single-levels": [
                    "2m-temperature", "total-precipitation",
                ],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=tmp_path,
        )

        earthly.download(progress_bar=False)

        # Each variable gets its own
        # <cds_variable>_<cds_dataset>.nc under tmp_path.
        produced = sorted(p.name for p in tmp_path.glob("*.nc"))
        assert "2m_temperature_reanalysis-era5-single-levels.nc" in produced, (
            f"2T NetCDF missing from outputs: {produced}"
        )
        assert "total_precipitation_reanalysis-era5-single-levels.nc" in produced, (
            f"TP NetCDF missing from outputs: {produced}"
        )
