from __future__ import annotations

import glob
import os
import shutil
from typing import List

import pytest

from earthly.chirps import CHIRPS


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
    assert coello.lon_boundaries == [-180, 180]
    assert coello.lat_bondaries == [-50, 50]
    assert str(coello.dates[0].date()) == dates[0]
    assert str(coello.dates[-1].date()) == dates[1]

    return coello


@pytest.mark.e2e
def test_download(
    test_create_chirps_object: CHIRPS,
    chirps_base_dir: str,
    number_downloaded_files: int,
):
    fname = test_create_chirps_object.clipped_fname
    test_create_chirps_object.download()

    filelist = glob.glob(os.path.join(f"{chirps_base_dir}", f"{fname}*.tif"))
    assert len(filelist) == number_downloaded_files
    # delete the files
    try:
        shutil.rmtree(f"{chirps_base_dir}")
    except PermissionError:
        print("the downloaded files could not be deleted")
