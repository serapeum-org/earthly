"""Tiny shared helpers for the bulk-curation scripts.

Resolves an Earth Engine asset id to the per-category YAML file under
``src/earthlens/gee/catalog/`` that holds (or should hold) its stanza
(via the rules baked into ``_recategorize_catalog.py``), with a fallback
that walks every file looking for an existing stanza so already-curated
entries are always edited in place no matter what bucket they live in.

Used by ``_run_batch.py`` / ``_hydrate_placeholders.py`` /
``_migrate_terms_to_license.py``. Not part of the installed package.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _recategorize_catalog import _categorise  # noqa: E402

CATALOG_DIR = Path("src/earthlens/gee/catalog")


def category_for(asset_id: str, title: str = "") -> str:
    """Return the per-category file stem for `asset_id`.

    Wraps :func:`_recategorize_catalog._categorise` so curation scripts
    don't have to import it directly. `title` is optional (helps the
    title-keyword fallbacks land correctly); pass an empty string when
    you don't have it.
    """
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


_ASSET_HEAD = re.compile(r"^  [A-Za-z0-9_./\-]+:\s*$", re.MULTILINE)


def find_stanza_span(text: str, asset_id: str) -> tuple[int, int] | None:
    """Return `(start, end)` byte offsets of `asset_id:`'s stanza, or None.

    `start` is the position of the `  <asset_id>:` line; `end` is the
    position of the next sibling stanza (`  <next_id>:` at indent 2) or
    end-of-file.
    """
    key = re.escape(asset_id)
    head = re.search(rf"^  {key}:\s*$", text, re.MULTILINE)
    if not head:
        return None
    next_head = _ASSET_HEAD.search(text, head.end())
    end = next_head.start() if next_head else len(text)
    return head.start(), end
