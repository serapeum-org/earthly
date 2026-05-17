"""Climate Hazards Center (CHC) FTP backend.

Downloads CHC's full product line from `data.chc.ucsb.edu` over
anonymous FTP — the CHIRPS / CHIRP precipitation series, CHIRTSdaily
/ CHIRTSmonthly temperature & humidity, CHIRPS-GEFS ensemble forecasts,
CHPclim v2 climatology, WBGT (wet bulb globe temperature), SPI / SPEI
drought indices, and CHC_CMIP6 scenario deltas. Every dataset is
discoverable through :class:`Catalog`.

The class is named :class:`CHIRPS` for brand-recognition reasons —
CHIRPS is by far the best-known CHC product — but the same class
downloads any CHC dataset addressable through the catalog. Pass the
dataset key under `variables=` (e.g.
`variables={"africa-pentad": ["precipitation"]}`) to reach a
non-CHIRPS product.

Public surface:

* :class:`CHIRPS` — the backend itself; instantiate with a date range,
  a bbox, and a `variables` spec, then call :meth:`CHIRPS.download` to
  fetch the data.
* :class:`Catalog` — pydantic-backed loader for the per-family
  `catalog/*.yaml` files (split GEE-style: `chirps-2.0.yaml`,
  `chirps-v3.yaml`, `chirp.yaml`, `chirts.yaml`, `gefs.yaml`,
  `climatology.yaml`, `wbgt.yaml`, `indices.yaml`, `cmip6.yaml`,
  `centennial-trends.yaml`, plus `_index.yaml` for the informational
  index + regions block).
  Exposes the YAML's structure as :attr:`Catalog.available_datasets`,
  :attr:`Catalog.available_regions`, and :attr:`Catalog.datasets`.
* :class:`Dataset` — one CHC dataset's section inside the catalog
  (FTP path, spatial/temporal metadata, variables map).
* :class:`Variable` — one variable's metadata (units, type, description).
* :data:`CATALOG_PATH` — absolute path to the bundled YAML catalog;
  monkey-patchable to redirect the loader.
"""

from __future__ import annotations

from earthlens.chc.backend import CHIRPS
from earthlens.chc.catalog import CATALOG_PATH, Catalog, Dataset, Variable

__all__ = [
    "CHIRPS",
    "Catalog",
    "Dataset",
    "Variable",
    "CATALOG_PATH",
]
