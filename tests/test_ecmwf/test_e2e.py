"""End-to-end tests against the live Copernicus Climate Data Store.

Every test in this file submits a tiny request (one day or one
month, ~1°×1° area, one or two variables) so the per-test wall
clock stays in the minutes range and the CDS quota footprint stays
small. CDS queue times are real — expect each test to block.
"""

from __future__ import annotations

import numpy as np
import pytest

from earth2observe.earth2observe import Earth2Observe
from earth2observe.ecmwf import Catalog, ECMWF

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
            variables=["2m-temperature"],
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
            temporal_resolution="daily",
        )

        target = ecmwf.api(Catalog().get_dataset("2m-temperature"))

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
            variables=["temperature"],
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
            temporal_resolution="daily",
        )

        target = ecmwf.api(Catalog().get_dataset("temperature"))

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
            variables=["2m-temperature"],
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
            temporal_resolution="monthly",
        )

        target = ecmwf.api(Catalog().get_dataset("2m-temperature"))

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"
        assert (
            "reanalysis-era5-single-levels-monthly-means" in target.name
        ), f"Monthly retrieve should land at -monthly-means; got {target.name}"

    def test_live_post_download_reads_real_netcdf(self, tmp_path):
        """`post_download` parses a real CDS NetCDF and applies factors.

        Test scenario:
            Run the full download_dataset → api → post_download
            chain against the live service. `post_download` must
            open the real NetCDF written by CDS, slice on the time
            axis, and apply the K → C conversion. The 2m
            temperature for any inhabited surface point in January
            is well within `-50 °C .. +50 °C`; assert against
            that bound.
        """
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables=["2m-temperature"],
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
            temporal_resolution="daily",
        )
        spec = Catalog().get_dataset("2m-temperature")

        ecmwf.download_dataset(spec, progress_bar=False)

        # download_dataset returns None; api() return value is
        # consumed internally. Re-call api() to get the file path
        # for the read-back assertion (CDS will return the same
        # cached request quickly the second time).
        target = ecmwf.api(spec)
        assert target.exists()

        per_date = ecmwf.post_download(spec, target, progress_bar=False)
        assert len(per_date) == 1, (
            f"Daily post_download for one date should yield 1 array; "
            f"got {len(per_date)}"
        )
        _date, arr, _name_out = per_date[0]
        finite = arr[np.isfinite(arr)]
        assert finite.size > 0, "post_download array is entirely non-finite"
        assert -50.0 <= float(np.nanmean(finite)) <= 50.0, (
            f"2m_temperature in Celsius should fall in [-50, 50]; "
            f"got mean {float(np.nanmean(finite))}"
        )


class TestFacadeE2E:
    """End-to-end tests for the `Earth2Observe` facade."""

    def test_live_multi_variable_download_through_facade(self, tmp_path):
        """`Earth2Observe(...).download()` chains every stage end-to-end.

        Test scenario:
            Exercise C1+C3+H1+H2+H3+M3 together: facade dispatch,
            `self.vars` iteration, two retrieves (one per
            variable), no spurious `data_interim.nc` deletion,
            and partial-success aggregation. `2T` and `TP` are
            both single-level so the request shape is uniform but
            distinct dataset+variable pairs go to CDS.
        """
        e2o = Earth2Observe(
            data_source="ecmwf",
            temporal_resolution="daily",
            start="2022-01-01",
            end="2022-01-01",
            variables=["2m-temperature", "total-precipitation"],
            lat_lim=_BBOX_LAT,
            lon_lim=_BBOX_LON,
            path=str(tmp_path),
        )

        e2o.download(progress_bar=False)

        # Each variable gets its own
        # <cds_variable>_<cds_dataset>.nc under tmp_path.
        produced = sorted(p.name for p in tmp_path.glob("*.nc"))
        assert "2m_temperature_reanalysis-era5-single-levels.nc" in produced, (
            f"2T NetCDF missing from outputs: {produced}"
        )
        assert "total_precipitation_reanalysis-era5-single-levels.nc" in produced, (
            f"TP NetCDF missing from outputs: {produced}"
        )
