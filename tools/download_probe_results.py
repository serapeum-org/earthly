"""Download the result of every successful CDS probe in the user's
recent job list and extract its NetCDF metadata.

Pairs with `tools.probe_open_datasets` — that script submits
fire-and-forget requests; this one waits for them to finish and
pulls the resulting NetCDF / Zip into `C:/tmp/cds_probe/` before
running nc-variable extraction.

Thin CLI wrapper around `Catalog.list_recent_jobs` and
`Catalog.download_job` from the package — no duplicated HTTP
plumbing here.

Usage::

    pixi run -e dev python tools/download_probe_results.py
    pixi run -e dev python tools/download_probe_results.py --max-age-min 60
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from probe_cds_netcdf import collect_metadata, maybe_unzip

from earthly.ecmwf import Catalog

CACHE_DIR = Path("C:/tmp/cds_probe")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age-min", type=int, default=60)
    parser.add_argument(
        "--out-json", type=Path, default=CACHE_DIR / "_open_summary.json"
    )
    args = parser.parse_args()
    cat = Catalog()
    jobs = cat.list_recent_jobs(
        status="successful", max_age_min=args.max_age_min, limit=100
    )
    print(f"Found {len(jobs)} successful job(s) within last {args.max_age_min}m")
    summary: dict[str, dict[str, Any]] = {}
    for job in jobs:
        process = job["processID"]
        target = CACHE_DIR / f"{process}_open.nc"
        try:
            path = cat.download_job(job["jobID"], target)
            extracted = maybe_unzip(path)
            metadata = collect_metadata(extracted)
            summary[process] = {
                "jobID": job["jobID"][:8],
                "path": str(path),
                "nc_variables": metadata,
            }
            print(f"  [OK] {process}: {len(metadata)} nc_variable(s)")
        except Exception as exc:
            print(f"  [ERR] {process}: {type(exc).__name__}: {str(exc)[:100]}")
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote summary to {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
