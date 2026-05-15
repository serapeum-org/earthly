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

from earthlens.aggregate import AggregationConfig
from earthlens.chirps import CHIRPS
from earthlens.earthlens import EarthLens
from earthlens.ecmwf import ECMWF
from earthlens.s3 import S3


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
        earthlens = EarthLens(
            data_source=chirps_data_source,
            start=dates[0],
            end=dates[1],
            variables=chirps_variables,
            lat_lim=lat_bounds,
            lon_lim=lon_bounds,
            temporal_resolution=daily_temporal_resolution,
            path=chirps_data_source_output_dir,
        )
        assert isinstance(earthlens.DataSources, Mapping)
        assert isinstance(earthlens.datasource, CHIRPS)
        # Legacy list-shape `variables` is normalized to the catalog
        # dict shape (mirroring ECMWF). The dataset key is derived
        # from `temporal_resolution`: "daily" → "global-daily".
        assert earthlens.datasource.vars == {"global-daily": chirps_variables}
        return earthlens

    @pytest.mark.e2e
    def test_download_chirps_backend(
        self,
        test_chirps_data_source_instantiate_object: CHIRPS,
        chirps_data_source_output_dir: str,
        number_downloaded_files: int,
    ):
        test_chirps_data_source_instantiate_object.download()
        # Filename scheme is `<dataset-key>_<variable>_<date>.tif`.
        filelist = glob.glob(
            os.path.join(
                f"{chirps_data_source_output_dir}",
                "global-daily_precipitation_*.tif",
            )
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
        earthlens = EarthLens(
            data_source=s3_data_source,
            start=monthly_dates[0],
            end=monthly_dates[1],
            variables=s3_era5_variables,
            lat_lim=lat_bounds,
            lon_lim=lon_bounds,
            temporal_resolution=monthly_temporal_resolution,
            path=s3_era5_data_source_output_dir,
        )
        assert isinstance(earthlens.DataSources, Mapping)
        assert isinstance(earthlens.datasource, S3)
        assert earthlens.datasource.vars == s3_era5_variables
        return earthlens

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

    Pre-C1, `EarthLens(data_source="ecmwf", ...)` raised
    `ValueError: ecmwf not supported` because the `DataSources`
    mapping omitted ECMWF. These tests pin the registration so
    regressions show up immediately.
    """

    def test_ecmwf_is_registered_in_data_sources(self):
        """`EarthLens.DataSources` maps `"ecmwf"` to :class:`ECMWF`."""
        assert "ecmwf" in EarthLens.DataSources, (
            f"'ecmwf' missing from DataSources keys: " f"{sorted(EarthLens.DataSources)}"
        )
        assert EarthLens.DataSources["ecmwf"] is ECMWF, (
            f"DataSources['ecmwf'] should be the ECMWF class; got "
            f"{EarthLens.DataSources['ecmwf']!r}"
        )

    def test_facade_accepts_ecmwf_data_source(self, tmp_path, monkeypatch):
        """`EarthLens(data_source="ecmwf", ...)` no longer raises."""
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())

        earthlens = EarthLens(
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

        assert isinstance(earthlens.datasource, ECMWF), (
            f"datasource should be an ECMWF instance; got "
            f"{type(earthlens.datasource).__name__}"
        )

    def test_unknown_data_source_still_raises(self, tmp_path):
        """Unknown `data_source` values still raise `ValueError`."""
        with pytest.raises(ValueError, match="not supported"):
            EarthLens(
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

        earthlens = EarthLens(
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

        ecmwf = earthlens.datasource
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
        """End-to-end: `EarthLens(...).download()` reaches CDS.

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
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )
        earthlens.download(progress_bar=False)

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
class TestEarthLensDownloadAggregate:
    """Tests for the M3 `aggregate` pass-through on `EarthLens.download`."""

    @pytest.fixture
    def stub_facade(self, tmp_path, monkeypatch):
        """Build an `EarthLens` whose `.datasource` is a MagicMock.

        The facade is instantiated normally (with cdsapi.Client
        mocked) so its constructor logic runs unchanged; then
        `.datasource` is replaced with a MagicMock so we can inspect
        what `download()` forwards into the backend without
        exercising the real `ECMWF.download` body.

        Returns:
            EarthLens: Facade ready for `download(...)` calls; its
            `datasource.download` is a `MagicMock` exposing
            `call_args`.
        """
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())

        earthlens = EarthLens(
            data_source="ecmwf",
            temporal_resolution="daily",
            start="2022-01-01",
            end="2022-01-01",
            variables={"reanalysis-era5-single-levels": ["2m-temperature"]},
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )
        earthlens.datasource = MagicMock(name="stub_backend")
        return earthlens

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
    """Pin the top-level `earthlens` package surface (L2)."""

    def test_earthlens_facade_importable_from_package_root(self):
        """`from earthlens import EarthLens` resolves to the facade class."""
        import earthlens

        assert earthlens.EarthLens is EarthLens, (
            f"Top-level re-export should be the facade class; got "
            f"{earthlens.EarthLens!r}"
        )

    def test_aggregate_symbols_importable_from_package_root(self):
        """`AggregationConfig` and `aggregate_netcdf` resolve at top level."""
        import earthlens

        assert earthlens.AggregationConfig is AggregationConfig, (
            f"Top-level AggregationConfig drift: {earthlens.AggregationConfig!r}"
        )
        assert callable(earthlens.aggregate_netcdf), (
            f"Top-level aggregate_netcdf must be callable; got "
            f"{earthlens.aggregate_netcdf!r}"
        )

    def test_all_lists_only_sdk_free_symbols(self):
        """`__all__` excludes the per-backend classes (each needs an extra)."""
        import earthlens

        assert sorted(earthlens.__all__) == [
            "AggregationConfig",
            "EarthLens",
            "aggregate_netcdf",
        ], f"Unexpected top-level __all__: {earthlens.__all__!r}"
