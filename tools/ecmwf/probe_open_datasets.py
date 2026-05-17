"""Submit pre-validated probes for every open dataset in parallel.

For each dataset, asks the package's `Catalog.minimal_valid_request`
for a known-valid request (drawn from the public `constraints.json`),
runs the M16+M17 validator locally, and submits via fire-and-forget
POST only if the validator passes.

Usage::

    pixi run -e dev python tools/ecmwf/probe_open_datasets.py
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import requests as _requests

from earthlens.ecmwf import Catalog
from earthlens.ecmwf.catalog import _read_cdsapirc
from earthlens.ecmwf.constraints import RequestValidator

CACHE_DIR = Path("C:/tmp/cds_probe")

OPEN_DATASETS: tuple[str, ...] = (
    "reanalysis-carra-means",
    "reanalysis-cerra-pressure-levels",
    "reanalysis-cerra-height-levels",
    "reanalysis-cerra-single-levels",
    "reanalysis-cerra-model-levels",
    "reanalysis-cerra-land",
    "reanalysis-pan-carra",
    "reanalysis-pan-carra-means",
    "reanalysis-uerra-europe-single-levels",
    "reanalysis-uerra-europe-pressure-levels",
    "reanalysis-uerra-europe-height-levels",
    "reanalysis-uerra-europe-soil-levels",
    "seasonal-monthly-single-levels",
    "seasonal-monthly-pressure-levels",
    "seasonal-monthly-ocean",
    "seasonal-postprocessed-single-levels",
    "seasonal-postprocessed-pressure-levels",
    "seasonal-original-single-levels",
    "seasonal-original-pressure-levels",
    "projections-cmip6",
    "projections-cmip6-decadal-prototype",
    "derived-drought-historical-monthly",
    "derived-gridded-glacier-mass-change",
    "derived-near-surface-meteorological-variables",
    "derived-utci-historical",
    "ecv-for-climate-change",
)


def submit_async(dataset: str, request: dict[str, Any]) -> str:
    """POST the request to ``/processes/<id>/execution`` and return.

    Unlike :meth:`cdsapi.Client.retrieve`, this does **not** poll
    for results — the job is queued and the caller returns
    immediately. The result can be downloaded later via the job
    ID returned by CDS.
    """
    try:
        RequestValidator(dataset, request).check()
    except ValueError as exc:
        return f"FAIL (validator): {str(exc).splitlines()[0][:90]}"
    cfg = _read_cdsapirc()
    url = cfg["url"].rstrip("/") + f"/retrieve/v1/processes/{dataset}/execution"
    payload = {"inputs": {k: v for k, v in request.items() if k != "data_format"}}
    payload["inputs"]["data_format"] = request.get("data_format", "netcdf")
    try:
        resp = _requests.post(
            url,
            headers={"PRIVATE-TOKEN": cfg["key"], "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
    except Exception as exc:
        return f"ERR (POST): {type(exc).__name__}: {str(exc)[:80]}"
    if resp.status_code in (200, 201, 202):
        try:
            jid = resp.json().get("jobID", "?")[:8]
        except Exception:
            jid = "?"
        return f"QUEUED: jobID={jid}"
    return f"REJECTED ({resp.status_code}): {resp.text[:120]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the validator only; do not submit anything to CDS.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait between submissions (avoids rate-limit rejections).",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        help="Restrict to a subset of dataset names.",
    )
    args = parser.parse_args()
    pool = args.only or list(OPEN_DATASETS)
    catalog = Catalog()
    for i, dataset in enumerate(pool):
        if i > 0 and not args.dry_run:
            time.sleep(args.delay)
        request = catalog.minimal_valid_request(dataset)
        if set(request) <= {"data_format"}:
            print(f"[SKIP] {dataset}: no usable constraint entry")
            continue
        if args.dry_run:
            try:
                RequestValidator(dataset, request).check()
                print(f"[VALID] {dataset}")
            except ValueError as exc:
                print(f"[FAIL ] {dataset}: {str(exc).splitlines()[0][:90]}")
            continue
        outcome = submit_async(dataset, request)
        verdict = outcome.split(":", 1)[0].split()[0]
        print(f"[{verdict:<14}] {dataset}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
