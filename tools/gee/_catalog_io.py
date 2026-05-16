"""Shared helpers for the GEE bulk-curation scripts.

Single home for the YAML stanza / title regexes, the
asset-id → per-category-file resolver, and the in-place stanza-span
locator used by `_hydrate_placeholders` / `_run_batch` / `add-ids` /
`_migrate_source` / `_recategorize_catalog`. Centralising these
prevents the regex drifting out of sync across the four-five tools
that all need to slice the same YAML shape (M3 in
``planning/code-quality-cleanup.md``).

Not part of the installed package.
"""

from __future__ import annotations

import re
from pathlib import Path

CATALOG_DIR = Path("src/earthlens/gee/catalog")

# Matches the head of every curated dataset stanza: 2-space indent
# (so the top-level `datasets:` header line itself never matches), a
# slug-shaped asset id, and the trailing colon.
STANZA_RE = re.compile(r"^  (?P<asset>[A-Za-z0-9_./\-]+):\s*$", re.MULTILINE)

# Matches `    title: <value>` at the standard 4-space stanza-body
# indent. The value is captured raw (still potentially quoted) — use
# :func:`title_of` to YAML-unquote.
TITLE_RE = re.compile(r"^    title:\s*(.+?)\s*$", re.MULTILINE)


def split_stanzas(text: str) -> list[tuple[str, str]]:
    """Slice catalog YAML `text` into `(asset_id, stanza_text)` pairs.

    Works on either a stripped `datasets:` body or the full file text
    (the regex anchors on 2-space indent so the `datasets:` header
    line itself doesn't match). Stanza text spans from `<id>:` to the
    next sibling stanza head or end of input.
    """
    matches = list(STANZA_RE.finditer(text))
    pairs: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        pairs.append((m.group("asset"), text[m.start():end]))
    return pairs


def title_of(stanza: str) -> str:
    """Return the YAML-unquoted `title:` value of one stanza, or `""`."""
    m = TITLE_RE.search(stanza)
    if not m:
        return ""
    t = m.group(1).strip()
    if t.startswith("'") and t.endswith("'"):
        t = t[1:-1].replace("''", "'")
    elif t.startswith('"') and t.endswith('"'):
        t = t[1:-1]
    return t


def category_for(asset_id: str, title: str = "") -> str:
    """Return the per-category file stem for `asset_id`.

    Thin wrapper around `_recategorize_catalog._categorise` — imported
    lazily inside the function body so `_recategorize_catalog` can
    safely import this module at its own top level without a cycle.
    `title` is optional (helps title-keyword fallbacks land correctly).
    """
    # Lazy import: see module docstring; breaks an otherwise circular
    # `_catalog_io <-> _recategorize_catalog` dependency.
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from _recategorize_catalog import _categorise  # noqa: E402
    return _categorise(asset_id, title)


def file_for(asset_id: str, title: str = "") -> Path:
    """Return the path of the per-category YAML that owns / should own `asset_id`.

    Falls back to scanning every existing `*.yaml` in `CATALOG_DIR` for
    an existing `^  <asset_id>:` stanza so that already-curated entries
    are edited in place even if the categoriser would now route them
    elsewhere.
    """
    for path in sorted(CATALOG_DIR.glob("*.yaml")):
        if path.name == "_index.yaml":
            continue
        if find_stanza_span(path.read_text(encoding="utf-8"), asset_id):
            return path
    return CATALOG_DIR / f"{category_for(asset_id, title)}.yaml"


def find_stanza_span(text: str, asset_id: str) -> tuple[int, int] | None:
    """Return `(start, end)` byte offsets of `asset_id:`'s stanza, or None.

    `start` is the position of the `  <asset_id>:` line; `end` is the
    position of the next sibling stanza head or end of input.
    """
    key = re.escape(asset_id)
    head = re.search(rf"^  {key}:\s*$", text, re.MULTILINE)
    if not head:
        return None
    next_head = STANZA_RE.search(text, head.end())
    end = next_head.start() if next_head else len(text)
    return head.start(), end
