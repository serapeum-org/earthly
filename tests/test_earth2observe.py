import glob
import os
import shutil
from typing import List

import cdsapi
import pytest

from earth2observe.chirps import CHIRPS
from earth2observe.earth2observe import Earth2Observe
from earth2observe.ecmwf import ECMWF
from earth2observe.s3 import S3


class _SentinelClient:
    """Stand-in for :class:`cdsapi.Client` used in facade tests."""


class TestChirpsBackend:
    @pytest.fixture(scope="module")
    def test_chirps_data_source_instantiate_object(
        self,
        chirps_data_source: str,
        dates: List,
        daily_temporal_resolution: str,
        chirps_variables: List[str],
        lat_bounds: List,
        lon_bounds: List,
        chirps_data_source_output_dir: str,
    ):
        e2o = Earth2Observe(
            data_source=chirps_data_source,
            start=dates[0],
            end=dates[1],
            variables=chirps_variables,
            lat_lim=lat_bounds,
            lon_lim=lon_bounds,
            temporal_resolution=daily_temporal_resolution,
            path=chirps_data_source_output_dir,
        )
        assert isinstance(e2o.DataSources, dict)
        assert isinstance(e2o.datasource, CHIRPS)
        assert e2o.datasource.vars == chirps_variables
        assert isinstance(e2o.datasource.lat_lim, list)
        return e2o

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


class TestS3Backend:
    @pytest.fixture(scope="module")
    def test_s3_data_source_instantiate_object(
        self,
        s3_data_source: str,
        monthly_dates: List,
        monthly_temporal_resolution: str,
        s3_era5_variables: List[str],
        lat_bounds: List,
        lon_bounds: List,
        s3_era5_data_source_output_dir: str,
    ):
        e2o = Earth2Observe(
            data_source=s3_data_source,
            start=monthly_dates[0],
            end=monthly_dates[1],
            variables=s3_era5_variables,
            lat_lim=lat_bounds,
            lon_lim=lon_bounds,
            temporal_resolution=monthly_temporal_resolution,
            path=s3_era5_data_source_output_dir,
        )
        assert isinstance(e2o.DataSources, dict)
        assert isinstance(e2o.datasource, S3)
        assert e2o.datasource.vars == s3_era5_variables
        return e2o

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


class TestECMWFBackend:
    """Tests for the C1+L3 fix that registers ECMWF in the facade.

    Pre-C1, ``Earth2Observe(data_source="ecmwf", ...)`` raised
    ``ValueError: ecmwf not supported`` because the ``DataSources``
    mapping omitted ECMWF. These tests pin the registration so
    regressions show up immediately.
    """

    def test_ecmwf_is_registered_in_data_sources(self):
        """``Earth2Observe.DataSources`` maps ``"ecmwf"`` to :class:`ECMWF`.

        Test scenario:
            The class-level ``DataSources`` dict must contain the key
            ``"ecmwf"`` whose value is the ``ECMWF`` class itself
            (not an instance).
        """
        assert "ecmwf" in Earth2Observe.DataSources, (
            f"'ecmwf' missing from DataSources keys: "
            f"{sorted(Earth2Observe.DataSources)}"
        )
        assert Earth2Observe.DataSources["ecmwf"] is ECMWF, (
            f"DataSources['ecmwf'] should be the ECMWF class; got "
            f"{Earth2Observe.DataSources['ecmwf']!r}"
        )

    def test_facade_accepts_ecmwf_data_source(self, tmp_path, monkeypatch):
        """``Earth2Observe(data_source="ecmwf", ...)`` no longer raises.

        Test scenario:
            With cdsapi.Client mocked, constructing the facade with
            ``data_source="ecmwf"`` must succeed and produce an
            :class:`ECMWF` backend bound to ``e2o.datasource``.
        """
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())

        e2o = Earth2Observe(
            data_source="ecmwf",
            temporal_resolution="daily",
            start="2022-01-01",
            end="2022-01-01",
            variables=["2T"],
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )

        assert isinstance(e2o.datasource, ECMWF), (
            f"datasource should be an ECMWF instance; got "
            f"{type(e2o.datasource).__name__}"
        )

    def test_unknown_data_source_still_raises(self, tmp_path):
        """Unknown ``data_source`` values still raise ``ValueError``.

        Test scenario:
            Adding ECMWF to the registry must not weaken the rejection
            of unrecognised data-source names.
        """
        with pytest.raises(ValueError, match="not supported"):
            Earth2Observe(
                data_source="not-a-real-source",
                start="2022-01-01",
                end="2022-01-01",
                variables=["2T"],
                lat_lim=[4.0, 5.0],
                lon_lim=[-75.0, -74.0],
                path=str(tmp_path),
            )

    def test_ecmwf_facade_propagates_constructor_arguments(
        self, tmp_path, monkeypatch
    ):
        """The facade threads its constructor args into ECMWF unchanged.

        Test scenario:
            ``variables``, ``lat_lim``/``lon_lim``, ``temporal_resolution``
            and ``path`` passed to ``Earth2Observe`` must reach the
            underlying :class:`ECMWF` backend, since downstream code
            (e.g. :meth:`ECMWF.api`) reads them from ``self.vars`` /
            ``self.space`` / ``self.time`` / ``self.root_dir``.
        """
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())

        e2o = Earth2Observe(
            data_source="ecmwf",
            temporal_resolution="monthly",
            start="2022-01-01",
            end="2022-02-01",
            variables=["2T", "TP"],
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )

        ecmwf = e2o.datasource
        assert ecmwf.vars == ["2T", "TP"], (
            f"variables should be threaded through; got {ecmwf.vars!r}"
        )
        assert ecmwf.temporal_resolution == "monthly", (
            f"temporal_resolution should be 'monthly'; got "
            f"{ecmwf.temporal_resolution!r}"
        )
        assert ecmwf.root_dir == tmp_path.resolve(), (
            f"root_dir should be the tmp path; got {ecmwf.root_dir}"
        )
