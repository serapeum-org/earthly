"""One-off migration: drop ``description: Band <id>`` stub lines from the catalog.

Walks every ``catalog/*.yaml`` (except ``_index.yaml``) and removes lines of
the form::

      <indent>description: Band <id>

when ``<id>`` matches the band key immediately above. These stubs were
emitted by ``_hydrate_placeholders.py`` for assets where Earth Engine
returned a band name but no description; they carry no information beyond
the band id itself. After this script runs, the `Band.description` field
is `None` for those bands (the model accepts that as of M4).

Idempotent: re-running on an already-cleaned tree is a no-op.

Not part of the installed package — bulk-curation helper only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CATALOG_DIR = Path("src/earthlens/gee/catalog")

# Match a band header (`      <band_id>:` at 6-space indent) followed
# immediately by its `description: Band <band_id>` stub line. `(?m)`
# makes ^/$ work per-line.
_PATTERN = re.compile(
    r"(?m)^(?P<head>      (?P<band_id>[A-Za-z0-9_\-]+):\s*\n)"
    r"        description:\s*Band\s+(?P=band_id)\s*\n"
)


def main() -> int:
    files = sorted(p for p in CATALOG_DIR.glob("*.yaml") if p.name != "_index.yaml")
    total_dropped = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        new_text, n = _PATTERN.subn(r"\g<head>", text)
        if n:
            path.write_text(new_text, encoding="utf-8")
            print(f"  {n:4d}  {path.name}")
            total_dropped += n
    print(f"dropped {total_dropped} stub description lines across {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
