"""CHIRPS (Climate Hazards InfraRed Precipitation with Stations) backend.

Downloads global and regional rainfall estimates from the CHIRPS FTP
server at `data.chc.ucsb.edu`.

Public surface:

* :class:`CHIRPS` — the backend itself; instantiate with a date range,
  a bbox, and a list of variable codes, then call
  :meth:`CHIRPS.download` to fetch the data.
* :class:`Catalog` — pydantic-backed loader for
  `chirps_data_catalog.yaml`. Exposes the YAML's structure as
  :attr:`Catalog.available_datasets` and :attr:`Catalog.datasets`.
* :class:`Dataset` — one CHIRPS dataset's section inside the catalog
  (FTP path, spatial/temporal metadata, variables map).
* :class:`Variable` — one variable's metadata (units, type, description).
* :data:`CATALOG_PATH` — absolute path to the bundled YAML catalog;
  monkey-patchable to redirect the loader.
"""

from __future__ import annotations

from earthlens.chirps.catalog import CATALOG_PATH, Catalog, Dataset, Variable
from earthlens.chirps.chirps import CHIRPS

__all__ = [
    "CHIRPS",
    "Catalog",
    "Dataset",
    "Variable",
    "CATALOG_PATH",
]
