from __future__ import annotations

import glob
import os
import shutil
from collections.abc import Mapping
from typing import List
from unittest.mock import MagicMock

import cdsapi
import numpy as np
import pandas as pd
import pytest

from earthly.aggregate import AggregationConfig
from earthly.chirps import CHIRPS
from earthly.earthly import Earthly
from earthly.ecmwf import ECMWF
from earthly.s3 import S3


class _SentinelClient:
    """Stand-in for :class:`cdsapi.Client` used in facade tests."""


@pytest.mark.chirps
class TestChirpsBackend:
    @pytest.fixture(scope="module")
    def test_chirps_data_source_instantiate_object(
        self,
        chirps_data_source: str,
        dates: list,
        daily_temporal_resolution: str,
        chirps_variables: list[str],
        lat_bounds: list,
        lon_bounds: list,
        chirps_data_source_output_dir: str,
    ):
        earthly = Earthly(
            data_source=chirps_data_source,
            start=dates[0],
            end=dates[1],
            variables=chirps_variables,
            lat_lim=lat_bounds,
            lon_lim=lon_bounds,
            temporal_resolution=daily_temporal_resolution,
            path=chirps_data_source_output_dir,
        )
        assert isinstance(earthly.DataSources, Mapping)
        assert isinstance(earthly.datasource, CHIRPS)
        assert earthly.datasource.vars == chirps_variables
        assert isinstance(earthly.datasource.lat_lim, list)
        return earthly

    @pytest.mark.e2e
    def test_download_chirps_backend(
        self,
        test_chirps_data_source_instantiate_object: CHIRPS,
        chirps_data_source_output_dir: str,
        number_downloaded_files: int,
    ):
        test_chirps_data_source_instantiate_object.download()
        fname = "P_CHIRPS"
        filelist = glob.glob(
            os.path.join(f"{chirps_data_source_output_dir}", f"{fname}*.tif")
        )
        assert len(filelist) == number_downloaded_files
        # delete the files
        try:
            shutil.rmtree(f"{chirps_data_source_output_dir}")
        except PermissionError:
            print("the downloaded files could not be deleted")


@pytest.mark.s3
class TestS3Backend:

    @pytest.fixture(scope="module")
    def test_s3_data_source_instantiate_object(
        self,
        s3_data_source: str,
        monthly_dates: list,
        monthly_temporal_resolution: str,
        s3_era5_variables: list[str],
        lat_bounds: list,
        lon_bounds: list,
        s3_era5_data_source_output_dir: str,
    ):
        earthly = Earthly(
            data_source=s3_data_source,
            start=monthly_dates[0],
            end=monthly_dates[1],
            variables=s3_era5_variables,
            lat_lim=lat_bounds,
            lon_lim=lon_bounds,
            temporal_resolution=monthly_temporal_resolution,
            path=s3_era5_data_source_output_dir,
        )
        assert isinstance(earthly.DataSources, Mapping)
        assert isinstance(earthly.datasource, S3)
        assert earthly.datasource.vars == s3_era5_variables
        return earthly

    @pytest.mark.e2e
    def test_download_s3_backend(
        self,
        test_s3_data_source_instantiate_object: S3,
        s3_era5_data_source_output_dir: str,
        number_downloaded_files: int,
    ):
        test_s3_data_source_instantiate_object.download()
        filelist = glob.glob(os.path.join(f"{s3_era5_data_source_output_dir}", f"*.nc"))
        assert len(filelist) == number_downloaded_files
        # delete the files
        try:
            shutil.rmtree(f"{s3_era5_data_source_output_dir}")
        except PermissionError:
            print("the downloaded files could not be deleted")


@pytest.mark.ecmwf
class TestECMWFBackend:
    """Tests for the C1+L3 fix that registers ECMWF in the facade.

    Pre-C1, `Earthly(data_source="ecmwf", ...)` raised
    `ValueError: ecmwf not supported` because the `DataSources`
    mapping omitted ECMWF. These tests pin the registration so
    regressions show up immediately.
    """

    def test_ecmwf_is_registered_in_data_sources(self):
        """`Earthly.DataSources` maps `"ecmwf"` to :class:`ECMWF`."""
        assert "ecmwf" in Earthly.DataSources, (
            f"'ecmwf' missing from DataSources keys: " f"{sorted(Earthly.DataSources)}"
        )
        assert Earthly.DataSources["ecmwf"] is ECMWF, (
            f"DataSources['ecmwf'] should be the ECMWF class; got "
            f"{Earthly.DataSources['ecmwf']!r}"
        )

    def test_facade_accepts_ecmwf_data_source(self, tmp_path, monkeypatch):
        """`Earthly(data_source="ecmwf", ...)` no longer raises."""
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())

        earthly = Earthly(
            data_source="ecmwf",
            temporal_resolution="daily",
            start="2022-01-01",
            end="2022-01-01",
            variables={
                "reanalysis-era5-single-levels": ["2m-temperature"],
            },
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )

        assert isinstance(earthly.datasource, ECMWF), (
            f"datasource should be an ECMWF instance; got "
            f"{type(earthly.datasource).__name__}"
        )

    def test_unknown_data_source_still_raises(self, tmp_path):
        """Unknown `data_source` values still raise `ValueError`."""
        with pytest.raises(ValueError, match="not supported"):
            Earthly(
                data_source="not-a-real-source",
                start="2022-01-01",
                end="2022-01-01",
                variables=["2m-temperature"],
                lat_lim=[4.0, 5.0],
                lon_lim=[-75.0, -74.0],
                path=str(tmp_path),
            )

    def test_ecmwf_facade_propagates_constructor_arguments(self, tmp_path, monkeypatch):
        """The facade threads its constructor args into ECMWF unchanged."""
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())

        earthly = Earthly(
            data_source="ecmwf",
            temporal_resolution="monthly",
            start="2022-01-01",
            end="2022-02-01",
            variables={
                "reanalysis-era5-single-levels": [
                    "2m-temperature",
                    "total-precipitation",
                ],
            },
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )

        ecmwf = earthly.datasource
        assert ecmwf.vars == {
            "reanalysis-era5-single-levels": [
                "2m-temperature",
                "total-precipitation",
            ],
        }, f"variables should be threaded through; got {ecmwf.vars!r}"
        assert ecmwf.temporal_resolution == "monthly", (
            f"temporal_resolution should be 'monthly'; got "
            f"{ecmwf.temporal_resolution!r}"
        )
        assert (
            ecmwf.root_dir == tmp_path.resolve()
        ), f"root_dir should be the tmp path; got {ecmwf.root_dir}"

    def test_full_download_through_facade_routes_to_cdsapi(self, tmp_path, monkeypatch):
        """End-to-end: `Earthly(...).download()` reaches CDS.

            * Two cdsapi.Client.retrieve calls — one per variable
            * Each retrieve receives the right dataset name and
              `variable=[cds_variable]` from the catalog

            Per-date GeoTIFF post-processing is intentionally not
            part of the package; see
            `examples/post_process_ecmwf_netcdf.py`.
        """
        retrieved = []

        class FakeClient:
            def retrieve(self, dataset, request, target):
                retrieved.append((dataset, request, target))

        monkeypatch.setattr(cdsapi, "Client", FakeClient)

        earthly = Earthly(
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
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )
        earthly.download(progress_bar=False)

        assert len(retrieved) == 2, (
            f"Expected 2 retrieve calls (one per variable); " f"got {len(retrieved)}"
        )
        datasets = [args[0] for args in retrieved]
        variables = [args[1]["variable"] for args in retrieved]
        assert datasets == [
            "reanalysis-era5-single-levels",
            "reanalysis-era5-single-levels",
        ], f"datasets: {datasets!r}"
        assert variables == [
            ["2m_temperature"],
            ["total_precipitation"],
        ], f"variables: {variables!r}"


@pytest.mark.unit
class TestEarthlyDownloadAggregate:
    """Tests for the M3 `aggregate` pass-through on `Earthly.download`."""

    @pytest.fixture
    def stub_facade(self, tmp_path, monkeypatch):
        """Build an `Earthly` whose `.datasource` is a MagicMock.

        The facade is instantiated normally (with cdsapi.Client
        mocked) so its constructor logic runs unchanged; then
        `.datasource` is replaced with a MagicMock so we can inspect
        what `download()` forwards into the backend without
        exercising the real `ECMWF.download` body.

        Returns:
            Earthly: Facade ready for `download(...)` calls; its
            `datasource.download` is a `MagicMock` exposing
            `call_args`.
        """
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())

        earthly = Earthly(
            data_source="ecmwf",
            temporal_resolution="daily",
            start="2022-01-01",
            end="2022-01-01",
            variables={"reanalysis-era5-single-levels": ["2m-temperature"]},
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )
        earthly.datasource = MagicMock(name="stub_backend")
        return earthly

    def test_aggregate_none_does_not_reach_backend(self, stub_facade):
        """`aggregate=None` (default) leaves the backend kwargs untouched."""
        stub_facade.download(progress_bar=False)
        _, kwargs = stub_facade.datasource.download.call_args
        assert "aggregate" not in kwargs, (
            f"`aggregate` should not appear in backend kwargs when None; "
            f"got kwargs={kwargs!r}"
        )

    def test_aggregate_config_forwarded_to_backend(self, stub_facade):
        """`aggregate=cfg` reaches the backend's `download` as a kwarg."""
        cfg = AggregationConfig(freq="1MS", op="sum")
        stub_facade.download(progress_bar=False, aggregate=cfg)
        _, kwargs = stub_facade.datasource.download.call_args
        assert kwargs.get("aggregate") is cfg, (
            f"Expected backend to receive the same config instance; "
            f"got kwargs={kwargs!r}"
        )

    def test_progress_bar_still_forwarded_alongside_aggregate(self, stub_facade):
        """Adding `aggregate` does not displace `progress_bar` in the kwargs."""
        cfg = AggregationConfig(freq="1D")
        stub_facade.download(progress_bar=False, aggregate=cfg)
        _, kwargs = stub_facade.datasource.download.call_args
        assert kwargs.get("progress_bar") is False, (
            f"`progress_bar` should still be forwarded; got kwargs={kwargs!r}"
        )
        assert kwargs.get("aggregate") is cfg, (
            f"`aggregate` should be forwarded alongside; got kwargs={kwargs!r}"
        )

    def test_extra_kwargs_pass_through_unchanged(self, stub_facade):
        """Backend-specific kwargs (e.g. CHIRPS `cores=`) still pass through."""
        stub_facade.download(progress_bar=False, cores=4)
        _, kwargs = stub_facade.datasource.download.call_args
        assert kwargs.get("cores") == 4, (
            f"Passed-through kwargs should reach the backend verbatim; "
            f"got kwargs={kwargs!r}"
        )


@pytest.mark.unit
class TestTopLevelReExports:
    """Pin the top-level `earthly` package surface (L2)."""

    def test_earthly_facade_importable_from_package_root(self):
        """`from earthly import Earthly` resolves to the facade class."""
        import earthly

        assert earthly.Earthly is Earthly, (
            f"Top-level re-export should be the facade class; got "
            f"{earthly.Earthly!r}"
        )

    def test_aggregate_symbols_importable_from_package_root(self):
        """`AggregationConfig` and `aggregate_netcdf` resolve at top level."""
        import earthly

        assert earthly.AggregationConfig is AggregationConfig, (
            f"Top-level AggregationConfig drift: {earthly.AggregationConfig!r}"
        )
        assert callable(earthly.aggregate_netcdf), (
            f"Top-level aggregate_netcdf must be callable; got "
            f"{earthly.aggregate_netcdf!r}"
        )

    def test_all_lists_only_sdk_free_symbols(self):
        """`__all__` excludes the per-backend classes (each needs an extra)."""
        import earthly

        assert sorted(earthly.__all__) == [
            "AggregationConfig",
            "Earthly",
            "aggregate_netcdf",
        ], f"Unexpected top-level __all__: {earthly.__all__!r}"
