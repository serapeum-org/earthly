"""Backend-agnostic spatial helpers.

Small pure-Python utilities that act on geographic bounding boxes and
are useful across every concrete data-source backend (GEE, ECMWF, CHC,
S3, ...). Kept here rather than in any one backend so a new backend
doesn't have to reach into `gee/_helpers.py` for them. Eventual home
is the sibling pyramids GIS package — keep the free-function shape
that's already pyramids-compatible.
"""

from __future__ import annotations

import math

#: Approximate metres per degree of latitude at the equator. Used by
#: :func:`estimate_pixel_dims` for fast pre-flight pixel-grid sizing;
#: slightly over-counts longitude pixels away from the equator (which
#: is the safe direction for a size guard).
METRES_PER_DEGREE: float = 111_320.0


def estimate_pixel_dims(
    west: float,
    south: float,
    east: float,
    north: float,
    scale_m: float,
) -> tuple[int, int]:
    """Estimate the (width, height) in pixels of a WGS84 bbox at `scale_m`.

    A rough estimate suitable for pre-flight size guards on raster
    downloads. Degrees are converted to metres with the equatorial
    constant :data:`METRES_PER_DEGREE`, so the width is over-counted
    away from the equator — the safe direction for a guard. For an
    exact geodesic computation use pyproj's `Geod.inv` instead.

    Args:
        west: Western edge of the bbox in degrees longitude.
        south: Southern edge of the bbox in degrees latitude.
        east: Eastern edge of the bbox in degrees longitude.
        north: Northern edge of the bbox in degrees latitude.
        scale_m: Output pixel size in metres.

    Returns:
        `(width_px, height_px)` — both rounded up to the next integer,
        each at least 1.

    Raises:
        ValueError: If `scale_m` is not positive or `east < west` /
            `north < south`.

    Examples:
        - A 0.1° × 0.1° box at 90 m is tiny:
            ```python
            >>> estimate_pixel_dims(31.0, 30.0, 31.1, 30.1, 90.0)
            (124, 124)

            ```
        - The same box at 10 m is ~9× larger per axis:
            ```python
            >>> estimate_pixel_dims(31.0, 30.0, 31.1, 30.1, 10.0)
            (1114, 1114)

            ```
    """
    if scale_m <= 0:
        raise ValueError(f"scale_m must be positive, got {scale_m}")
    if east < west:
        raise ValueError(f"east ({east}) < west ({west})")
    if north < south:
        raise ValueError(f"north ({north}) < south ({south})")
    deg_per_px = scale_m / METRES_PER_DEGREE
    width_px = math.ceil((east - west) / deg_per_px)
    height_px = math.ceil((north - south) / deg_per_px)
    return max(width_px, 1), max(height_px, 1)
