"""Audit Earth Engine datasets for inclusion in the GEE catalog.

For every asset id in ``available_datasets:`` of
``src/earthlens/gee/catalog/_index.yaml`` (merged with the per-provider
``catalog/*.yaml`` files by :class:`earthlens.gee.Catalog`), fetch its
STAC document and classify it:

* **DONE** — already a key in the curated ``datasets:`` map.
* **addressable** — an ``image`` / ``image_collection`` with at least
  one band carrying usable metadata (a ``gee:units`` or ``gee:scale``);
  can be auto-stanza'd via ``tools/gee/refresh_gee_catalog.py --with-bands``.
* **thin** — an ``image`` / ``image_collection`` whose STAC has no
  ``eo:bands`` or only bare bands (needs hand-modelling).
* **table** — a ``FeatureCollection`` (``gee:type == "table"``); out of
  scope for the raster backend.
* **missing** — no STAC document available.

Prints per-bucket counts plus a TODO list of the highest-impact
``addressable`` datasets not yet DONE. Fetched STAC documents are cached
under ``tools/gee/_gee_stac_cache/`` so a re-run is offline.

This is the GEE analogue of ``tools/ecmwf/audit_cds_datasets.py``. Run:

    pixi run -e dev python tools/gee/audit_gee_datasets.py

Not part of the installed package.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gee_stac import fetch_collection_stac, stac_url  # noqa: E402

from earthlens.gee import Catalog  # noqa: E402

CACHE_DIR = Path("tools/gee/_gee_stac_cache")

# Checks where any failure should fail CI. `raster_no_bands` and
# `unused_provider` are reported but not blocking — the former tracks
# access-restricted assets that legitimately can't be hydrated, the
# latter just notes registry entries (often parent slugs) that no
# dataset currently references.
_BLOCKING_CHECKS = ("long_title", "html_in_title", "unregistered_provider")


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


def _print_health(cat: Catalog) -> int:
    """Print `Catalog.health()` and return non-zero if a blocking check fails."""
    report = cat.health()
    print("\nCatalog hygiene (Catalog.health()):")
    for check, ids in report.items():
        tag = "FAIL" if (check in _BLOCKING_CHECKS and ids) else "ok  "
        print(f"  [{tag}] {check:24s} {len(ids)}")
        for aid in ids[:5]:
            print(f"             - {aid}")
        if len(ids) > 5:
            print(f"             ... and {len(ids) - 5} more")
    failed = [c for c in _BLOCKING_CHECKS if report.get(c)]
    if failed:
        print(f"\nblocking hygiene checks failed: {failed}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Classify every ``available_datasets:`` entry and print a coverage report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--health-only",
        action="store_true",
        help="skip the STAC walk + coverage classification; only run the "
             "Catalog.health() hygiene checks (fast, no network).",
    )
    args = parser.parse_args(argv)

    try:
        cat = Catalog()
    except (FileNotFoundError, ValueError) as exc:
        print(f"could not load the GEE catalog: {exc}", file=sys.stderr)
        return 1

    if args.health_only:
        return _print_health(cat)

    available = list(cat.available_datasets)
    curated = set(cat.datasets.keys())
    if not available:
        print("available_datasets: is empty — run tools/gee/refresh_gee_catalog.py first", file=sys.stderr)
        return 1

    buckets: dict[str, list[str]] = {}
    for asset_id in available:
        buckets.setdefault(classify(asset_id, curated), []).append(asset_id)

    counts = Counter({name: len(ids) for name, ids in buckets.items()})
    print(f"\nAudited {len(available)} datasets in src/earthlens/gee/catalog/:")
    for name in ("DONE", "addressable", "thin", "table", "missing"):
        print(f"  {name:12s}: {counts.get(name, 0)}")

    todo = sorted(buckets.get("addressable", []))
    if todo:
        print(f"\nTODO — {len(todo)} addressable datasets not yet curated "
              "(auto-stanza with `refresh-gee-catalog refresh --with-bands <id>`):")
        for asset_id in todo[:40]:
            print(f"  - {asset_id}   ({stac_url(asset_id)})")
        if len(todo) > 40:
            print(f"  ... and {len(todo) - 40} more")

    return _print_health(cat)


if __name__ == "__main__":
    raise SystemExit(main())
