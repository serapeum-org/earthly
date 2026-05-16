"""Hydrate placeholder stanzas (``bands: {}`` or empty ``bands:``) by querying Earth Engine directly.

Many catalog stanzas were synthesised without per-band metadata because the
public EE STAC walker can't reach the asset (community ``projects/...`` paths,
some custom-published assets, and tables / image-collections whose STAC entry
didn't carry a band manifest). This script:

* Authenticates with the service-account credentials in
  ``GEE_SERVICE_ACCOUNT`` / ``GEE_SERVICE_KEY``.
* Walks every dataset in the per-category YAML files under
  ``src/earthlens/gee/catalog/`` whose ``bands`` field is empty
  (either ``bands: {}`` or a naked ``bands:`` with no children).
* For each, calls ``ee.data.getAsset(id)`` to get the asset metadata
  (type, properties, bands when reported).
* Falls back to ``ee.ImageCollection(id).first()`` / ``ee.Image(id)`` to
  pull band names live when the asset payload doesn't carry them.
* Replaces the placeholder stanza in-place with a fuller one (corrected
  ee_type, sanitised title, start/end date, per-band entries with
  whatever wavelength / scale / offset properties EE returns).

Title handling: if the existing stanza title looks like the
``_minimal_stanza.py`` placeholder ("asset_id (community-published...)")
we overwrite. Otherwise we keep the existing curated title (avoids
overwriting with raw HTML/markdown junk that some EE assets carry as
their description).

Usage::

    pixi run -e dev python tools/gee/_hydrate_placeholders.py [--limit N]

Logs progress per asset; failures are recorded but don't stop the run.
The script saves periodically so a crash mid-run doesn't lose work.

Not part of the installed package — bulk-curation helper only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import ee  # type: ignore[import-untyped]

sys.path.insert(0, "src")
sys.path.insert(0, "tools/gee")
from _catalog_io import file_for  # noqa: E402

from earthlens.gee.auth import EarthEngineAuth  # noqa: E402


def _authenticate() -> None:
    sa = os.environ.get("GEE_SERVICE_ACCOUNT")
    key = os.environ.get("GEE_SERVICE_KEY")
    if not sa or not key:
        raise SystemExit("set GEE_SERVICE_ACCOUNT and GEE_SERVICE_KEY")
    EarthEngineAuth.initialize(sa, key)


_EE_TYPE_MAP = {
    "IMAGE_COLLECTION": "image_collection",
    "IMAGE": "image",
    "TABLE": "table",
    "TABLE_COLLECTION": "table_collection",
    "FEATURE_VIEW": "table",
}


_BOOL_BAND_NAMES = {
    "y", "Y", "yes", "Yes", "YES", "n", "N", "no", "No", "NO",
    "true", "True", "TRUE", "false", "False", "FALSE",
    "on", "On", "ON", "off", "Off", "OFF",
}


def _strip_html(text: str) -> str:
    """Strip HTML tags + collapse whitespace; safe for use in a YAML title."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&\w+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _looks_like_placeholder_title(title: str | None) -> bool:
    if not title:
        return True
    return "(community-published catalog reference)" in title or title.strip() == ""


def _get_band_info(asset_id: str, asset: dict[str, Any]) -> list[dict[str, Any]]:
    bands = asset.get("bands") or []
    if bands:
        return list(bands)
    et = asset.get("type", "").upper()
    try:
        if et == "IMAGE":
            band_names = ee.Image(asset_id).bandNames().getInfo()
        elif et == "IMAGE_COLLECTION":
            band_names = ee.ImageCollection(asset_id).first().bandNames().getInfo()
        else:
            return []
        return [{"id": b} for b in (band_names or [])]
    except Exception:  # noqa: BLE001
        return []


def _properties_text(asset: dict[str, Any], *keys: str) -> str | None:
    props = asset.get("properties") or {}
    for k in keys:
        v = props.get(k)
        if v:
            return str(v)
    return None


def _date_window(asset: dict[str, Any]) -> tuple[str | None, str | None]:
    sd = asset.get("startTime") or asset.get("start_time")
    ed = asset.get("endTime") or asset.get("end_time")
    if sd:
        sd = sd[:10] if len(sd) >= 10 else sd
    if ed:
        ed = ed[:10] if len(ed) >= 10 else ed
    return sd, ed


def _hydrate_one(asset_id: str) -> dict[str, Any] | None:
    try:
        asset = ee.data.getAsset(asset_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {asset_id}: getAsset failed: {exc}")
        return None
    ee_type = _EE_TYPE_MAP.get(asset.get("type", "").upper(), "image_collection")
    bands = _get_band_info(asset_id, asset) if ee_type in {"image", "image_collection"} else []
    sd, ed = _date_window(asset)
    raw_title = _properties_text(asset, "title", "system:asset_title", "system:title")
    if not raw_title:
        return {
            "ee_type": ee_type,
            "title": None,
            "start_date": sd,
            "end_date": ed,
            "bands": bands,
        }
    cleaned_title = _strip_html(raw_title)
    if len(cleaned_title) > 180:
        cleaned_title = cleaned_title[:180].rstrip()
    return {
        "ee_type": ee_type,
        "title": cleaned_title or None,
        "start_date": sd,
        "end_date": ed,
        "bands": bands,
    }


def _rewrite_stanza(text: str, asset_id: str, payload: dict[str, Any], existing_title: str | None) -> str:
    """Replace the placeholder bands / ee_type / dates / title for ``asset_id``.

    Title only overwritten if the existing title looks like a placeholder
    (otherwise we trust the manually curated one).
    """
    key = re.escape(asset_id)
    pattern = re.compile(rf"(?ms)^  {key}:\n(.*?)(?=^  [A-Za-z0-9/_.-]+:|\Z)")
    m = pattern.search(text)
    if not m:
        print(f"  ! {asset_id}: stanza not found in YAML")
        return text
    block = m.group(1)
    new_block = block

    # 1) ee_type
    if payload["ee_type"]:
        new_block = re.sub(r"(    ee_type: )[^\n]+", rf"\1{payload['ee_type']}", new_block, count=1)

    # 2) title — only overwrite if existing is a placeholder
    if payload["title"] and _looks_like_placeholder_title(existing_title):
        title_yaml = payload["title"].replace("'", "''")
        new_block = re.sub(r"(    title: ).*", rf"\1'{title_yaml}'", new_block, count=1)

    # 3) start_date / end_date
    if payload["start_date"]:
        new_block = re.sub(
            r'(      start_date: ")[^"]*(")',
            rf"\g<1>{payload['start_date']}\g<2>",
            new_block,
            count=1,
        )
    if payload["end_date"]:
        new_block = re.sub(
            r"(      end_date: )[^\n]+",
            rf'\1"{payload["end_date"]}"',
            new_block,
            count=1,
        )

    # 4) bands
    if payload["bands"]:
        bands_yaml_lines = []
        for b in payload["bands"]:
            bid = b.get("id") or b.get("name") or ""
            if not bid:
                continue
            quote_id = bid in _BOOL_BAND_NAMES or bid.isdigit()
            bid_yaml = f'"{bid}"' if quote_id else bid
            bands_yaml_lines.append(f"      {bid_yaml}:")
            bands_yaml_lines.append(f"        description: Band {bid}")
            for k_in in ("wavelength_um", "center_wavelength"):
                v = b.get(k_in)
                if v is not None:
                    bands_yaml_lines.append(f"        wavelength: {v}")
                    break
            for k in ("units", "unit"):
                v = b.get(k)
                if v:
                    bands_yaml_lines.append(f"        units: {v}")
                    break
            scale = b.get("scale")
            if scale not in (None, 1.0, 1):
                bands_yaml_lines.append(f"        scale: {scale}")
            offset = b.get("offset")
            if offset not in (None, 0.0, 0):
                bands_yaml_lines.append(f"        offset: {offset}")
        bands_yaml = "    bands:\n" + "\n".join(bands_yaml_lines) + "\n"
        # Match either ``    bands: {}\n`` or ``    bands:\n`` (followed by blank/next-stanza)
        replaced = re.sub(
            r"(?ms)^    bands:[ \t]*(\{\}\s*)?\n(?=^  [A-Za-z0-9/_.-]+:|\Z|\s*$)",
            bands_yaml,
            new_block,
            count=1,
        )
        if replaced == new_block:
            # Last-resort fallback: bands at end of file/stanza, no trailing context
            replaced = re.sub(
                r"(?ms)^    bands:[ \t]*(\{\}\s*)?\n",
                bands_yaml,
                new_block,
                count=1,
            )
        new_block = replaced

    return text[: m.start()] + f"  {asset_id}:\n" + new_block + text[m.end():]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="hydrate at most N assets (0 = no limit)")
    parser.add_argument("--save-every", type=int, default=25, help="save YAML every N assets")
    args = parser.parse_args()

    _authenticate()

    sys.path.insert(0, "src")
    from earthlens.gee import Catalog

    cat = Catalog()
    placeholders = sorted([k for k, d in cat.datasets.items() if not d.bands])
    print(f"placeholders: {len(placeholders)}")
    if args.limit:
        placeholders = placeholders[: args.limit]
        print(f"limiting to first {args.limit}")
    existing_titles = {k: cat.datasets[k].title for k in placeholders}

    # Per-provider file cache: read once, mutate, write back at save points.
    file_texts: dict[Path, str] = {}
    dirty: set[Path] = set()

    def _load(path: Path) -> str:
        if path not in file_texts:
            file_texts[path] = path.read_text(encoding="utf-8")
        return file_texts[path]

    def _flush() -> None:
        for path in list(dirty):
            file_texts[path] and path.write_text(file_texts[path], encoding="utf-8")
        dirty.clear()

    success = 0
    skipped = 0
    for i, aid in enumerate(placeholders, start=1):
        try:
            payload = _hydrate_one(aid)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {aid}: unexpected error: {exc}")
            payload = None
        if not payload:
            skipped += 1
            continue
        path = file_for(aid)
        text = _load(path)
        new_text = _rewrite_stanza(text, aid, payload, existing_titles.get(aid))
        if new_text != text:
            file_texts[path] = new_text
            dirty.add(path)
            success += 1
            tag = f"[{len(payload['bands']):3d} bands, {payload['ee_type']}]"
            print(f"  ok  {i:4d}/{len(placeholders)} {aid:80s} {tag}")
        else:
            skipped += 1
        if i % args.save_every == 0:
            _flush()
            time.sleep(0.5)

    _flush()
    print(f"hydrated: {success}, skipped: {skipped}")

    from importlib import reload
    import earthlens.gee.catalog as _cat
    reload(_cat)
    _cat.clear_catalog_cache()
    cat2 = _cat.Catalog()
    placeholders_after = [k for k, d in cat2.datasets.items() if not d.bands]
    print(f"placeholders after:  {len(placeholders_after)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
