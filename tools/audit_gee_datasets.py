"""Audit Earth Engine datasets for inclusion in the GEE catalog.

For every asset id in ``available_datasets:`` of
``src/earthlens/gee/gee_data_catalog.yaml``, fetch its STAC document and
classify it:

* **DONE** — already a key in the curated ``datasets:`` map.
* **addressable** — an ``image`` / ``image_collection`` with at least
  one band carrying usable metadata (a ``gee:units`` or ``gee:scale``);
  can be auto-stanza'd via ``tools/refresh_gee_catalog.py --with-bands``.
* **thin** — an ``image`` / ``image_collection`` whose STAC has no
  ``eo:bands`` or only bare bands (needs hand-modelling).
* **table** — a ``FeatureCollection`` (``gee:type == "table"``); out of
  scope for the raster backend.
* **missing** — no STAC document available.

Prints per-bucket counts plus a TODO list of the highest-impact
``addressable`` datasets not yet DONE. Fetched STAC documents are cached
under ``tools/_gee_stac_cache/`` so a re-run is offline.

This is the GEE analogue of ``tools/audit_cds_datasets.py``. Run:

    pixi run -e dev python tools/audit_gee_datasets.py

Not part of the installed package.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from _gee_stac import fetch_collection_stac, stac_url  # noqa: E402

CATALOG_PATH = Path("src/earthlens/gee/gee_data_catalog.yaml")
CACHE_DIR = Path("tools/_gee_stac_cache")


def _cached_stac(asset_id: str) -> dict | None:
    """Fetch a dataset's STAC doc, using/refreshing a local on-disk cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / (asset_id.replace("/", "_") + ".json")
    if cache_file.is_file():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    doc = fetch_collection_stac(asset_id)
    if doc is not None:
        cache_file.write_text(json.dumps(doc), encoding="utf-8")
    return doc


def classify(asset_id: str, curated: set[str]) -> str:
    """Return the bucket name for `asset_id` (see the module docstring)."""
    if asset_id in curated:
        return "DONE"
    doc = _cached_stac(asset_id)
    if doc is None:
        return "missing"
    if doc.get("gee:type") == "table":
        return "table"
    bands = (doc.get("summaries", {}) or {}).get("eo:bands") or []
    has_metadata = any(b.get("gee:units") or b.get("gee:scale") is not None for b in bands)
    return "addressable" if (bands and has_metadata) else "thin"


def main() -> int:
    """Classify every ``available_datasets:`` entry and print a coverage report."""
    if not CATALOG_PATH.is_file():
        print(f"{CATALOG_PATH} not found", file=sys.stderr)
        return 1
    catalog = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
    available = list(catalog.get("available_datasets") or [])
    curated = set((catalog.get("datasets") or {}).keys())
    if not available:
        print("available_datasets: is empty — run tools/refresh_gee_catalog.py first", file=sys.stderr)
        return 1

    buckets: dict[str, list[str]] = {}
    for asset_id in available:
        buckets.setdefault(classify(asset_id, curated), []).append(asset_id)

    counts = Counter({name: len(ids) for name, ids in buckets.items()})
    print(f"\nAudited {len(available)} datasets in {CATALOG_PATH}:")
    for name in ("DONE", "addressable", "thin", "table", "missing"):
        print(f"  {name:12s}: {counts.get(name, 0)}")

    todo = sorted(buckets.get("addressable", []))
    if todo:
        print(f"\nTODO — {len(todo)} addressable datasets not yet curated "
              "(auto-stanza with `refresh_gee_catalog.py --with-bands <id>`):")
        for asset_id in todo[:40]:
            print(f"  - {asset_id}   ({stac_url(asset_id)})")
        if len(todo) > 40:
            print(f"  ... and {len(todo) - 40} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
