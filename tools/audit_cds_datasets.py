"""Audit every CDS dataset in cds_data_catalog.yaml's available_datasets list.

For each dataset short name in `available_datasets` (the informational
index inside `src/earthlens/ecmwf/cds_data_catalog.yaml`), this
script hits the public constraints endpoint via the package's
`fetch_constraints` helper and prints:

* whether constraints are public,
* how many distinct `variable` values appear,
* which extra request fields beyond the ERA5 standard set are required.

The output is grouped by category (`DONE`, `addressable`,
`no-variable-key`, `no-or-empty-constraints`) so the agent maintaining
`planning/cdsapi/all-catalog.md` can see at a glance which datasets
can be added under the existing schema and which need bespoke modelling.

Usage::

    pixi run -e dev python tools/audit_cds_datasets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from earthlens.ecmwf.constraints import fetch_constraints

ERA5_KNOWN = {
    "variable",
    "year",
    "month",
    "day",
    "time",
    "pressure_level",
    "product_type",
    "data_format",
    "area",
}


def main() -> int:
    cat_path = Path("src/earthlens/ecmwf/cds_data_catalog.yaml")
    cat = yaml.safe_load(cat_path.read_text(encoding="utf-8"))
    have = set(cat["datasets"])
    rows = []
    for ds in cat["available_datasets"]:
        if ds in have:
            rows.append((ds, "DONE", 0, []))
            continue
        data = fetch_constraints(ds)
        if not data:
            # Package collapses 404 / network error / empty into [].
            rows.append((ds, "no-or-empty-constraints", 0, []))
            continue
        keys = set().union(*(set(e) for e in data[:50]))
        if "variable" not in keys:
            rows.append(
                (
                    ds,
                    "no-variable-key",
                    0,
                    sorted(k for k in keys if k not in ERA5_KNOWN),
                )
            )
            continue
        n = len({v for e in data for v in e.get("variable", [])})
        rows.append(
            (
                ds,
                "addressable",
                n,
                sorted(k for k in keys if k not in ERA5_KNOWN),
            )
        )

    for status in (
        "DONE",
        "addressable",
        "no-variable-key",
        "no-or-empty-constraints",
    ):
        items = [(d, n, e) for d, s, n, e in rows if s == status]
        print(f"\n{status}: {len(items)}")
        for d, n, e in items:
            print(f"  {d}: {n} vars, extras={e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
