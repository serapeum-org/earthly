"""One-off migration: replace ``user_uploaded: true`` with ``source: ...`` (L2).

Walks every ``catalog/*.yaml`` (except ``_index.yaml``) and rewrites each
stanza that carries ``user_uploaded: true`` into one carrying
``source: community`` (for ``projects/...`` assets) or
``source: republished`` (anything else marked uploaded). ``ee_native``
stanzas (the majority) keep their terse default — no `source:` line.

Idempotent: a stanza that already has a `source:` line is left alone.

Not part of the installed package — bulk-curation helper only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _catalog_io import STANZA_RE  # noqa: E402

CATALOG_DIR = Path("src/earthlens/gee/catalog")

_USER_UPLOADED_LINE = re.compile(r"^    user_uploaded:\s*true\s*$\n", re.MULTILINE)
_HAS_SOURCE = re.compile(r"^    source:\s*\S+\s*$", re.MULTILINE)


def _rewrite_file(path: Path) -> tuple[int, int]:
    text = path.read_text(encoding="utf-8")
    matches = list(STANZA_RE.finditer(text))
    if not matches:
        return 0, 0

    out_parts: list[str] = [text[: matches[0].start()]]
    replaced = community = republished = 0

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        stanza = text[m.start():end]
        asset_id = m.group("asset")
        if _HAS_SOURCE.search(stanza):
            out_parts.append(stanza)
            continue
        if not _USER_UPLOADED_LINE.search(stanza):
            out_parts.append(stanza)
            continue
        source = "community" if asset_id.startswith("projects/") else "republished"
        new_stanza = _USER_UPLOADED_LINE.sub(f"    source: {source}\n", stanza, count=1)
        out_parts.append(new_stanza)
        replaced += 1
        if source == "community":
            community += 1
        else:
            republished += 1

    new_text = "".join(out_parts)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return replaced, community


def main() -> int:
    files = sorted(p for p in CATALOG_DIR.glob("*.yaml") if p.name != "_index.yaml")
    total = 0
    for path in files:
        n, _ = _rewrite_file(path)
        if n:
            print(f"  {n:4d}  {path.name}")
        total += n
    print(f"rewrote {total} stanzas across {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
