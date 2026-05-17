"""Refresh the ``available_datasets`` index in the CDS data catalog.

Pulls the current STAC catalogue from
``https://cds.climate.copernicus.eu/api/catalogue/v1/collections``,
filters for the ECMWF / Copernicus Climate Data Store entries the
package targets, and rewrites the ``available_datasets:`` block in
``src/earthlens/ecmwf/cds_data_catalog.yaml`` in place. Other
parts of the YAML (the ``datasets:`` curated map, the schema header
comments) are preserved verbatim.

Run before each release so the catalogue file reflects whatever
datasets CDS hosts on the day the release ships:

    pixi run -e dev python tools/ecmwf/refresh_available_datasets.py

The script exits 0 on a successful refresh, 1 on any HTTP / parse
error. It does **not** mutate the curated `datasets:` block — that
remains authored by hand.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

CATALOG_PATH = Path("src/earthlens/ecmwf/cds_data_catalog.yaml")
CDS_COLLECTIONS_URL = "https://cds.climate.copernicus.eu/api/catalogue/v1/collections"


_CATEGORY_PREFIXES: tuple[tuple[str, str], ...] = (
    ("Reanalyses", "reanalysis-"),
    ("Derived / aggregated products", "derived-"),
    ("Climate projections", "projections-"),
    ("Seasonal forecasts", "seasonal-"),
    ("In-situ observations", "insitu-"),
    ("Satellite products", "satellite-"),
    ("Sectoral Information System (SIS)", "sis-"),
    ("Multi-origin / atlas", "multi-"),
    ("Provider", "provider-"),
)


def fetch_collections() -> list[str]:
    """Return every CDS collection short name, sorted."""
    if not CDS_COLLECTIONS_URL.startswith(("https://", "http://")):
        raise ValueError(f"refusing to fetch non-http(s) URL: {CDS_COLLECTIONS_URL!r}")
    req = urllib.request.Request(
        CDS_COLLECTIONS_URL,
        headers={"Accept": "application/json"},
    )
    # Scheme validated above — bandit B310 does not apply.
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
        payload = json.loads(resp.read())
    raw = payload.get("collections") or []
    return sorted({c["id"] for c in raw if c.get("id")})


def categorise(ids: list[str]) -> list[tuple[str, list[str]]]:
    """Group dataset short names by family prefix, preserving order."""
    bucketed: dict[str, list[str]] = {label: [] for label, _ in _CATEGORY_PREFIXES}
    other: list[str] = []
    for ds in ids:
        for label, prefix in _CATEGORY_PREFIXES:
            if ds.startswith(prefix):
                bucketed[label].append(ds)
                break
        else:
            other.append(ds)
    grouped = [(label, bucketed[label]) for label, _ in _CATEGORY_PREFIXES]
    if other:
        grouped.append(("Other", other))
    return grouped


def render_block(grouped: list[tuple[str, list[str]]]) -> str:
    """Format the ``available_datasets:`` YAML block."""
    out = ["available_datasets:"]
    first = True
    for label, items in grouped:
        if not items:
            continue
        prefix = "" if first else "\n"
        out.append(f"{prefix}  # ----- {label} -----")
        out.extend(f"  - {ds}" for ds in items)
        first = False
    return "\n".join(out) + "\n"


def splice_into_yaml(yaml_text: str, new_block: str) -> str:
    """Replace the existing ``available_datasets:`` block in-place."""
    pattern = re.compile(
        r"^available_datasets:\n(?:[ \t]+.*(?:\n|$)|\n)+",
        re.MULTILINE,
    )
    match = pattern.search(yaml_text)
    if not match:
        raise ValueError(f"{CATALOG_PATH} is missing an ``available_datasets:`` block")
    return yaml_text[: match.start()] + new_block + yaml_text[match.end() :]


def main() -> int:
    try:
        ids = fetch_collections()
    except Exception as exc:
        print(f"failed to fetch CDS collections: {exc}", file=sys.stderr)
        return 1
    grouped = categorise(ids)
    block = render_block(grouped)
    text = CATALOG_PATH.read_text(encoding="utf-8")
    updated = splice_into_yaml(text, block)
    if updated == text:
        print("No changes — catalog already up to date.")
        return 0
    CATALOG_PATH.write_text(updated, encoding="utf-8")
    print(f"Rewrote {CATALOG_PATH} with {len(ids)} datasets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
