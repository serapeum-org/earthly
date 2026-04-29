"""Download the result of every successful CDS probe in the user's
recent job list and extract its NetCDF metadata.

Pairs with :mod:`tools.probe_open_datasets` — that script submits
fire-and-forget requests; this one waits for them to finish and
pulls the resulting NetCDF / Zip into ``C:/tmp/cds_probe/`` before
running the same nc-variable extraction the original probe script
does.

Usage::

    pixi run -e dev python tools/download_probe_results.py
    pixi run -e dev python tools/download_probe_results.py --max-age-min 60
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_cds_netcdf import collect_metadata, maybe_unzip  # noqa: E402

CACHE_DIR = Path("C:/tmp/cds_probe")


def _read_cdsapirc() -> dict[str, str]:
    cfg: dict[str, str] = {}
    with open(os.path.expanduser("~/.cdsapirc")) as fh:
        for line in fh:
            if ":" in line:
                k, _, v = line.partition(":")
                cfg[k.strip()] = v.strip()
    return cfg


def list_jobs(cfg: dict[str, str], max_age_min: int) -> list[dict[str, Any]]:
    url = cfg["url"].rstrip("/") + "/retrieve/v1/jobs"
    r = requests.get(
        url,
        headers={"PRIVATE-TOKEN": cfg["key"]},
        params={"limit": 100},
    )
    out: list[dict[str, Any]] = []
    # CDS timestamps are UTC; compare against UTC clock so the
    # ``--max-age-min`` window means what it says.
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    for j in r.json().get("jobs", []):
        if j.get("status") != "successful":
            continue
        created = j.get("created", "")
        if not created:
            continue
        ago = (now - datetime.datetime.fromisoformat(created.replace("Z", ""))).total_seconds() / 60
        if ago < max_age_min:
            out.append(j)
    return out


def fetch_result(cfg: dict[str, str], job: dict[str, Any]) -> Path | None:
    """Pull the result NetCDF for a successful job into the cache."""
    job_id = job["jobID"]
    process = job["processID"]
    target = CACHE_DIR / f"{process}_open.nc"
    if target.exists() and target.stat().st_size > 0:
        return target
    rurl = cfg["url"].rstrip("/") + f"/retrieve/v1/jobs/{job_id}/results"
    r = requests.get(rurl, headers={"PRIVATE-TOKEN": cfg["key"]})
    if r.status_code != 200:
        print(f"  result lookup failed for {process}: {r.status_code}")
        return None
    data = r.json()
    asset = data.get("asset", {}).get("value", {})
    href = asset.get("href")
    if not href:
        print(f"  no asset href for {process}")
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(href, timeout=60) as resp, open(target, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-age-min", type=int, default=60)
    parser.add_argument("--out-json", type=Path, default=CACHE_DIR / "_open_summary.json")
    args = parser.parse_args()
    cfg = _read_cdsapirc()
    jobs = list_jobs(cfg, args.max_age_min)
    print(f"Found {len(jobs)} successful job(s) within last {args.max_age_min}m")
    summary: dict[str, dict[str, Any]] = {}
    for job in jobs:
        process = job["processID"]
        try:
            path = fetch_result(cfg, job)
            if path is None:
                continue
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
