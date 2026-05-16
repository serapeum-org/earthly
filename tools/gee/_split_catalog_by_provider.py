"""One-shot migration: split the monolithic GEE catalog into per-provider files.

Reads `src/earthlens/gee/gee_data_catalog.yaml` (the legacy single-file
layout) and emits `src/earthlens/gee/catalog/<PROVIDER>.yaml` for every
top-level asset-id prefix, plus `_index.yaml` carrying the
`available_datasets:` list. Per-stanza text is sliced from the source
verbatim (preserving comments, ordering and formatting); only the
top-level `datasets:` / `available_datasets:` headers are re-emitted.

Run once from the repo root, then delete the monolithic YAML.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src" / "earthlens" / "gee" / "gee_data_catalog.yaml"
DST_DIR = REPO / "src" / "earthlens" / "gee" / "catalog"


def _provider_of(asset_id: str) -> str:
    """Map an EE asset id to its per-provider file stem.

    `projects/foo/bar` and any user-uploaded path lives in `community.yaml`;
    everything else groups under its first path segment uppercased (most
    are already uppercase: `MODIS`, `COPERNICUS`, `LANDSAT`, …).
    """
    if asset_id.startswith("projects/"):
        return "community"
    return asset_id.split("/", 1)[0]


_AVAILABLE_ITEM_RE = re.compile(r"^  - (?P<asset>\S+)\s*$", re.MULTILINE)


def _slice_blocks(text: str) -> tuple[list[str], str, str]:
    """Return `(available_datasets, datasets_body, suffix)`.

    `available_datasets` is the verbatim list of asset ids from the
    `available_datasets:` block (preserving its native order — the
    refresh tool emits it in EE STAC walk order). `datasets_body` is
    the text inside the `datasets:` block (each stanza is a top-level
    `  <ASSET_ID>:` entry). `suffix` is anything after the `datasets:`
    block.
    """
    avail_m = re.search(r"^available_datasets:\s*\n", text, re.MULTILINE)
    ds_m = re.search(r"^datasets:\s*\n", text, re.MULTILINE)
    if not ds_m:
        raise SystemExit("monolithic catalog has no 'datasets:' block")
    if not avail_m:
        raise SystemExit("monolithic catalog has no 'available_datasets:' block")
    if avail_m.start() >= ds_m.start():
        raise SystemExit("'available_datasets:' must precede 'datasets:'")

    avail_block = text[avail_m.end() : ds_m.start()]
    available = [m.group("asset") for m in _AVAILABLE_ITEM_RE.finditer(avail_block)]

    body_start = ds_m.end()
    # `datasets:` runs to EOF in the current catalog; defend against a
    # hypothetical trailing top-level key by stopping at the first
    # subsequent zero-indent, non-blank, non-comment line.
    suffix_match = re.search(r"^[A-Za-z_]", text[body_start:], re.MULTILINE)
    body_end = body_start + suffix_match.start() if suffix_match else len(text)
    return available, text[body_start:body_end], text[body_end:]


# Each curated stanza begins with `  <ASSET_ID>:` at exactly two-space
# indent, where ASSET_ID may contain `/`, letters, digits, `-`, `_`, `.`.
# Sibling lines under a stanza are indented four or more spaces, so a
# fresh two-space-indented line reliably marks the next stanza.
_STANZA_RE = re.compile(r"^  (?P<asset>[A-Za-z0-9_./\-]+):\s*$", re.MULTILINE)


def _split_stanzas(datasets_body: str) -> list[tuple[str, str]]:
    """Slice a `datasets:` body into `(asset_id, stanza_text)` pairs."""
    matches = list(_STANZA_RE.finditer(datasets_body))
    if not matches:
        raise SystemExit("no dataset stanzas found in 'datasets:' block")
    pairs: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(datasets_body)
        pairs.append((m.group("asset"), datasets_body[start:end]))
    return pairs


def main() -> int:
    if not SRC.exists():
        print(f"source not found: {SRC}", file=sys.stderr)
        return 1

    text = SRC.read_text(encoding="utf-8")
    available, datasets_body, suffix = _slice_blocks(text)
    if suffix.strip():
        print(
            f"warning: ignoring trailing content after 'datasets:' block: "
            f"{suffix[:200]!r}",
            file=sys.stderr,
        )

    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for asset_id, stanza in _split_stanzas(datasets_body):
        grouped[_provider_of(asset_id)].append((asset_id, stanza))

    DST_DIR.mkdir(parents=True, exist_ok=True)
    for old in DST_DIR.glob("*.yaml"):
        old.unlink()

    for provider, stanzas in sorted(grouped.items()):
        out = DST_DIR / f"{provider}.yaml"
        parts = [
            f"# Auto-grouped slice of the GEE catalog: {provider}.\n",
            f"# {len(stanzas)} dataset(s). Edit in place; the loader merges every\n",
            "# *.yaml file in this directory into one Catalog at import time.\n",
            "\n",
            "datasets:\n",
        ]
        for _, stanza in sorted(stanzas, key=lambda p: p[0]):
            parts.append(stanza)
            if not stanza.endswith("\n"):
                parts.append("\n")
        out.write_text("".join(parts), encoding="utf-8")

    index = DST_DIR / "_index.yaml"
    lines = [
        "# Informational index: every Earth Engine asset id discovered by\n",
        "# tools/gee/refresh_gee_catalog.py during its last STAC walk, in walk\n",
        "# order. The curated subset lives in the per-provider *.yaml files in\n",
        "# this directory; edit those, not this list (the refresh tool rewrites\n",
        "# this file in place).\n",
        "\n",
        "available_datasets:\n",
    ]
    for aid in available:
        lines.append(f"  - {aid}\n")
    index.write_text("".join(lines), encoding="utf-8")

    sizes = {p: len(s) for p, s in grouped.items()}
    print(
        f"wrote {len(grouped)} provider files + _index.yaml "
        f"({sum(sizes.values())} datasets) under {DST_DIR}"
    )
    for p, n in sorted(sizes.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {n:4d}  {p}.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
