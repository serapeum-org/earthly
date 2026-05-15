"""Walk the CHC FTP server and refresh the catalog coverage report.

The CHC analogue of `tools/gee/refresh_gee_catalog.py` and
`tools/ecmwf/refresh_available_datasets.py`. Recursively walks
`pub/org/chc/products/` on `data.chc.ucsb.edu`, identifies every
*product directory* (a directory whose children are data files or
year-named subdirs), and produces a coverage report against the
current `chc_data_catalog.yaml`:

* **discovered**: product paths the FTP walk found.
* **in-yaml**: distinct `ftp_bases` paths declared in the catalog.
* **only-on-ftp**: product paths the walk found but the YAML does not
  reference — candidates to promote into `available_datasets:` /
  `datasets:`.
* **only-in-yaml**: catalog paths that no FTP walk node touched.
  Either the walk did not descend deep enough (rare; bump
  `--max-depth`) or the catalog points at a stale / removed product.

Unlike the GEE / ECMWF refreshers, this script **does not** rewrite
`available_datasets:` automatically. The CHC catalog's index uses
hand-curated slugs (`global-daily`, `africa-pentad`, ...) rather than
the raw FTP paths, and slug derivation is a human-curation task. The
report's job is to surface the deltas; the maintainer applies them.

Walk results are cached at `tools/chc/_discovered_paths.json` so a
re-run is offline. Pass `--no-cache` to force a fresh walk,
`--max-depth N` to control how far the walk descends (default 6), and
`--root <ftp-path>` to start the walk somewhere other than
`pub/org/chc/products/`.

Run with:

    pixi run -e dev python tools/chc/refresh_chc_catalog.py
    pixi run -e dev python tools/chc/refresh_chc_catalog.py --no-cache
    pixi run -e dev python tools/chc/refresh_chc_catalog.py --max-depth 4

Not part of the installed package.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from ftplib import FTP, error_perm  # nosec B402  # noqa: S402
from pathlib import Path

# Allow running directly from the repo without an editable install.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
from earthlens.chc import Catalog  # noqa: E402

FTP_HOST: str = "data.chc.ucsb.edu"
DEFAULT_ROOT: str = "pub/org/chc/products/"
CACHE_PATH: Path = Path(__file__).parent / "_discovered_paths.json"

# Suffixes that mark a "data file" on the CHC FTP.
_DATA_SUFFIXES: tuple[str, ...] = (
    ".tif", ".tif.gz", ".tiff", ".nc", ".nc4", ".bil", ".bil.gz", ".bin",
    ".cog", ".png", ".grb", ".grib",
)
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _is_year_dir(name: str) -> bool:
    return bool(_YEAR_RE.fullmatch(name))


def _is_data_file(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(suf) for suf in _DATA_SUFFIXES)


def _classify_listing(entries: list[str]) -> tuple[bool, bool]:
    """Return `(has_data_files, has_year_subdirs)` for one listing.

    Anything else is treated as an intermediate directory to descend
    into.
    """
    has_data = any(_is_data_file(e) for e in entries)
    has_years = any(_is_year_dir(e) for e in entries)
    return has_data, has_years


def _walk(
    ftp: FTP, root: str, max_depth: int
) -> list[str]:
    """BFS-walk `root` and return every discovered product directory.

    A product directory is one whose listing contains data files
    (`.tif`, `.nc`, `.bil`, ...) or year-named subdirs. Intermediate
    directories are descended until either a product is reached or
    `max_depth` levels below `root`.
    """
    discovered: list[str] = []
    queue: list[tuple[str, int]] = [(root, 0)]
    while queue:
        path, depth = queue.pop(0)
        try:
            ftp.cwd("/")
            ftp.cwd(path)
            entries = sorted(ftp.nlst())
        except error_perm as exc:
            print(f"  cwd {path}: {exc}", file=sys.stderr)
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  walk {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue

        has_data, has_years = _classify_listing(entries)
        if has_data or has_years:
            discovered.append(path)
            continue
        if depth >= max_depth:
            print(
                f"  depth cap at {path} ({len(entries)} entries, "
                "none data/year-subdir)",
                file=sys.stderr,
            )
            continue
        # Otherwise descend into each non-leaf-looking child.
        for entry in entries:
            if "." in entry:
                # Likely a file we did not recognise (e.g. README.txt);
                # skip rather than try to cwd into it.
                continue
            child = f"{path.rstrip('/')}/{entry}/"
            queue.append((child, depth + 1))
    return sorted(discovered)


def _yaml_ftp_paths(catalog: Catalog) -> set[str]:
    """Return every distinct `ftp_bases` value declared in the catalog."""
    paths: set[str] = set()
    for dataset in catalog.datasets.values():
        for base in dataset.ftp_bases.values():
            paths.add(base.rstrip("/") + "/")
    return paths


def _load_cache() -> dict[str, list[str]]:
    if not CACHE_PATH.is_file():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_cache(payload: dict[str, list[str]]) -> None:
    CACHE_PATH.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore the on-disk cache and walk live.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=6,
        help="How far to descend from the root (default: 6).",
    )
    parser.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        help=f"FTP path to walk from (default: {DEFAULT_ROOT!r}).",
    )
    args = parser.parse_args()

    catalog = Catalog()
    yaml_paths = _yaml_ftp_paths(catalog)

    cache = {} if args.no_cache else _load_cache()
    cache_key = f"{args.root}|depth={args.max_depth}"
    if cache_key in cache:
        discovered = cache[cache_key]
        print(f"Using cached walk for {cache_key} ({len(discovered)} dirs)")
    else:
        print(f"Walking FTP {args.root} (max depth {args.max_depth}) ...")
        with FTP(FTP_HOST) as ftp:  # nosec B321
            ftp.login()
            discovered = _walk(ftp, args.root.rstrip("/"), args.max_depth)
        cache[cache_key] = discovered
        _save_cache(cache)

    discovered_norm = {p.rstrip("/") + "/" for p in discovered}
    only_on_ftp = sorted(discovered_norm - yaml_paths)
    only_in_yaml = sorted(yaml_paths - discovered_norm)
    overlap = sorted(discovered_norm & yaml_paths)

    print(f"\nFTP walk: {len(discovered_norm)} product directories")
    print(f"YAML:     {len(yaml_paths)} distinct ftp_bases entries")
    print(f"Overlap:  {len(overlap)}")
    print(f"  -> only-on-ftp (candidates to promote): {len(only_on_ftp)}")
    print(f"  -> only-in-yaml (catalog drift):        {len(only_in_yaml)}")

    if only_on_ftp:
        print("\nonly-on-ftp:")
        for path in only_on_ftp[:80]:
            print(f"  - {path}")
        if len(only_on_ftp) > 80:
            print(f"  ... and {len(only_on_ftp) - 80} more")

    if only_in_yaml:
        print("\nonly-in-yaml:")
        for path in only_in_yaml[:80]:
            print(f"  - {path}")
        if len(only_in_yaml) > 80:
            print(f"  ... and {len(only_in_yaml) - 80} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
