"""Module-scope helpers for the Google Earth Engine backend.

Small, side-effect-free utilities used by :mod:`earthlens.gee.backend`,
kept out of the backend module so there are no nested function
definitions and so they are independently testable.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

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
