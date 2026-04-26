"""ECMWF / Copernicus Climate Data Store backend.

Public surface re-exported from this package so callers can write
``from earth2observe.ecmwf import ECMWF`` without reaching into the
private module layout.

The catalog YAML (``cds_data_catalog.yaml``) ships with this package
as data, loaded by :class:`Catalog.get_catalog` via
``Path(__file__).parent``.
"""

from __future__ import annotations

from earth2observe.ecmwf.backend import (
    ERA5_GRID_DEGREES,
    AuthenticationError,
    ECMWF,
    Variable,
)
from earth2observe.ecmwf.catalog import CATALOG_PATH, Catalog

__all__ = [
    "ECMWF",
    "Catalog",
    "Variable",
    "AuthenticationError",
    "ERA5_GRID_DEGREES",
    "CATALOG_PATH",
]
