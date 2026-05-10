"""Abstract base classes and shared value objects for every data source.

Public surface re-exported from this package so callers can write
`from earthlens.base import SpatialExtent` without reaching
into the private module layout.
"""

from __future__ import annotations

from earthlens.base.abstractdatasource import (
    AbstractCatalog,
    AbstractDataSource,
    SpatialExtent,
    TemporalExtent,
)

__all__ = [
    "AbstractCatalog",
    "AbstractDataSource",
    "SpatialExtent",
    "TemporalExtent",
]
