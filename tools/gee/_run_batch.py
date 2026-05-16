"""Run one curate-batch end-to-end: fetch stanzas, compact, append, validate.

Usage::

    pixi run -e dev python tools/gee/_run_batch.py id1 id2 id3 ...

Pipes the requested asset ids through ``refresh_gee_catalog.py
--with-bands`` (with PYTHONIOENCODING=utf-8 to keep Unicode safe), runs
the result through ``_compact_stanzas`` to normalise reducers / line
endings / quote-truncation, appends each stanza to the per-category
file under ``src/earthlens/gee/catalog/`` chosen by
``_catalog_io.category_for``, and loads the catalog to verify it still
parses (raises if not).

Skips ids that are already in the curated ``datasets`` block.

Not part of the installed package — bulk-curation helper only.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "tools/gee")
from _catalog_io import CATALOG_DIR, category_for  # noqa: E402

_STANZA_RE = re.compile(r"^  (?P<asset>[A-Za-z0-9_./\-]+):\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^    title:\s*(.+?)\s*$", re.MULTILINE)


def _title_of(stanza: str) -> str:
    m = _TITLE_RE.search(stanza)
    if not m:
        return ""
    t = m.group(1).strip()
    if t.startswith("'") and t.endswith("'"):
        t = t[1:-1].replace("''", "'")
    elif t.startswith('"') and t.endswith('"'):
        t = t[1:-1]
    return t


def _split_stanzas(body: str) -> list[tuple[str, str]]:
    matches = list(_STANZA_RE.finditer(body))
    pairs: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        pairs.append((m.group("asset"), body[m.start():end]))
    return pairs


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

    per_category: dict[str, list[str]] = defaultdict(list)
    for asset_id, stanza in _split_stanzas(compact):
        per_category[category_for(asset_id, _title_of(stanza))].append(stanza)

    for category, stanzas in per_category.items():
        target = CATALOG_DIR / f"{category}.yaml"
        if target.exists():
            existing_bytes = target.read_bytes()
            if not existing_bytes.endswith(b"\n"):
                existing_bytes += b"\n"
        else:
            header = (
                f"# Auto-grouped slice of the GEE catalog: {category}.\n"
                f"# Created by _run_batch.py. Edit in place.\n\n"
                f"datasets:\n"
            ).encode("utf-8")
            existing_bytes = header
        target.write_bytes(existing_bytes + b"".join(s.encode("utf-8") for s in stanzas))

    # Re-validate
    from importlib import reload
    import earthlens.gee.catalog as _cat_mod
    reload(_cat_mod)
    _cat_mod.clear_catalog_cache()
    cat2 = _cat_mod.Catalog()
    print(f"appended {len(fresh)} stanzas — total curated: {len(cat2.datasets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run(sys.argv[1:]))
