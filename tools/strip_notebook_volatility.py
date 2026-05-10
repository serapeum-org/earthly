"""Strip volatile per-cell execution metadata from Jupyter notebooks.

Removes the per-cell `ExecuteTime` and `execution` metadata blocks
that change on every re-run (`iopub.*` and `shell.*` timestamps) so
the diff after re-running a notebook only reflects content changes,
not metadata churn. **Cell outputs** (figures, dataframes, streams)
and `execution_count` numbering are kept, so the notebook still
renders with full results on GitHub / mkdocs.

Output-stream volatility — log lines containing CDS request IDs,
download URLs, multiurl progress timestamps — is **not** stripped,
because doing so would require parsing every stream output and is
brittle to upstream log-format changes. Live with it; it's smaller
churn than the per-cell metadata block.

Usage::

    pixi run -e dev python tools/strip_notebook_volatility.py docs/examples/*.ipynb
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Per-cell `metadata` keys whose values are pure execution
# timestamps. Adding a key here strips it from every cell.
_VOLATILE_METADATA_KEYS: tuple[str, ...] = ("ExecuteTime", "execution")


def _strip_one(path: Path) -> bool:
    """Strip volatile metadata from `path` in place. Return True if changed."""
    text = path.read_text(encoding="utf-8")
    nb: dict[str, Any] = json.loads(text)
    changed = False
    for cell in nb.get("cells", []):
        meta = cell.get("metadata", {})
        for key in _VOLATILE_METADATA_KEYS:
            if key in meta:
                del meta[key]
                changed = True
    if changed:
        # nbformat writes notebooks with 1-space indent and a trailing
        # newline; matching that format keeps the diff minimal when
        # the file is re-saved by JupyterLab later.
        path.write_text(
            json.dumps(nb, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return changed


def main() -> int:
    """Walk every notebook on the command line and strip volatility metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notebooks", nargs="+", type=Path)
    args = parser.parse_args()
    total_changed = 0
    for path in args.notebooks:
        if _strip_one(path):
            print(f"stripped: {path}")
            total_changed += 1
        else:
            print(f"clean:    {path}")
    print(f"\n{total_changed}/{len(args.notebooks)} notebooks updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
