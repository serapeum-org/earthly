"""Shared helpers for the Earth Engine STAC catalog tools.

The Earth Engine data catalog has a machine-readable STAC 1.0.0 form
rooted at ``https://storage.googleapis.com/earthengine-stac/catalog/catalog.json``:
a root catalog whose ``rel: child`` links point at provider
sub-catalogs, each of whose ``rel: child`` links point at either a
nested sub-catalog (href ending ``/catalog.json``) or a single dataset
STAC JSON. This module walks that tree to enumerate the dataset STAC
documents and to fetch them; it is imported by
``tools/gee/refresh_gee_catalog.py`` and ``tools/gee/audit_gee_datasets.py``.

Not part of the installed package — a ``tools/`` script helper only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

STAC_BASE = "https://storage.googleapis.com/earthengine-stac/catalog"
STAC_ROOT = f"{STAC_BASE}/catalog.json"

_TIMEOUT_SECONDS = 30
_FETCH_WORKERS = 16


def fetch_json(url: str) -> Any:
    """GET `url` and parse the body as JSON (raises on network/parse error)."""
    with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:  # nosec B310
        return json.load(response)


def try_fetch_json(url: str) -> Any | None:
    """GET `url` and parse the body as JSON, returning ``None`` on any error."""
    try:
        return fetch_json(url)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def stac_url(asset_id: str) -> str:
    """Return the STAC-JSON URL for an Earth Engine asset id.

    Convention: replace every ``/`` in the id with ``_``, prepend the
    provider (the id's first path segment) as a directory, append
    ``.json`` (hyphens and dots in segments are preserved).

    Args:
        asset_id: e.g. ``"LANDSAT/LC09/C02/T1_L2"``.

    Returns:
        e.g. ``".../catalog/LANDSAT/LANDSAT_LC09_C02_T1_L2.json"``.
    """
    provider = asset_id.split("/", 1)[0]
    return f"{STAC_BASE}/{provider}/{asset_id.replace('/', '_')}.json"


def _dataset_hrefs(*, verbose: bool = False) -> list[str]:
    """Walk the STAC tree from the root and return every dataset STAC-JSON href.

    Recurses into catalog nodes (hrefs ending ``/catalog.json``) and
    collects the rest as dataset documents. ~130 catalog fetches.
    """
    hrefs: list[str] = []
    queue = [STAC_ROOT]
    seen: set[str] = set()
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        catalog = try_fetch_json(url)
        if catalog is None:
            if verbose:
                print(f"  ! skipping unreadable catalog {url}")
            continue
        if verbose and url != STAC_ROOT:
            print(f"  walked {url}")
        for link_obj in catalog.get("links", []):
            if link_obj.get("rel") != "child":
                continue
            href = link_obj.get("href")
            if not href:
                continue
            (queue if href.endswith("/catalog.json") else hrefs).append(href)
    return hrefs


def collect_collection_ids(*, verbose: bool = False) -> list[str]:
    """Return a sorted list of every Earth Engine dataset asset id.

    Walks the STAC tree for the dataset STAC-JSON hrefs, then fetches
    each document (concurrently) and reads its authoritative ``id``
    field — the filename cannot be inverted reliably because id segments
    may themselves contain underscores.

    Args:
        verbose: Print progress.

    Returns:
        A sorted, de-duplicated list of asset ids.
    """
    hrefs = _dataset_hrefs(verbose=verbose)
    if verbose:
        print(f"  found {len(hrefs)} dataset STAC documents; fetching ids ...")
    ids: set[str] = set()
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
        for doc in pool.map(try_fetch_json, hrefs):
            if isinstance(doc, dict) and doc.get("id"):
                ids.add(doc["id"])
    return sorted(ids)


def fetch_collection_stac(asset_id: str) -> dict[str, Any] | None:
    """Fetch one dataset's STAC JSON, or ``None`` if unavailable (404 / error)."""
    return try_fetch_json(stac_url(asset_id))
