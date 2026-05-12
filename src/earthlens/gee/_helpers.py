"""Module-scope helpers for the Google Earth Engine backend.

Small, side-effect-free utilities used by :mod:`earthlens.gee.backend`,
kept out of the backend module so there are no nested function
definitions and so they are independently testable.
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

# Approximate metres per degree of latitude at the equator. Used only
# to estimate a request's pixel dimensions for the size guard; it
# slightly overcounts longitude pixels away from the equator (which is
# the safe direction for a guard).
_METRES_PER_DEGREE: float = 111_320.0

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


def estimate_pixel_dims(space: SpatialExtent, scale_m: float) -> tuple[int, int]:
    """Estimate the (width, height) in pixels of a bbox sampled at `scale_m`.

    A rough estimate for the synchronous-download size guard: degrees are
    converted to metres with a constant equatorial factor, so the width
    is over-counted away from the equator — the safe direction for a
    guard.

    Args:
        space: The request bounding box (degrees).
        scale_m: The output pixel size in metres.

    Returns:
        ``(width_px, height_px)`` — both rounded up to the next integer,
        each at least 1.

    Raises:
        ValueError: If `scale_m` is not positive.

    Examples:
        - A 0.1° × 0.1° box at 90 m is tiny:
            ```python
            >>> from earthlens.base import SpatialExtent
            >>> box = SpatialExtent.from_pairs([30.0, 30.1], [31.0, 31.1])
            >>> estimate_pixel_dims(box, 90.0)
            (124, 124)

            ```
        - The same box at 10 m is ~9× larger per axis:
            ```python
            >>> from earthlens.base import SpatialExtent
            >>> box = SpatialExtent.from_pairs([30.0, 30.1], [31.0, 31.1])
            >>> estimate_pixel_dims(box, 10.0)
            (1114, 1114)

            ```
    """
    if scale_m <= 0:
        raise ValueError(f"scale_m must be positive, got {scale_m}")
    deg_per_px = scale_m / _METRES_PER_DEGREE
    width_px = math.ceil((space.longitude_max - space.longitude_min) / deg_per_px)
    height_px = math.ceil((space.latitude_max - space.latitude_min) / deg_per_px)
    return max(width_px, 1), max(height_px, 1)


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
