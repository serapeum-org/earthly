"""Tiny shared helpers for the bulk-curation scripts.

Resolves an Earth Engine asset id to the per-provider YAML file under
``src/earthlens/gee/catalog/`` that holds (or should hold) its stanza,
and provides a stanza-level edit helper that locates an existing stanza
inside one file and replaces it in place.

Used by ``_run_batch.py`` / ``_hydrate_placeholders.py`` /
``_migrate_terms_to_license.py``. Not part of the installed package.
"""

from __future__ import annotations

import re
from pathlib import Path

CATALOG_DIR = Path("src/earthlens/gee/catalog")


def provider_of(asset_id: str) -> str:
    """Return the per-provider file stem for `asset_id` (matches the split tool)."""
    if asset_id.startswith("projects/"):
        return "community"
    return asset_id.split("/", 1)[0]


def file_for(asset_id: str) -> Path:
    """Return the path of the per-provider YAML that owns `asset_id`."""
    return CATALOG_DIR / f"{provider_of(asset_id)}.yaml"


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
