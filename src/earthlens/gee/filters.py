"""Small `ee.ImageCollection` filter helpers.

Thin wrappers around `ee.ImageCollection.filter*` so the call site
reads as intent rather than as an Earth Engine filter expression.
Each helper is a one-liner; the value is the named API surface plus
a single place to add tests / docstrings / examples.

Ported (and renamed for the in-`filters` namespace) from
`gee_utils.raster.filter_*`. The `filter_` prefix is dropped: under
`earthlens.gee.filters` the call sites read `filters.by_year(...)`,
`filters.by_bounds(...)`, etc., which is clearer than
`filters.filter_year(...)`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import ee


def by_year(collection: ee.ImageCollection, year: int) -> ee.ImageCollection:
    """Filter an `ee.ImageCollection` to a calendar year.

    Equivalent to `collection.filterDate(f"{year}-01-01",
    f"{year + 1}-01-01")` — the upper bound is exclusive (Earth Engine
    convention).

    Args:
        collection: The Earth Engine `ImageCollection`.
        year: Four-digit year, e.g. `2024`.

    Returns:
        A new `ImageCollection` containing images with
        `system:time_start` inside `[year-01-01, year+1-01-01)`.
    """
    return collection.filterDate(f"{year}-01-01", f"{year + 1}-01-01")


def by_bounds(
    collection: ee.ImageCollection, region: ee.Geometry | ee.FeatureCollection,
) -> ee.ImageCollection:
    """Filter an `ee.ImageCollection` to images intersecting `region`.

    Thin alias around `collection.filterBounds(region)` — kept as a
    standalone helper so it sits next to the other filters and so the
    intent ("filter by spatial bounds") is named at the call site.

    Args:
        collection: The Earth Engine `ImageCollection`.
        region: An `ee.Geometry` or `ee.FeatureCollection` defining the
            spatial filter.

    Returns:
        A new `ImageCollection` with the bounds filter applied.
    """
    return collection.filterBounds(region)


def by_property_in(
    collection: ee.ImageCollection, property_name: str, values: Iterable[Any],
) -> ee.ImageCollection:
    """Filter an `ee.ImageCollection` to images whose `property_name` is in `values`.

    Equivalent to `collection.filter(ee.Filter.inList(property_name,
    list(values)))`. Useful e.g. for restricting to certain
    `WRS_PATH` / `WRS_ROW` combinations or specific scene IDs.

    Args:
        collection: The Earth Engine `ImageCollection`.
        property_name: The metadata property name to match.
        values: Iterable of acceptable values; converted to a list for
            `ee.Filter.inList`.

    Returns:
        A new `ImageCollection` with the property filter applied.
    """
    return collection.filter(ee.Filter.inList(property_name, list(values)))


def by_cloud_cover_lte(
    collection: ee.ImageCollection,
    max_pct: float,
    *,
    property_name: str = "CLOUD_COVER",
) -> ee.ImageCollection:
    """Filter to images whose cloud-cover metadata is `<= max_pct`.

    Wraps `collection.filter(ee.Filter.lte(property_name, max_pct))`.
    The default `property_name` matches Landsat C2 (`CLOUD_COVER`);
    pass `property_name="CLOUDY_PIXEL_PERCENTAGE"` for Sentinel-2.

    Args:
        collection: The Earth Engine `ImageCollection`.
        max_pct: Maximum allowed cloud-cover percentage (0-100).
        property_name: The metadata property carrying the cloud
            percentage. Defaults to `"CLOUD_COVER"`.

    Returns:
        A new `ImageCollection` with the cloud-cover filter applied.

    Raises:
        ValueError: If `max_pct` is outside `[0, 100]`.
    """
    if not 0 <= max_pct <= 100:
        raise ValueError(f"max_pct must be in [0, 100], got {max_pct!r}")
    return collection.filter(ee.Filter.lte(property_name, max_pct))


def by_year_and_bounds(
    collection: ee.ImageCollection,
    year: int,
    region: ee.Geometry | ee.FeatureCollection | None = None,
) -> ee.ImageCollection:
    """Compose `by_year` and `by_bounds`.

    Equivalent to `by_bounds(by_year(collection, year), region)` when
    `region` is given, else just `by_year(collection, year)`.

    Args:
        collection: The Earth Engine `ImageCollection`.
        year: Four-digit calendar year.
        region: Optional spatial filter. Defaults to `None` (year only).

    Returns:
        A new `ImageCollection` with both filters applied (or just the
        year filter when `region is None`).
    """
    out = by_year(collection, year)
    if region is not None:
        out = by_bounds(out, region)
    return out
