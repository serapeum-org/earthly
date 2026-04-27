"""ECMWF / Copernicus Climate Data Store backend.

Thin wrapper over :mod:`cdsapi` that downloads ERA5 reanalyses from
the Climate Data Store and slices the resulting NetCDF into per-date
arrays.

Public surface (re-exported from this package):

* :class:`ECMWF` — the backend itself; instantiate with a date range,
  a bbox, and a list of variable short codes, then call
  :meth:`ECMWF.download` to fetch every variable.
* :class:`Catalog` — pydantic-backed loader for
  ``cds_data_catalog.yaml``. Exposes the YAML's structure as three
  fields: :attr:`Catalog.available_datasets`, :attr:`Catalog.datasets`,
  and :attr:`Catalog.catalog` (flat per-variable map).
* :class:`Dataset` — one CDS dataset's section inside the catalog
  (monthly variant + variables map).
* :class:`Variable` — one variable's metadata (CDS request name,
  NetCDF short name, raw ERA5 unit, pressure-level info).
* :class:`AuthenticationError` — raised when cdsapi cannot
  authenticate against CDS.
* :data:`ERA5_GRID_DEGREES` — ERA5 native grid spacing (0.125°),
  used by :meth:`ECMWF.create_grid` to snap user bboxes.
* :data:`CATALOG_PATH` — absolute path to the bundled YAML catalog;
  monkey-patchable to redirect the loader.

The catalog YAML ships with this package as data, loaded by
:class:`Catalog` from ``Path(__file__).parent``.

Examples:
    - List all entries in the bundled catalog:

        ```python
        >>> from earth2observe.ecmwf import Catalog
        >>> cat = Catalog()
        >>> "2m-temperature" in cat.catalog
        True
        >>> cat.datasets["reanalysis-era5-single-levels"].monthly
        'reanalysis-era5-single-levels-monthly-means'

        ```
"""

from __future__ import annotations

from earth2observe.ecmwf.backend import (
    ERA5_GRID_DEGREES,
    AuthenticationError,
    ECMWF,
    Variable,
)
from earth2observe.ecmwf.catalog import CATALOG_PATH, Catalog, Dataset

__all__ = [
    "ECMWF",
    "Catalog",
    "Dataset",
    "Variable",
    "AuthenticationError",
    "ERA5_GRID_DEGREES",
    "CATALOG_PATH",
]
