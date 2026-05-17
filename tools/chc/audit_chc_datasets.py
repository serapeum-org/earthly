"""Audit each curated CHC dataset against the live FTP server.

For every dataset in :class:`earthlens.chc.Catalog`, probe the FTP base
path on `data.chc.ucsb.edu` and a sample remote filename. Classify each
dataset into one of:

* `accessible` — `cwd` to `ftp_bases[default_format]` succeeds and a
  sample file (derived from `file_patterns[default_format]` with a date
  inside `[start_date, end_date]`) is listed by `NLST`.
* `cwd-failed` — the FTP path itself does not exist on the server.
* `file-missing` — `cwd` OK but the sample filename is not listed.
* `skipped` — the file pattern uses a placeholder the backend does not
  yet expand (`{start_yyyymmdd}`, `{month_pair}`, `{res}`, `{scale}`);
  these are tracked under planning issue M5 and not probed here.

Prints per-bucket counts plus a TODO list of every `cwd-failed` /
`file-missing` dataset so the maintainer can correct the YAML. Probe
results are cached under `tools/chc/_audit_cache.json` so a re-run is
offline. Pass `--no-cache` to force a fresh walk, or `--limit N` to cap
the number of datasets probed (useful for smoke-testing).

Run with:

    pixi run -e dev python tools/chc/audit_chc_datasets.py
    pixi run -e dev python tools/chc/audit_chc_datasets.py --no-cache
    pixi run -e dev python tools/chc/audit_chc_datasets.py --limit 10

Not part of the installed package.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from ftplib import FTP, error_perm  # nosec B402  # noqa: S402
from pathlib import Path

import pandas as pd

# Allow running directly from the repo without an editable install.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
from earthlens.chc import Catalog  # noqa: E402
from earthlens.chc.backend import CHIRPS  # noqa: E402

FTP_HOST: str = "data.chc.ucsb.edu"
CACHE_PATH: Path = Path(__file__).parent / "_audit_cache.json"

# Placeholders the backend's `_placeholders()` already substitutes —
# anything else triggers `skipped`.
_KNOWN_PLACEHOLDERS = {"year", "month", "day", "dekad", "pentad", "hour", "doy"}


def _sample_date(start: str, end: str | None) -> pd.Timestamp:
    """Pick a representative date inside `[start, end]` to probe.

    Defaults to one year past the dataset's `start_date`, clamped into
    the window. Avoids the boundary dates (which sometimes are missing
    on the FTP server while the bulk of the series is fine).
    """
    start_ts = pd.Timestamp(start)
    if end is None:
        end_ts = pd.Timestamp.now()
    else:
        end_ts = pd.Timestamp(end)
    candidate = start_ts + pd.DateOffset(years=1)
    if candidate < start_ts:
        candidate = start_ts
    if candidate > end_ts:
        candidate = end_ts
    return candidate


def _required_placeholders(pattern: str) -> set[str]:
    """Return the `{...}` placeholder names in `pattern`."""
    placeholders: set[str] = set()
    i = 0
    while i < len(pattern):
        if pattern[i] == "{":
            end = pattern.find("}", i + 1)
            if end == -1:
                break
            placeholders.add(pattern[i + 1 : end])
            i = end + 1
        else:
            i += 1
    return placeholders


def _classify(ftp: FTP, ds_key: str, catalog: Catalog) -> dict[str, str]:
    """Probe one dataset; return a record `{status, detail}`."""
    dataset = catalog.datasets[ds_key]
    fmt = dataset.default_format
    ftp_base = dataset.ftp_bases[fmt]
    pattern = dataset.file_patterns[fmt]

    needed = _required_placeholders(pattern)
    unsupported = needed - _KNOWN_PLACEHOLDERS
    if unsupported:
        return {
            "status": "skipped",
            "detail": f"pattern needs unsupported placeholder(s): "
            f"{sorted(unsupported)}",
        }

    date = _sample_date(dataset.start_date, dataset.end_date)
    relative = pattern.format(**CHIRPS._placeholders(date))
    if "/" in relative:
        subdir, _, sample_filename = relative.rpartition("/")
        remote_dir = f"{ftp_base.rstrip('/')}/{subdir}/"
    else:
        remote_dir = ftp_base
        sample_filename = relative

    try:
        ftp.cwd("/")
        ftp.cwd(remote_dir)
    except error_perm as exc:
        return {
            "status": "cwd-failed",
            "detail": f"{remote_dir} -> {exc}",
        }
    except Exception as exc:  # noqa: BLE001  - report any FTP failure verbatim
        return {
            "status": "cwd-failed",
            "detail": f"{remote_dir} -> {type(exc).__name__}: {exc}",
        }

    try:
        listing = ftp.nlst()
    except error_perm as exc:
        return {
            "status": "file-missing",
            "detail": f"nlst {remote_dir} -> {exc}",
        }
    if sample_filename not in listing:
        return {
            "status": "file-missing",
            "detail": (
                f"{sample_filename} not in {remote_dir} "
                f"({len(listing)} entries listed)"
            ),
        }
    return {"status": "accessible", "detail": f"{remote_dir}{sample_filename}"}


def _load_cache() -> dict[str, dict[str, str]]:
    if not CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_cache(records: dict[str, dict[str, str]]) -> None:
    CACHE_PATH.write_text(
        json.dumps(records, indent=2, sort_keys=True), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore the on-disk cache and probe every dataset live.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Stop after probing N datasets (useful for smoke testing).",
    )
    args = parser.parse_args()

    catalog = Catalog()
    keys = sorted(catalog.datasets)
    if args.limit:
        keys = keys[: args.limit]

    cache = {} if args.no_cache else _load_cache()
    fresh: dict[str, dict[str, str]] = {}

    with FTP(FTP_HOST) as ftp:  # nosec B321
        ftp.login()
        for i, ds_key in enumerate(keys, 1):
            if ds_key in cache:
                fresh[ds_key] = cache[ds_key]
                continue
            print(
                f"[{i:3d}/{len(keys)}] probing {ds_key} ...",
                end=" ",
                flush=True,
            )
            try:
                record = _classify(ftp, ds_key, catalog)
            except Exception as exc:  # noqa: BLE001
                record = {
                    "status": "cwd-failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            fresh[ds_key] = record
            print(record["status"])

    _save_cache({**cache, **fresh})

    buckets: dict[str, list[tuple[str, str]]] = {}
    for ds_key in keys:
        record = fresh.get(ds_key) or cache.get(ds_key)
        if record is None:
            continue
        buckets.setdefault(record["status"], []).append(
            (ds_key, record["detail"])
        )

    counts = Counter({name: len(items) for name, items in buckets.items()})
    print(f"\nAudited {len(keys)} CHC datasets against {FTP_HOST}:")
    for name in ("accessible", "skipped", "cwd-failed", "file-missing"):
        print(f"  {name:14s}: {counts.get(name, 0)}")

    failures = buckets.get("cwd-failed", []) + buckets.get("file-missing", [])
    if failures:
        print(f"\nTODO — {len(failures)} datasets need catalog fixes:")
        for ds_key, detail in sorted(failures):
            print(f"  - {ds_key}: {detail}")

    skipped = buckets.get("skipped", [])
    if skipped:
        print(
            f"\nSkipped (M5 — unsupported pattern placeholder) — "
            f"{len(skipped)} datasets:"
        )
        for ds_key, detail in sorted(skipped):
            print(f"  - {ds_key}: {detail}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
