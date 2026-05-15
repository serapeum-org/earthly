"""Run one curate-batch end-to-end: fetch stanzas, compact, append, validate.

Usage::

    pixi run -e dev python tools/gee/_run_batch.py id1 id2 id3 ...

Pipes the requested asset ids through ``refresh_gee_catalog.py
--with-bands`` (with PYTHONIOENCODING=utf-8 to keep Unicode safe), runs
the result through ``_compact_stanzas`` to normalise reducers / line
endings / quote-truncation, appends to ``gee_data_catalog.yaml``, and
loads the catalog to verify it still parses (raises if not).

Skips ids that are already in the curated ``datasets`` block.

Not part of the installed package — bulk-curation helper only.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

CATALOG_PATH = Path("src/earthlens/gee/gee_data_catalog.yaml")


def _run(ids: list[str]) -> int:
    sys.path.insert(0, "tools/gee")
    from _compact_stanzas import _process

    from earthlens.gee import Catalog

    cat = Catalog()
    existing = set(cat.datasets)
    fresh = [i for i in ids if i not in existing]
    if not fresh:
        print("nothing to add — all ids already curated")
        return 0
    skipped = sorted(set(ids) - set(fresh))
    if skipped:
        print(f"skipping already-curated: {skipped}")

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "tools/gee/refresh_gee_catalog.py", "--dry-run", "--with-bands", *fresh],
        capture_output=True, env=env, check=False,
    )
    if proc.returncode != 0:
        sys.stderr.buffer.write(proc.stderr)
        return proc.returncode
    raw = proc.stdout.decode("utf-8")
    chunks = raw.split("# ---- paste under")
    body = "".join(c.split("\n", 1)[1] if "\n" in c else "" for c in chunks[1:])
    compact = _process(io.StringIO(body))

    existing_bytes = CATALOG_PATH.read_bytes()
    if not existing_bytes.endswith(b"\n"):
        existing_bytes += b"\n"
    CATALOG_PATH.write_bytes(existing_bytes + b"\n" + compact.encode("utf-8"))

    # Re-validate
    from importlib import reload
    import earthlens.gee.catalog as _cat_mod
    reload(_cat_mod)
    cat2 = _cat_mod.Catalog()
    print(f"appended {len(fresh)} stanzas — total curated: {len(cat2.datasets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run(sys.argv[1:]))
