"""Tests for `earthlens.gee._helpers` — the GEE backend's module-scope utilities."""

from __future__ import annotations

import pytest

from earthlens.base import SpatialExtent
from earthlens.gee._helpers import (
    EE_MAX_DIMENSION,
    reduce_collection,
    slug_asset_id,
    split_aoi_for_url,
    task_state_name,
    wait_for_task,
)


class _FakeTask:
    """A stand-in for an `ee.batch.Task` (records `start()`, scripted `status()`)."""

    def __init__(self, states: list[str], *, error_message: str | None = None, error: str | None = None):
        self._states = list(states)
        self._error_message = error_message
        self._error = error
        self.started = False
        self.poll_count = 0

    def start(self):
        self.started = True

    def status(self) -> dict:
        self.poll_count += 1
        state = self._states[min(self.poll_count - 1, len(self._states) - 1)]
        out = {"state": state}
        if "FAILED" in state or "CANCEL" in state:
            if self._error_message is not None:
                out["error_message"] = self._error_message
            if self._error is not None:
                out["error"] = self._error
        return out


class _FakeCollection:
    """A stand-in for `ee.ImageCollection` exposing the reducer convenience methods."""

    def __init__(self):
        self.reduced_with: str | None = None

    def _reducer(self, name):
        self.reduced_with = name
        return f"image<{name}>"

    def mean(self):
        return self._reducer("mean")

    def median(self):
        return self._reducer("median")

    def mosaic(self):
        return self._reducer("mosaic")

    def sum(self):
        return self._reducer("sum")


class TestSlugAssetId:
    """Tests for `slug_asset_id`."""

    @pytest.mark.parametrize(
        "asset_id, expected",
        [
            ("LANDSAT/LC09/C02/T1_L2", "LANDSAT_LC09_C02_T1_L2"),
            ("USGS/SRTMGL1_003", "USGS_SRTMGL1_003"),
            ("FLAT_ID", "FLAT_ID"),
            ("", ""),
        ],
    )
    def test_slashes_become_underscores(self, asset_id, expected):
        """`/` is replaced with `_`; everything else is left as-is."""
        assert slug_asset_id(asset_id) == expected


class TestEeSizeGuard:
    """Cross-check that the bbox/scale sizing trips the GEE 32768-px cap when expected.

    The general `estimate_pixel_dims` tests live in
    `tests/test_base/test_spatial.py`; this class only verifies the
    EE-specific interplay between the bbox sizing and
    :data:`EE_MAX_DIMENSION`.
    """

    def test_oversized_box_exceeds_ee_limit(self):
        """A 40°×40° box at 30 m blows past `EE_MAX_DIMENSION` per axis."""
        box = SpatialExtent.from_pairs([0.0, 40.0], [0.0, 40.0])
        width_px, height_px = box.estimate_pixel_dims(30.0)
        assert max(width_px, height_px) > EE_MAX_DIMENSION


class TestReduceCollection:
    """Tests for `reduce_collection`."""

    @pytest.mark.parametrize("reducer", ["mean", "median", "mosaic", "sum"])
    def test_dispatches_to_named_method(self, reducer):
        """The named reducer maps to the matching collection method."""
        col = _FakeCollection()
        result = reduce_collection(col, reducer)
        assert col.reduced_with == reducer
        assert result == f"image<{reducer}>"

    def test_unknown_reducer_raises(self):
        """An unsupported reducer name raises `ValueError` listing the valid ones."""
        with pytest.raises(ValueError, match="unsupported reducer 'p95'"):
            reduce_collection(_FakeCollection(), "p95")


class TestTaskStateName:
    """Tests for `task_state_name`."""

    @pytest.mark.parametrize(
        "status, expected",
        [
            ({"state": "RUNNING"}, "RUNNING"),
            ({"state": "State.COMPLETED"}, "COMPLETED"),
            ({"state": "completed"}, "COMPLETED"),
            ({"state": "Operation.State.FAILED"}, "FAILED"),
            ({}, ""),
        ],
    )
    def test_normalises_state(self, status, expected):
        """The state is reduced to its bare, upper-cased name."""
        assert task_state_name(status) == expected


class TestWaitForTask:
    """Tests for `wait_for_task`."""

    def test_immediate_completion(self):
        """A task that is `COMPLETED` on the first poll returns immediately."""
        slept: list[float] = []
        task = _FakeTask(["COMPLETED"])
        result = wait_for_task(task, progress_bar=False, sleep=slept.append)
        assert task.started is True and task.poll_count == 1
        assert result == {"state": "COMPLETED"}
        assert slept == []

    def test_polls_until_terminal(self):
        """A `RUNNING` task is polled (with sleeps) until it `COMPLETED`s."""
        slept: list[float] = []
        task = _FakeTask(["READY", "RUNNING", "COMPLETED"])
        result = wait_for_task(task, poll_seconds=7.0, progress_bar=False, sleep=slept.append)
        assert task.poll_count == 3
        assert slept == [7.0, 7.0]
        assert result["state"] == "COMPLETED"

    def test_failed_task_raises_with_error_message(self):
        """A `FAILED` task raises `RuntimeError` including `error_message`."""
        task = _FakeTask(["RUNNING", "FAILED"], error_message="quota exceeded")
        with pytest.raises(RuntimeError, match="ended FAILED: quota exceeded"):
            wait_for_task(task, progress_bar=False, sleep=lambda s: None)

    def test_failed_task_uses_error_key_when_no_error_message(self):
        """When the status has `error` but not `error_message`, that is used."""
        task = _FakeTask(["FAILED"], error="boom")
        with pytest.raises(RuntimeError, match="ended FAILED: boom"):
            wait_for_task(task, progress_bar=False, sleep=lambda s: None)

    def test_cancelled_task_raises(self):
        """A `CANCELLED` task raises `RuntimeError` (no message needed)."""
        task = _FakeTask(["CANCELLED"])
        with pytest.raises(RuntimeError, match="ended CANCELLED"):
            wait_for_task(task, progress_bar=False, sleep=lambda s: None)

    def test_enum_repr_state_is_recognised_as_terminal(self):
        """An enum-repr terminal state (`"State.COMPLETED"`) ends the loop cleanly."""
        task = _FakeTask(["State.COMPLETED"])
        assert wait_for_task(task, progress_bar=False, sleep=lambda s: None) == {"state": "State.COMPLETED"}


class TestSplitAoiForUrl:
    """Tests for `split_aoi_for_url` (H2 — temp inline polygon-splitter)."""

    def test_small_aoi_returns_single_extent(self):
        """An AOI well under the per-axis cap is returned as a list of one."""
        space = SpatialExtent.from_pairs([29.9, 30.0], [31.2, 31.3])
        out = split_aoi_for_url(space, scale_m=30)
        assert out == [space]

    def test_oversized_aoi_is_tiled(self):
        """A 40°×40° box at 30 m → multiple tiles each within `EE_MAX_DIMENSION`."""
        space = SpatialExtent.from_pairs([0.0, 40.0], [0.0, 40.0])
        tiles = split_aoi_for_url(space, scale_m=30)
        assert len(tiles) > 1
        for tile in tiles:
            w, h = tile.estimate_pixel_dims(30)
            assert max(w, h) <= EE_MAX_DIMENSION

    def test_tile_count_matches_axis_ceilings(self):
        """The tile grid is `ceil(width/max_dim) * ceil(height/max_dim)`."""
        space = SpatialExtent.from_pairs([0.0, 40.0], [0.0, 40.0])
        w, h = space.estimate_pixel_dims(30)
        import math as _math
        expected = _math.ceil(w / EE_MAX_DIMENSION) * _math.ceil(h / EE_MAX_DIMENSION)
        assert len(split_aoi_for_url(space, scale_m=30)) == expected

    def test_tiles_cover_the_original_extent_with_aligned_edges(self):
        """Tile edges align (no gaps, no overlap) and span the full bbox."""
        space = SpatialExtent.from_pairs([0.0, 40.0], [0.0, 40.0])
        tiles = split_aoi_for_url(space, scale_m=30)

        souths = sorted({t.south for t in tiles})
        norths = sorted({t.north for t in tiles})
        wests = sorted({t.west for t in tiles})
        easts = sorted({t.east for t in tiles})

        assert souths[0] == space.south
        assert norths[-1] == space.north
        assert wests[0] == space.west
        assert easts[-1] == space.east
        assert souths[1:] == norths[:-1]
        assert wests[1:] == easts[:-1]

    def test_max_dim_below_one_raises(self):
        """`max_dim < 1` is rejected up-front."""
        space = SpatialExtent.from_pairs([0.0, 1.0], [0.0, 1.0])
        with pytest.raises(ValueError, match="max_dim must be >= 1"):
            split_aoi_for_url(space, scale_m=30, max_dim=0)

    def test_resolution_is_preserved_on_each_tile(self):
        """If `space.resolution` is set, every tile carries it through."""
        space = SpatialExtent.from_pairs([0.0, 40.0], [0.0, 40.0], resolution=0.5)
        tiles = split_aoi_for_url(space, scale_m=30)
        assert {t.resolution for t in tiles} == {0.5}
