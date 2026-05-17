"""UTM zone / EPSG helpers for WGS84 geometries.

Maps a (longitude, latitude) point — or the centroid of a polygon —
to its UTM zone and the corresponding EPSG code, and reprojects a
WGS84 `GeoDataFrame` to its UTM CRS. Pure pyproj + GeoPandas + Shapely.

The standard UTM zone formula is `floor((lon + 180) / 6) + 1`, with
two well-known exceptions that this module honours:

* **Norway** (56°-64° N, 3°-12° E): zone 32 is extended westward over
  what would otherwise be the eastern half of zone 31.
* **Svalbard** (72°-84° N): zones 31, 33, 35, 37 cover the whole
  Norway-Svalbard arc; zones 32, 34, 36 are skipped.

EPSG codes follow the WGS84/UTM convention: `32600 + zone` north of
the equator, `32700 + zone` south.

Public surface:

* :func:`utm_zone` — `(lon, lat) -> int`
* :func:`utm_epsg` — `(lon, lat) -> int` (EPSG code, north/south aware)
* :func:`utm_epsg_for_polygon` — `GeoDataFrame -> int` (uses bbox centroid)
* :func:`project_to_utm` — `GeoDataFrame -> (projected_gdf, epsg)`

Examples:
    - Zone + EPSG for a point in Cairo (north of the equator):
        ```python
        >>> from earthlens.utm import utm_zone, utm_epsg
        >>> utm_zone(31.25, 30.05)
        36
        >>> utm_epsg(31.25, 30.05)
        32636

        ```
    - The same longitude south of the equator picks the southern EPSG:
        ```python
        >>> from earthlens.utm import utm_epsg
        >>> utm_epsg(31.25, -25.0)
        32736

        ```
    - Norway exception (Bergen, lon=5°, lat=60°): zone 32, not 31:
        ```python
        >>> from earthlens.utm import utm_zone
        >>> utm_zone(5.0, 60.0)
        32

        ```
"""

from __future__ import annotations

import math

from geopandas import GeoDataFrame

_WGS84_CRS: str = "EPSG:4326"


def utm_zone(lon: float, lat: float) -> int:
    """Return the UTM zone number for a WGS84 `(lon, lat)` point.

    Implements the standard `floor((lon + 180) / 6) + 1` rule, with
    the Norway (56°-64° N, 3°-12° E) and Svalbard (72°-84° N)
    exceptions baked in. Longitudes are not pre-normalised — pass
    values in `[-180, 180]`.

    Args:
        lon: Longitude in degrees, `[-180, 180]`.
        lat: Latitude in degrees, `[-90, 90]`.

    Returns:
        UTM zone number, `[1, 60]`.

    Examples:
        - The standard formula in the middle of zone 33:
            ```python
            >>> from earthlens.utm import utm_zone
            >>> utm_zone(15.0, 0.0)
            33

            ```
        - Norway exception (Bergen, lon=5°, lat=60°):
            ```python
            >>> from earthlens.utm import utm_zone
            >>> utm_zone(5.0, 60.0)
            32

            ```
        - Svalbard exception (Longyearbyen, lon=15°, lat=78°):
            ```python
            >>> from earthlens.utm import utm_zone
            >>> utm_zone(15.0, 78.0)
            33

            ```
    """
    if 56.0 <= lat < 64.0 and 3.0 <= lon < 12.0:
        return 32
    if 72.0 <= lat < 84.0:
        if 0.0 <= lon < 9.0:
            return 31
        if 9.0 <= lon < 21.0:
            return 33
        if 21.0 <= lon < 33.0:
            return 35
        if 33.0 <= lon < 42.0:
            return 37
    return int(math.floor((lon + 180.0) / 6.0) + 1)


def utm_epsg(lon: float, lat: float) -> int:
    """Return the WGS84/UTM EPSG code for a `(lon, lat)` point.

    Combines :func:`utm_zone` with the standard EPSG offsets:
    `32600 + zone` north of the equator, `32700 + zone` south.

    Args:
        lon: Longitude in degrees.
        lat: Latitude in degrees; `lat >= 0` selects the northern EPSG
            offset, `lat < 0` the southern.

    Returns:
        The EPSG code, e.g. `32636` for `(31.25, 30.05)` (Cairo) or
        `32736` for `(31.25, -25.0)` (south of equator, same zone).

    Examples:
        - Cairo, north of equator, zone 36:
            ```python
            >>> from earthlens.utm import utm_epsg
            >>> utm_epsg(31.25, 30.05)
            32636

            ```
        - Same longitude south of the equator picks 32700 + zone:
            ```python
            >>> from earthlens.utm import utm_epsg
            >>> utm_epsg(31.25, -25.0)
            32736

            ```
    """
    zone = utm_zone(lon, lat)
    base = 32600 if lat >= 0 else 32700
    return base + zone


def utm_epsg_for_polygon(gdf: GeoDataFrame) -> int:
    """Return the UTM EPSG code for the centroid of a `GeoDataFrame`'s bbox.

    The input is expected to be in WGS84 (`EPSG:4326`); a non-4326
    input is first reprojected to 4326 so the centroid is in degrees.
    An empty `GeoDataFrame` raises `ValueError`.

    Args:
        gdf: A `GeoDataFrame` with at least one geometry.

    Returns:
        The EPSG code of the UTM zone covering the bbox centroid.

    Raises:
        ValueError: If `gdf` is empty.

    Examples:
        - Pick the UTM EPSG for a small bbox over Cairo:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.utm import utm_epsg_for_polygon
            >>> poly = Polygon([(31.0, 30.0), (31.2, 30.0), (31.2, 30.2), (31.0, 30.2)])
            >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
            >>> utm_epsg_for_polygon(gdf)
            32636

            ```
        - A polygon in EPSG:3857 is reprojected to 4326 first:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.utm import utm_epsg_for_polygon
            >>> poly = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
            >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:3857")
            >>> utm_epsg_for_polygon(gdf)
            32631

            ```
    """
    if gdf.empty:
        raise ValueError("utm_epsg_for_polygon: GeoDataFrame is empty")
    if str(gdf.crs).upper() != _WGS84_CRS:
        gdf = gdf.to_crs(_WGS84_CRS)
    xmin, ymin, xmax, ymax = gdf.total_bounds
    return utm_epsg((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)


def project_to_utm(gdf: GeoDataFrame) -> tuple[GeoDataFrame, int]:
    """Project a `GeoDataFrame` to the UTM CRS of its bbox centroid.

    Equivalent to `gdf.to_crs(utm_epsg_for_polygon(gdf))`, returned
    together with the chosen EPSG so the caller can stamp / log it
    without recomputing.

    Args:
        gdf: A `GeoDataFrame` with at least one geometry.

    Returns:
        `(projected_gdf, utm_epsg)` — the reprojected `GeoDataFrame`
        and the EPSG code it was projected to.

    Raises:
        ValueError: If `gdf` is empty (propagated from
            :func:`utm_epsg_for_polygon`).

    Examples:
        - Project a Cairo bbox to UTM zone 36N:
            ```python
            >>> import geopandas as gpd
            >>> from shapely.geometry import Polygon
            >>> from earthlens.utm import project_to_utm
            >>> poly = Polygon([(31.0, 30.0), (31.2, 30.0), (31.2, 30.2), (31.0, 30.2)])
            >>> gdf = gpd.GeoDataFrame({"id": [1]}, geometry=[poly], crs="EPSG:4326")
            >>> projected, epsg = project_to_utm(gdf)
            >>> epsg
            32636
            >>> str(projected.crs).upper()
            'EPSG:32636'

            ```
    """
    epsg = utm_epsg_for_polygon(gdf)
    return gdf.to_crs(epsg), epsg
