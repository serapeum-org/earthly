"""Module-scope helpers for the Google Earth Engine backend.

Small, side-effect-free utilities used by :mod:`earthlens.gee.backend`,
kept out of the backend module so there are no nested function
definitions and so they are independently testable.

Backend-agnostic spatial helpers (e.g. :func:`estimate_pixel_dims`)
live in :mod:`earthlens.base.spatial` so the CHC / ECMWF / S3 backends
can use them too.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Callable

from tqdm import tqdm

if TYPE_CHECKING:  # pragma: no cover - typing only
    from earthlens.base import SpatialExtent

# `getDownloadURL` / synchronous-export pixel-grid limit: each axis of
# the requested raster must be <= this many pixels (Earth Engine raises
# "Pixel grid dimensions (WxH) must be less than or equal to 32768.").
EE_MAX_DIMENSION: int = 32768

# Earth Engine `ee.ImageCollection` reducers exposed as convenience
# methods that preserve the original band names (unlike
# `ImageCollection.reduce(ee.Reducer.X())`, which appends `_X`).
_COLLECTION_REDUCERS: frozenset[str] = frozenset(
    {"mean", "median", "min", "max", "mode", "mosaic", "sum"}
)

# Terminal `ee.batch.Task` states (the bare, upper-cased name — `task.status()`
# may report e.g. ``"COMPLETED"`` or ``"State.COMPLETED"`` depending on SDK
# version, so callers normalise via :func:`task_state_name` before comparing).
TERMINAL_TASK_STATES: frozenset[str] = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED", "CANCEL_REQUESTED"}
)


def slug_asset_id(asset_id: str) -> str:
    """Turn an Earth Engine asset id into a filesystem-safe slug.

    Args:
        asset_id: An Earth Engine asset id, e.g. `"LANDSAT/LC09/C02/T1_L2"`.

    Returns:
        The id with `/` replaced by `_`, e.g. `"LANDSAT_LC09_C02_T1_L2"`.

    Examples:
        - Slugify a Landsat id:
            ```python
            >>> slug_asset_id("LANDSAT/LC09/C02/T1_L2")
            'LANDSAT_LC09_C02_T1_L2'

            ```
        - A flat id is returned unchanged:
            ```python
            >>> slug_asset_id("SRTMGL1_003")
            'SRTMGL1_003'

            ```
    """
    return asset_id.replace("/", "_")


def reduce_collection(collection, reducer: str):
    """Collapse an `ee.ImageCollection` to a single `ee.Image` by name.

    Dispatches to the matching convenience method on the collection
    (`mean`, `median`, `min`, `max`, `mode`, `mosaic`, `sum`) so the
    resulting image keeps the original band names.

    Args:
        collection: An `ee.ImageCollection`.
        reducer: The reducer name; one of `mean`, `median`, `min`,
            `max`, `mode`, `mosaic`, `sum`.

    Returns:
        The reduced `ee.Image`.

    Raises:
        ValueError: If `reducer` is not a supported name.
    """
    if reducer not in _COLLECTION_REDUCERS:
        raise ValueError(
            f"unsupported reducer {reducer!r}; expected one of "
            f"{sorted(_COLLECTION_REDUCERS)}"
        )
    return getattr(collection, reducer)()


def task_state_name(status: dict) -> str:
    """Return the bare, upper-cased state name from an `ee.batch.Task` status dict.

    `task.status()` reports the state as a plain string (`"RUNNING"`) on
    most SDK versions, but some report the enum repr (`"State.RUNNING"`);
    this normalises both to `"RUNNING"`.

    Args:
        status: The dict returned by `ee.batch.Task.status()`.

    Returns:
        The state name, e.g. `"RUNNING"`, `"COMPLETED"`, `"FAILED"`,
        or `""` if absent.

    Examples:
        - A plain-string state:
            ```python
            >>> task_state_name({"state": "RUNNING"})
            'RUNNING'

            ```
        - An enum-repr state:
            ```python
            >>> task_state_name({"state": "State.COMPLETED"})
            'COMPLETED'

            ```
    """
    return str(status.get("state", "")).rsplit(".", 1)[-1].upper()


def split_aoi_for_url(
    space: SpatialExtent,
    scale_m: float,
    max_dim: int = EE_MAX_DIMENSION,
) -> list[SpatialExtent]:
    """Tile a :class:`SpatialExtent` into sub-extents each within `max_dim` px per axis.

    Used by :meth:`GEE._export_via_url` to auto-split oversized
    synchronous downloads. The bbox is divided into an `Nx*Ny` grid in
    its own lon/lat coordinates — no UTM projection, no GeoDataFrame
    round-trip — sized so each tile satisfies
    `max(width_px, height_px) <= max_dim` at the given `scale_m`. Tiles
    are emitted row by row, south-to-north, west-to-east.

    Temporary inline implementation pending `PY-2` in
    `planning/gee-utils.md` (pyramids polygon-splitter). Once that
    lands, this can be deleted in favour of
    `pyramids.spatial.split_polygon`.

    Args:
        space: The full request extent (in WGS84 degrees).
        scale_m: Output pixel size in metres.
        max_dim: Per-axis pixel cap each tile must respect. Defaults to
            :data:`EE_MAX_DIMENSION`.

    Returns:
        A list of `SpatialExtent`s tiling `space`. If `space` already
        fits within `max_dim` per axis at `scale_m`, the list is
        `[space]`.

    Raises:
        ValueError: If `scale_m` is not positive or `max_dim < 1`.
    """
    if max_dim < 1:
        raise ValueError(f"max_dim must be >= 1, got {max_dim}")

    # Local import to avoid a runtime cycle (`earthlens.base` imports
    # this package's `spatial` helpers in a few branches).
    from earthlens.base import SpatialExtent as _SpatialExtent

    width_px, height_px = space.estimate_pixel_dims(scale_m)
    if max(width_px, height_px) <= max_dim:
        return [space]

    tiles_x = math.ceil(width_px / max_dim)
    tiles_y = math.ceil(height_px / max_dim)
    span_lon = space.east - space.west
    span_lat = space.north - space.south
    step_lon = span_lon / tiles_x
    step_lat = span_lat / tiles_y

    sub_extents: list[SpatialExtent] = []
    for j in range(tiles_y):
        south = space.south + j * step_lat
        north = space.south + (j + 1) * step_lat if j < tiles_y - 1 else space.north
        for i in range(tiles_x):
            west = space.west + i * step_lon
            east = space.west + (i + 1) * step_lon if i < tiles_x - 1 else space.east
            sub_extents.append(
                _SpatialExtent.from_pairs(
                    lat_lim=[south, north],
                    lon_lim=[west, east],
                    resolution=space.resolution,
                )
            )
    return sub_extents


def wait_for_task(
    task,
    *,
    poll_seconds: float = 15.0,
    progress_bar: bool = True,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Start an Earth Engine batch-export task and block until it finishes.

    Calls `task.start()`, then polls `task.status()` every `poll_seconds`
    (showing a `tqdm` spinner with the current state) until the task
    reaches a terminal state. A `COMPLETED` task returns its final
    status dict; any other terminal state raises.

    Args:
        task: An `ee.batch.Task` (e.g. from `ee.batch.Export.image.toDrive`).
        poll_seconds: Seconds between `task.status()` polls. Defaults to 15.
        progress_bar: Show a `tqdm` spinner. Defaults to `True`.
        sleep: Sleep function (injectable so tests run instantly).

    Returns:
        The final `task.status()` dict (state `"COMPLETED"`).

    Raises:
        RuntimeError: If the task ends `FAILED` / `CANCELLED` /
            `CANCEL_REQUESTED`; the message includes
            `status["error_message"]` when present.
    """
    task.start()
    spinner = tqdm(desc="EE export", unit="poll", disable=not progress_bar)
    status: dict = {}
    state = ""
    try:
        while True:
            status = task.status()
            state = task_state_name(status)
            spinner.set_postfix_str(state or "?")
            spinner.update(1)
            if state in TERMINAL_TASK_STATES:
                break
            sleep(poll_seconds)
    finally:
        spinner.close()
    if state != "COMPLETED":
        detail = status.get("error_message") or status.get("error", "")
        raise RuntimeError(
            f"Earth Engine export task ended {state or '<unknown>'}"
            + (f": {detail}" if detail else "")
        )
    return status
