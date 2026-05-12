"""Refresh the ``available_datasets`` index in the GEE data catalog.

Walks the Earth Engine STAC catalog
(``https://storage.googleapis.com/earthengine-stac/catalog/catalog.json``),
collects every dataset asset id, groups them by provider, and rewrites
the ``available_datasets:`` block in
``src/earthlens/gee/gee_data_catalog.yaml`` in place. The curated
``datasets:`` map and the schema-header comments are preserved verbatim.

Optionally, ``--with-bands <asset_id> ...`` also prints a ready-to-paste
``datasets.<asset_id>:`` stanza for each given id, built from that
dataset's STAC document (title, ``ee_type``, ``cadence``, ``extent``,
per-band ``units`` / ``scale`` / ``offset`` / ``wavelength`` /
``min`` / ``max``). The ``default_reducer`` cannot be derived from
STAC, so it is emitted as ``median`` with a ``# TODO: verify`` comment.

This is the GEE analogue of ``tools/refresh_available_datasets.py``.
Run before a release:

    pixi run -e dev python tools/refresh_gee_catalog.py
    pixi run -e dev python tools/refresh_gee_catalog.py --dry-run
    pixi run -e dev python tools/refresh_gee_catalog.py --with-bands USGS/SRTMGL1_003

Exits 0 on success, 1 on any HTTP / parse error. Not part of the
installed package.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _gee_stac import collect_collection_ids, fetch_collection_stac  # noqa: E402

CATALOG_PATH = Path("src/earthlens/gee/gee_data_catalog.yaml")
_GLOBAL_BBOX = [-180, -90, 180, 90]


def categorise(ids: list[str]) -> list[tuple[str, list[str]]]:
    """Group asset ids by their provider (first path segment), sorted.

    Args:
        ids: Asset ids, e.g. ``["LANDSAT/LC09/C02/T1_L2", "MODIS/061/MOD13Q1"]``.

    Returns:
        ``[(provider, [ids...]), ...]`` sorted by provider, ids sorted within.
    """
    groups: dict[str, list[str]] = {}
    for asset_id in ids:
        groups.setdefault(asset_id.split("/", 1)[0], []).append(asset_id)
    return [(provider, sorted(groups[provider])) for provider in sorted(groups)]


def render_available_datasets_block(grouped: list[tuple[str, list[str]]]) -> str:
    """Render the ``available_datasets:`` YAML block.

    Args:
        grouped: Output of :func:`categorise`.

    Returns:
        The block as a string, ending with a newline; e.g.::

            available_datasets:
              # ----- LANDSAT -----
              - LANDSAT/LC08/C02/T1_L2
              - LANDSAT/LC09/C02/T1_L2
              # ----- MODIS -----
              - MODIS/061/MOD13Q1
    """
    lines = ["available_datasets:"]
    for provider, asset_ids in grouped:
        lines.append(f"  # ----- {provider} -----")
        lines.extend(f"  - {asset_id}" for asset_id in asset_ids)
    return "\n".join(lines) + "\n"


def splice_into_yaml(text: str, block: str) -> str:
    """Replace the ``available_datasets:`` block in `text` with `block`.

    Args:
        text: The full ``gee_data_catalog.yaml`` contents.
        block: The replacement block (from :func:`render_available_datasets_block`).

    Returns:
        The updated YAML text. The curated ``datasets:`` map and the
        header comments are untouched.

    Raises:
        ValueError: If `text` has no ``available_datasets:`` block
            followed by a top-level ``datasets:`` key.
    """
    pattern = re.compile(r"(?ms)^available_datasets:.*?(?=^datasets:)")
    if not pattern.search(text):
        raise ValueError(
            "could not find an 'available_datasets:' block ending at a "
            "top-level 'datasets:' key in the catalog YAML"
        )
    return pattern.sub(block + "\n", text)


def _gsd_to_metres(gsd) -> float | None:
    """Normalise a STAC band ``gsd`` (scalar or list) to a single metre value."""
    if gsd is None:
        return None
    if isinstance(gsd, (list, tuple)):
        values = [v for v in gsd if isinstance(v, (int, float))]
        return float(min(values)) if values else None
    return float(gsd) if isinstance(gsd, (int, float)) else None


def _first_line(text) -> str | None:
    """Return the first non-empty line of `text`, trimmed, or ``None``."""
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def stanza_for(asset_id: str) -> str:
    """Build a ready-to-paste ``datasets.<asset_id>:`` YAML stanza.

    Fetches the dataset's STAC document and transcribes the fields the
    catalog schema uses. ``default_reducer`` is emitted as ``median``
    with a ``# TODO: verify`` comment (STAC does not carry it).

    Args:
        asset_id: An Earth Engine asset id.

    Returns:
        The YAML stanza (2-space indented, ready to paste under
        ``datasets:``), ending with a newline.

    Raises:
        ValueError: If the dataset's STAC document cannot be fetched.
    """
    doc = fetch_collection_stac(asset_id)
    if doc is None:
        raise ValueError(f"could not fetch STAC for {asset_id!r}")
    ee_type = doc.get("gee:type", "image_collection")
    interval = doc.get("gee:interval") or {}
    extent = doc.get("extent", {})
    temporal = (extent.get("temporal", {}).get("interval") or [[None, None]])[0]
    spatial_bbox = (extent.get("spatial", {}).get("bbox") or [None])[0]
    summaries = doc.get("summaries", {})
    eo_bands = summaries.get("eo:bands") or []
    gsds = [_gsd_to_metres(b.get("gsd")) for b in eo_bands]
    spatial_res = min((g for g in gsds if g is not None), default=None)
    providers = doc.get("providers") or []

    lines = [f"  {asset_id}:"]
    title = _first_line(doc.get("title")) or asset_id
    lines.append(f"    title: {_yaml_str(title)}")
    if providers:
        lines.append(f"    provider: {_yaml_str(providers[0].get('name', '') or '')}")
    lines.append(f"    ee_type: {ee_type}")
    if interval.get("interval") and interval.get("unit"):
        lines.append(f"    cadence: {{ interval: {interval['interval']}, unit: {interval['unit']} }}")
    if spatial_res is not None:
        lines.append(f"    spatial_resolution: {_num(spatial_res)}")
    lines.append("    extent:")
    start = (temporal[0] or "")[:10] if temporal and temporal[0] else "1970-01-01"
    lines.append(f'      start_date: "{start}"')
    lines.append("      end_date: null            # TODO: confirm (null = continuously updated)")
    if spatial_bbox and list(spatial_bbox) != _GLOBAL_BBOX:
        lines.append(f"      bbox: {list(spatial_bbox)}")
    lines.append("    default_reducer: median     # TODO: verify (median for optical scenes, mean for rates/fields, mosaic for tiled/annual maps)")
    if doc.get("gee:terms_of_use"):
        lines.append(f"    terms: {_yaml_str(_first_line(doc['gee:terms_of_use']) or '')}")
    if doc.get("gee:user_uploaded"):
        lines.append("    user_uploaded: true")
    lines.append("    bands:")
    for band in eo_bands:
        lines.append(f"      {band.get('name')}:")
        lines.append(f"        description: {_yaml_str(_first_line(band.get('description')) or band.get('name', ''))}")
        if band.get("gee:units"):
            lines.append(f"        units: {_yaml_str(band['gee:units'])}")
        if band.get("gee:scale") is not None:
            lines.append(f"        scale: {_num(band['gee:scale'])}")
        if band.get("gee:offset") is not None:
            lines.append(f"        offset: {_num(band['gee:offset'])}")
        if band.get("center_wavelength") is not None:
            lines.append(f"        wavelength: {_num(band['center_wavelength'])}")
        rng = summaries.get(band.get("name"))
        if isinstance(rng, dict):
            if rng.get("minimum") is not None:
                lines.append(f"        min: {_num(rng['minimum'])}")
            if rng.get("maximum") is not None:
                lines.append(f"        max: {_num(rng['maximum'])}")
            if rng.get("gee:estimated_range"):
                lines.append("        estimated_range: true")
    return "\n".join(lines) + "\n"


def _yaml_str(value: str) -> str:
    """Quote a string for YAML if it needs quoting; else return it bare."""
    text = str(value).strip()
    if not text or re.search(r'[:#\[\]{}",&*!|>%@`]', text) or text[0] in "?-" or ": " in text:
        return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return text


def _num(value) -> str:
    """Render a number compactly (drop a trailing ``.0`` only when harmless)."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value) if isinstance(value, float) else str(value)


def main() -> int:
    """Refresh ``available_datasets:`` (and optionally print ``--with-bands`` stanzas)."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", type=Path, default=CATALOG_PATH, help="path to gee_data_catalog.yaml")
    parser.add_argument("--dry-run", action="store_true", help="print the new available_datasets: block instead of writing")
    parser.add_argument("--with-bands", nargs="+", metavar="ASSET_ID", help="also print a ready-to-paste datasets: stanza for each id")
    parser.add_argument("-v", "--verbose", action="store_true", help="print STAC-walk progress")
    args = parser.parse_args()

    try:
        ids = collect_collection_ids(verbose=args.verbose)
    except Exception as exc:  # noqa: BLE001 - tool: surface and exit non-zero
        print(f"error walking the Earth Engine STAC catalog: {exc}", file=sys.stderr)
        return 1
    if not ids:
        print("no dataset ids found — aborting", file=sys.stderr)
        return 1
    block = render_available_datasets_block(categorise(ids))
    print(f"collected {len(ids)} dataset ids across {block.count('# -----')} providers")

    if args.dry_run:
        print(block)
    else:
        text = args.catalog.read_text(encoding="utf-8")
        args.catalog.write_text(splice_into_yaml(text, block), encoding="utf-8")
        print(f"updated {args.catalog}")

    if args.with_bands:
        for asset_id in args.with_bands:
            print(f"\n# ---- paste under `datasets:` ----\n{stanza_for(asset_id)}", end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
