"""Submit pre-validated probes for every open dataset in parallel.

For each dataset, walks ``constraints.json`` to find the first entry
that lists at least one variable, builds a single-cell request from
that entry's first values, runs the M16+M17 validator locally, and
submits the request only if the validator passes.

Usage::

    pixi run -e dev python tools/probe_open_datasets.py
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests as _requests

from earthly.ecmwf.constraints import RequestValidator, fetch_constraints

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


def first_valid_combo(dataset: str) -> dict[str, Any] | None:
    """Pick the first constraint entry that has a non-empty variable list."""
    constraints = fetch_constraints(dataset)
    if not constraints:
        return None
    for entry in constraints:
        if entry.get("variable"):
            request: dict[str, Any] = {"data_format": "netcdf"}
            for key, value in entry.items():
                if key == "variable":
                    request["variable"] = value[:6]
                elif isinstance(value, list) and value:
                    request[key] = value[:1]
                else:
                    request[key] = value
            return request
    return None


def _read_cdsapirc() -> dict[str, str]:
    """Parse ``~/.cdsapirc`` into a {url, key} dict."""
    cfg: dict[str, str] = {}
    with open(os.path.expanduser("~/.cdsapirc")) as fh:
        for line in fh:
            if ":" in line:
                k, _, v = line.partition(":")
                cfg[k.strip()] = v.strip()
    return cfg


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
    import argparse
    import time

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
    for i, dataset in enumerate(pool):
        if i > 0 and not args.dry_run:
            time.sleep(args.delay)
        # noqa: continue on the for-loop scope
        request = first_valid_combo(dataset)
        if request is None:
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
