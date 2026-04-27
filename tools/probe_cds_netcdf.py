"""Probe CDS variables to discover their NetCDF short name and units.

Submits a single ``cdsapi.Client.retrieve()`` per batch of variables,
extracts ``long_name`` / ``units`` for every variable in the returned
NetCDF, and writes a JSON sidecar mapping ``cds_variable`` → metadata.

Usage::

    pixi run -e dev python tools/probe_cds_netcdf.py \
        --dataset reanalysis-era5-land \
        --variables evaporation_from_bare_soil,total_evaporation,... \
        --out C:/tmp/cds_probe/era5land_missing.json

Cached files land under ``C:/tmp/cds_probe/<dataset>_<batch>.nc``
so re-running the script avoids re-queuing CDS.
"""

from __future__ import annotations

import argparse
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import cdsapi
from pyramids.netcdf import NetCDF


CACHE_DIR = Path("C:/tmp/cds_probe")


def fetch_one_batch(
    client: cdsapi.Client,
    dataset: str,
    variables: list[str],
    target: Path,
    extras: dict[str, Any] | None = None,
) -> Path:
    """Submit a single retrieve for ``variables`` and return the file path."""
    request: dict[str, Any] = {
        "variable": variables,
        "year": ["2022"],
        "month": ["01"],
        "day": ["01"],
        "time": ["00:00"],
        "data_format": "netcdf",
        "area": [4.5, -75.5, 4.0, -74.5],
    }
    if "monthly" not in dataset:
        request["product_type"] = ["reanalysis"]
    if extras:
        request.update(extras)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        client.retrieve(dataset, request, str(target))
    return target


def maybe_unzip(nc_path: Path) -> Path:
    """If CDS returned a zip wrapping NetCDFs, unzip and return the dir."""
    extracted = nc_path.with_suffix(".extracted")
    if zipfile.is_zipfile(nc_path):
        if not extracted.exists():
            extracted.mkdir()
            with zipfile.ZipFile(nc_path) as zf:
                zf.extractall(extracted)
        return extracted
    return nc_path


def collect_metadata(path: Path) -> dict[str, dict[str, str]]:
    """Walk ``path`` (file or dir) and collect long_name + units per nc var."""
    if path.is_dir():
        files = sorted(path.glob("*.nc"))
    else:
        files = [path]
    out: dict[str, dict[str, str]] = {}
    skip = {
        "latitude",
        "longitude",
        "time",
        "valid_time",
        "number",
        "expver",
    }
    for nc in files:
        with NetCDF.read_file(str(nc), read_only=True) as fh:
            for name, var in fh.meta_data.variables.items():
                if name in skip:
                    continue
                long_name = getattr(var, "long_name", "") or ""
                units = getattr(var, "unit", "") or ""
                if long_name or units:
                    out[name] = {"long_name": long_name, "units": units}
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--variables",
        required=True,
        help="comma-separated CDS variable names",
    )
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--batch-tag", default="probe")
    args = parser.parse_args()

    variables = [v.strip() for v in args.variables.split(",") if v.strip()]
    cache_target = CACHE_DIR / f"{args.dataset}_{args.batch_tag}.nc"
    client = cdsapi.Client()
    fetched = fetch_one_batch(client, args.dataset, variables, cache_target)
    extracted = maybe_unzip(fetched)
    metadata = collect_metadata(extracted)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Wrote {len(metadata)} entries to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
