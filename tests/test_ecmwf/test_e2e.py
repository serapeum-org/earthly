"""End-to-end tests against the live Copernicus Climate Data Store.

Every test in this file submits a tiny request (one day or one
month, ~1°×1° area, one or two variables) so the per-test wall
clock stays in the minutes range and the CDS quota footprint stays
small. CDS queue times are real — expect each test to block.
"""

from __future__ import annotations

import pytest

from earthlens.earthlens import EarthLens
from earthlens.ecmwf import ECMWF, Catalog

pytestmark = [pytest.mark.e2e]

_BBOX_LAT = [4.0, 5.0]  # ~1° square over Colombia, small footprint
_BBOX_LON = [-75.0, -74.0]


class TestApiE2E:
    """End-to-end tests against the live Copernicus Climate Data Store."""

    def test_live_single_level_download(self, tmp_path):
        """Daily 2m_temperature on reanalysis-era5-single-levels."""
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-single-levels": ["2m-temperature"],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=tmp_path,
            temporal_resolution="daily",
        )

        target = ecmwf._api(
            Catalog().get_variable("reanalysis-era5-single-levels", "2m-temperature")
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"

    def test_live_pressure_level_download(self, tmp_path):
        """Daily temperature on reanalysis-era5-pressure-levels at 1000 hPa."""
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-pressure-levels": ["temperature"],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=tmp_path,
            temporal_resolution="daily",
        )

        target = ecmwf._api(
            Catalog().get_variable("reanalysis-era5-pressure-levels", "temperature")
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"

    def test_live_monthly_aggregation(self, tmp_path):
        """Monthly 2m_temperature on the synthesized -monthly-means dataset.

        After the dataset/product_type decoupling, the monthly path
        is selected by naming the monthly dataset directly in
        `variables`. The catalog's auto-synthesized row carries
        `product_type=['monthly_averaged_reanalysis']` and
        `temporal_resolution='monthly'` shapes the request body
        (single `time` slot, no `day` field).
        """
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-single-levels-monthly-means": ["2m-temperature"],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=tmp_path,
            temporal_resolution="monthly",
        )

        target = ecmwf._api(
            Catalog().get_variable(
                "reanalysis-era5-single-levels-monthly-means", "2m-temperature"
            )
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"
        assert (
            "reanalysis-era5-single-levels-monthly-means" in target.name
        ), f"Monthly retrieve should land at -monthly-means; got {target.name}"


class TestFacadeE2E:
    """End-to-end tests for the `EarthLens` facade."""

    def test_live_multi_variable_download_through_facade(self, tmp_path):
        """`EarthLens(...).download()` chains every stage end-to-end."""
        earthlens = EarthLens(
            data_source="ecmwf",
            temporal_resolution="daily",
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-single-levels": [
                    "2m-temperature",
                    "total-precipitation",
                ],
            },
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=tmp_path,
        )

        earthlens.download(progress_bar=False)

        # Each variable gets its own
        # <cds_variable>_<cds_dataset>.nc under tmp_path.
        produced = sorted(p.name for p in tmp_path.glob("*.nc"))
        assert (
            "2m_temperature_reanalysis-era5-single-levels.nc" in produced
        ), f"2T NetCDF missing from outputs: {produced}"
        assert (
            "total_precipitation_reanalysis-era5-single-levels.nc" in produced
        ), f"TP NetCDF missing from outputs: {produced}"
