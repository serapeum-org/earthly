"""FTP probe for the CHIRPS-GEFS v3 datasets.

The CHIRPS-GEFS v3 entries in `chc_data_catalog.yaml`
(`chirps-gefs-v3-daily`, `chirps-gefs-v3-dekad-lead0`,
`chirps-gefs-v3-pentad-lead0`) carry an explicit comment:
*"Provisional pattern; verify against FTP listing before use."* This
script lists the real contents of each GEFS directory on
`data.chc.ucsb.edu`, prints a sample of filenames, and suggests a
filename template inferred from the listing so the catalog patterns
can be corrected.

Run with:

    pixi run -e dev python tools/chc/probe_chirps_gefs.py
    pixi run -e dev python tools/chc/probe_chirps_gefs.py --limit 5

Output is purely diagnostic — the script never modifies the YAML.
"""

from __future__ import annotations

import argparse
import re
import sys
from ftplib import FTP, error_perm  # nosec B402  # noqa: S402
from pathlib import Path

# Allow running directly from the repo without an editable install.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
from earthlens.chc import Catalog  # noqa: E402

FTP_HOST: str = "data.chc.ucsb.edu"

# CHIRPS-GEFS v3 datasets the YAML flags as provisional.
TARGETS: tuple[str, ...] = (
    "chirps-gefs-v3-daily",
    "chirps-gefs-v3-dekad-lead0",
    "chirps-gefs-v3-pentad-lead0",
)


def _suggest_pattern(filenames: list[str]) -> str:
    """Heuristic: infer a `{year}.{month}.{day}`-style template.

    Replaces 4-digit year segments with `{year}`, 2-digit month/day
    segments with `{month}` / `{day}` based on position, and 3-digit
    `doy` runs with `{doy}`. Returns the first transformed filename
    seen; the caller is expected to eyeball it against the full
    listing and refine if needed.
    """
    if not filenames:
        return ""
    sample = filenames[0]
    # Tag 4-digit years first so the 2-digit month/day pass does not
    # shadow them.
    pattern = re.sub(r"\b(19|20)\d{2}\b", "{year}", sample)
    # Day-of-year (3 consecutive digits, e.g. "095") that is not part
    # of a longer run.
    pattern = re.sub(r"(?<!\d)(\d{3})(?!\d)", "{doy}", pattern)
    # Pair-of-2-digit dotted segments → month + day in that order.
    # We replace the first 2-digit run after `{year}.` as month,
    # and the next as day. This is a heuristic; the maintainer must
    # confirm whether the pattern actually nests `month.day` or
    # something else.
    seen_month = False
    out: list[str] = []
    for piece in re.split(r"(\{year\})", pattern):
        if piece == "{year}":
            out.append(piece)
            continue
        new_piece = piece
        if not seen_month:
            new_piece, n = re.subn(r"(?<=\.)(\d{2})(?=\.|$)", "{month}", new_piece, count=1)
            if n:
                seen_month = True
        if seen_month and "{day}" not in new_piece:
            new_piece = re.sub(
                r"(?<=\.)(\d{2})(?=\.|$)", "{day}", new_piece, count=1
            )
        out.append(new_piece)
    return "".join(out)


def _probe_directory(ftp: FTP, ds_key: str, ftp_base: str, limit: int) -> None:
    """List up to `limit` entries under `ftp_base` and print a summary."""
    print(f"\n=== {ds_key} ===")
    print(f"  ftp_base: {ftp_base}")
    try:
        ftp.cwd("/")
        ftp.cwd(ftp_base)
    except error_perm as exc:
        print(f"  cwd failed: {exc}")
        return
    try:
        listing = sorted(ftp.nlst())
    except error_perm as exc:
        print(f"  nlst failed: {exc}")
        return
    print(f"  {len(listing)} entries in directory")
    if not listing:
        return

    sample = listing[: limit]
    print(f"  sample (up to {limit}):")
    for name in sample:
        print(f"    - {name}")

    # If the immediate children are year-named subdirs, descend into the
    # first one for a flatter sample.
    if all(re.fullmatch(r"\d{4}", name) for name in listing[:5]):
        year_dir = listing[0]
        print(f"  (year-partitioned; descending into {year_dir}/)")
        try:
            ftp.cwd(year_dir)
            inner = sorted(ftp.nlst())[: limit]
            for name in inner:
                print(f"    - {year_dir}/{name}")
            suggestion = _suggest_pattern(inner)
            if suggestion:
                print(
                    f"  suggested pattern: "
                    f"{year_dir.replace(year_dir, '{year}')}/{suggestion}"
                )
        except Exception as exc:  # noqa: BLE001
            print(f"  descent failed: {exc}")
        return

    suggestion = _suggest_pattern(sample)
    if suggestion:
        print(f"  suggested pattern: {suggestion}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="How many filenames to print per directory (default: 10).",
    )
    args = parser.parse_args()

    catalog = Catalog()
    missing = [t for t in TARGETS if t not in catalog.datasets]
    if missing:
        print(
            f"warning: target datasets not in catalog: {missing}",
            file=sys.stderr,
        )

    with FTP(FTP_HOST) as ftp:  # nosec B321
        ftp.login()
        for ds_key in TARGETS:
            if ds_key not in catalog.datasets:
                continue
            dataset = catalog.datasets[ds_key]
            ftp_base = dataset.ftp_bases[dataset.default_format]
            _probe_directory(ftp, ds_key, ftp_base, args.limit)

    print(
        "\nNote: catalog file_patterns for these datasets are flagged "
        "as provisional. Compare the listings above against the "
        "`file_patterns:` entries in `chc_data_catalog.yaml` and "
        "correct as needed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
