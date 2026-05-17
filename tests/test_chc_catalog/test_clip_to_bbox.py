"""Lock-in for M2: `_clip_to_bbox` raises when the requested bbox doesn't overlap the raster."""

from __future__ import annotations

import numpy as np
import pytest

from earthlens.chc.backend import CHIRPS

pytestmark = [pytest.mark.chc]


def _chirps_with_bbox(lat_lim: list[float], lon_lim: list[float]) -> CHIRPS:
    """Build a minimal CHIRPS backend pinned to a known bbox + date.

    Uses CHIRPS-2.0 global-daily so the legacy list-shape `variables`
    path is exercised; the date range is one day to keep the constructor
    fast.
    """
    return CHIRPS(
        variables=["precipitation"],
        temporal_resolution="daily",
        start="2020-01-01",
        end="2020-01-01",
        lat_lim=lat_lim,
        lon_lim=lon_lim,
    )


# A small fake raster covering lon [-180, 180] x lat [-50, 50] at 1-degree
# pixels: 100 rows x 360 cols. The geo-affine origin sits at (-180, 50)
# (top-left), pixel size 1 in both directions, no rotation.
_FAKE_GEO: list[float] = [-180.0, 1.0, 0.0, 50.0, 0.0, -1.0]


class TestClipToBboxOverlap:
    """`_clip_to_bbox` refuses non-overlapping bboxes (M2)."""

    def test_overlapping_bbox_returns_a_non_empty_slice(self):
        """A normal bbox inside the raster extent returns a populated slice."""
        chirps = _chirps_with_bbox(lat_lim=[0.0, 10.0], lon_lim=[0.0, 10.0])
        data = np.zeros((100, 360), dtype=np.float32)
        clipped, new_geo = chirps._clip_to_bbox(data, _FAKE_GEO)
        assert clipped.shape == (10, 10)
        assert new_geo[0] == 0.0  # origin_x shifted to lon=0
        assert new_geo[3] == 10.0  # origin_y shifted to lat=10 (top edge)

    def test_bbox_entirely_north_of_raster_raises(self):
        """A bbox north of the raster's extent raises ValueError naming both extents."""
        chirps = _chirps_with_bbox(lat_lim=[60.0, 70.0], lon_lim=[0.0, 10.0])
        data = np.zeros((100, 360), dtype=np.float32)
        with pytest.raises(ValueError, match=r"does not overlap") as exc:
            chirps._clip_to_bbox(data, _FAKE_GEO)
        message = str(exc.value)
        assert "60.0" in message and "70.0" in message
        assert "raster" in message.lower()

    def test_bbox_entirely_south_of_raster_raises(self):
        """A bbox south of the raster's extent raises ValueError."""
        chirps = _chirps_with_bbox(lat_lim=[-80.0, -70.0], lon_lim=[0.0, 10.0])
        data = np.zeros((100, 360), dtype=np.float32)
        with pytest.raises(ValueError, match=r"does not overlap"):
            chirps._clip_to_bbox(data, _FAKE_GEO)

    def test_bbox_east_of_narrow_raster_raises(self):
        """A bbox east of a *narrow* raster (lon [-180, -100]) raises ValueError."""
        # SpatialExtent enforces user lon <= 180, so we can't put the bbox
        # east of a global raster — instead, make the raster narrow.
        chirps = _chirps_with_bbox(lat_lim=[0.0, 10.0], lon_lim=[0.0, 10.0])
        narrow_geo = [-180.0, 1.0, 0.0, 50.0, 0.0, -1.0]
        data = np.zeros((100, 80), dtype=np.float32)  # cols=80 -> east edge at lon=-100
        with pytest.raises(ValueError, match=r"does not overlap"):
            chirps._clip_to_bbox(data, narrow_geo)

    def test_message_names_both_bbox_and_raster_extent(self):
        """The error message must surface the user bbox AND the raster extent."""
        chirps = _chirps_with_bbox(lat_lim=[60.0, 70.0], lon_lim=[0.0, 10.0])
        data = np.zeros((100, 360), dtype=np.float32)
        with pytest.raises(ValueError) as exc:
            chirps._clip_to_bbox(data, _FAKE_GEO)
        message = str(exc.value)
        # User bbox
        assert "60.0" in message and "70.0" in message
        # Raster extent (top-edge at 50, bottom at -50, west -180, east 180)
        assert "50" in message and "-50" in message
        assert "-180" in message
