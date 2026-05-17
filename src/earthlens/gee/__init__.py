"""Google Earth Engine backend.

Downloads imagery from Google Earth Engine. A request is a mapping of
`{asset_id: [band, ...], ...}` (the addressable units of an Earth Engine
dataset are *bands*, and one image carries many at once), plus a date
range, a bbox (or a `GeoDataFrame` region), a temporal-compositing
resolution (`"raw"` / `"daily"` / `"monthly"` / `"yearly"`), and an
output pixel `scale` in metres. Asset ids and band metadata are resolved
through :class:`Catalog`, which loads the per-category YAMLs under
`src/earthlens/gee/catalog/` (the GEE analogue of the ECMWF
`cds_data_catalog.yaml`, but shaped by Earth Engine's own data model —
see that directory's `_index.yaml` header).

Public surface (re-exported from this package):

* :class:`GEE` — the backend; instantiate with a date range, a bbox, a
  `{asset_id: [band, ...]}` request, an output `scale`, and credentials
  (`service_account` + `service_key`, or a registered `project`), then
  call :meth:`GEE.download`.
* :class:`AuthenticationError` — raised when Earth Engine cannot be
  initialised (missing/invalid key, unregistered project, missing IAM role).
* :class:`Catalog` — pydantic-backed loader for the bundled per-category
  catalog under `src/earthlens/gee/catalog/`, exposing
  `available_datasets`, `datasets`, and
  `get_dataset` / `get_band` / `get_variable`.
* :class:`Dataset` / :class:`Band` / :class:`Cadence` / :class:`Extent`
  — the frozen value objects the catalog is built from.
* :data:`CATALOG_PATH` — absolute path to the bundled catalog directory;
  monkey-patchable to redirect the loader at a temp directory or single
  YAML file.
* :class:`EarthEngineAuth` — the low-level service-account auth helper
  (`ee.Initialize` against a registered project; base64 key encode/decode).
* :func:`create_geometry` / :func:`create_feature` — Shapely /
  `GeoDataFrame` → `ee.Geometry` / `ee.FeatureCollection` converters.

The Earth Engine SDK (`earthengine-api`, the `[gee]` extra) is imported
when this package is imported — install `earthlens[gee]` to use it; the
`EarthLens` facade still imports without it (it loads each backend
lazily). Authentication setup is documented under
`docs/reference/google-earth-engine/`.

Examples:
    - List datasets and look up a band's metadata (no network):

        ```python
        >>> from earthlens.gee import Catalog
        >>> cat = Catalog()
        >>> "USGS/SRTMGL1_003" in cat.datasets
        True
        >>> cat.get_dataset("USGS/SRTMGL1_003").title
        'NASA SRTM Digital Elevation 30m'
        >>> cat.get_band("UCSB-CHG/CHIRPS/DAILY", "precipitation").units
        'mm/d'

        ```
"""

from __future__ import annotations

from earthlens.gee.auth import AuthenticationError, EarthEngineAuth
from earthlens.gee.backend import GEE
from earthlens.gee.catalog import (
    CATALOG_PATH,
    PROVIDERS_PATH,
    Band,
    Cadence,
    Catalog,
    Dataset,
    Extent,
    Provider,
)
from earthlens.gee.features import create_feature, create_geometry
from earthlens.gee.io import (
    feature_collection_to_dataframe,
    feature_collection_to_gdf,
    feature_collections_to_dataframe,
)
from earthlens.gee.sampling import sample_points, sample_points_to_gdf

__all__ = [
    "GEE",
    "AuthenticationError",
    "Catalog",
    "Dataset",
    "Band",
    "Cadence",
    "Extent",
    "Provider",
    "CATALOG_PATH",
    "PROVIDERS_PATH",
    "EarthEngineAuth",
    "create_geometry",
    "create_feature",
    "feature_collection_to_dataframe",
    "feature_collection_to_gdf",
    "feature_collections_to_dataframe",
    "sample_points",
    "sample_points_to_gdf",
]
