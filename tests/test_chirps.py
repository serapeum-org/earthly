from __future__ import annotations

import glob
import os
import shutil

import pytest

from earthlens.chirps import CHIRPS

pytestmark = [pytest.mark.chirps]


@pytest.fixture(scope="module")
def test_create_chirps_object(
    dates: list,
    daily_temporal_resolution: str,
    chirps_variables: list[str],
    lat_bounds: list,
    lon_bounds: list,
    chirps_base_dir: str,
):
    coello = CHIRPS(
        start=dates[0],
        end=dates[1],
        lat_lim=lat_bounds,
        lon_lim=lon_bounds,
        variables=chirps_variables,
        temporal_resolution=daily_temporal_resolution,
        path=chirps_base_dir,
    )
    assert coello.api_url == "data.chc.ucsb.edu"
    # Legacy list-shape `variables` is normalized to the catalog dict shape.
    assert coello.vars == {"global-daily": ["precipitation"]}
    # `self.time` carries the outer window; per-dataset frequencies live
    # in the catalog (`Dataset.pandas_freq`) and are resolved per call.
    assert str(coello.time.start_date.date()) == dates[0]
    assert str(coello.time.end_date.date()) == dates[1]

    return coello


@pytest.mark.e2e
def test_download(
    test_create_chirps_object: CHIRPS,
    chirps_base_dir: str,
    number_downloaded_files: int,
):
    test_create_chirps_object.download()

    # New filename scheme is `<dataset-key>_<variable>_<date>.tif`.
    filelist = glob.glob(
        os.path.join(f"{chirps_base_dir}", "global-daily_precipitation_*.tif")
    )
    assert len(filelist) == number_downloaded_files
    # delete the files
    try:
        shutil.rmtree(f"{chirps_base_dir}")
    except PermissionError:
        print("the downloaded files could not be deleted")
