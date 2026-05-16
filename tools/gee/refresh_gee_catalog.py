"""Maintain the bundled GEE data catalog (`src/earthlens/gee/catalog/`).

A single ``argparse`` subcommand CLI that absorbs the four scratch
helpers that grew during the 1104-asset curation push (H3 in
``planning/gee/catalog-architecture-review.md``). Run with no args to
see the full subcommand list:

    pixi run -e dev python tools/gee/refresh_gee_catalog.py --help

Subcommands:

* ``refresh`` — walk the public Earth Engine STAC catalog, regroup the
  collected ids by provider, and rewrite the ``available_datasets:``
  block in ``catalog/_index.yaml`` in place. Optional ``--with-bands
  <id> ...`` prints a ready-to-paste ``datasets:`` stanza for each id.
* ``add-ids <id> ...`` — fetch ``--with-bands`` stanzas for the given
  ids, run them through ``compact``, and append each stanza to the
  per-category file under ``catalog/`` chosen by
  ``_catalog_io.category_for``. Skips already-curated ids. Reloads the
  catalog at the end so a broken stanza fails the run.
* ``hydrate-live [--limit N]`` — for every curated dataset whose
  ``bands`` field is empty, query Earth Engine directly
  (``ee.data.getAsset`` plus ``ee.Image`` / ``ee.ImageCollection``
  fallback) and replace the placeholder stanza in-place with a fuller
  one. Requires ``GEE_SERVICE_ACCOUNT`` + ``GEE_SERVICE_KEY``.
* ``minimal-stanza <id> ...`` — synthesise a placeholder stanza
  (empty bands) for asset ids the STAC walker can't reach, mainly
  community ``projects/...`` paths.
* ``compact`` — read raw ``refresh --with-bands`` output on stdin and
  write the catalog's terser style on stdout (picks
  ``default_reducer`` from asset-id keywords, repairs truncated
  quotes, drops ``# TODO:`` comments, normalises line endings, quotes
  numeric / YAML-bool band names).

Exits 0 on success, 1 on any HTTP / parse / EE-API error.
Not part of the installed package.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from _catalog_io import CATALOG_DIR, category_for, find_stanza_span  # noqa: E402
from _gee_stac import collect_collection_ids, fetch_collection_stac  # noqa: E402

CATALOG_INDEX_PATH = Path("src/earthlens/gee/catalog/_index.yaml")
_GLOBAL_BBOX = [-180, -90, 180, 90]


# ---------------------------------------------------------------------------
# refresh — STAC walk + _index.yaml rewrite + optional --with-bands stanzas
# ---------------------------------------------------------------------------

def categorise(ids: list[str]) -> list[tuple[str, list[str]]]:
    """Group asset ids by their provider (first path segment), sorted.

    Args:
        ids: Asset ids, e.g. `["LANDSAT/LC09/C02/T1_L2", "MODIS/061/MOD13Q1"]`.

    Returns:
        `[(provider, [ids...]), ...]` sorted by provider, ids sorted within.
    """
    groups: dict[str, list[str]] = {}
    for asset_id in ids:
        groups.setdefault(asset_id.split("/", 1)[0], []).append(asset_id)
    return [(provider, sorted(groups[provider])) for provider in sorted(groups)]


def render_available_datasets_block(grouped: list[tuple[str, list[str]]]) -> str:
    """Render the `available_datasets:` YAML block.

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


def splice_into_index(text: str, block: str) -> str:
    """Replace the `available_datasets:` block in `text` with `block`.

    `text` is the full contents of `catalog/_index.yaml` — a file
    whose only top-level key is `available_datasets:` plus a leading
    comment header. The header is preserved; the list itself is fully
    rewritten.

    Args:
        text: The current `_index.yaml` contents.
        block: The replacement block (from
            :func:`render_available_datasets_block`), ending with a
            newline.

    Returns:
        The updated YAML text.

    Raises:
        ValueError: If `text` has no top-level `available_datasets:` key.
    """
    pattern = re.compile(r"(?ms)^available_datasets:.*\Z")
    if not pattern.search(text):
        raise ValueError(
            "could not find an 'available_datasets:' block in the index YAML"
        )
    return pattern.sub(block.rstrip("\n") + "\n", text)


def _gsd_to_metres(gsd) -> float | None:
    """Normalise a STAC band `gsd` (scalar or list) to a single metre value."""
    if gsd is None:
        return None
    if isinstance(gsd, (list, tuple)):
        values = [v for v in gsd if isinstance(v, (int, float))]
        return float(min(values)) if values else None
    return float(gsd) if isinstance(gsd, (int, float)) else None


def _first_line(text) -> str | None:
    """Return the first non-empty line of `text`, trimmed, or `None`."""
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return None


def stanza_for(asset_id: str) -> str:
    """Build a ready-to-paste `datasets.<asset_id>:` YAML stanza.

    Fetches the dataset's STAC document and transcribes the fields the
    catalog schema uses. `default_reducer` is emitted as `median`
    with a `# TODO: verify` comment (STAC does not carry it).

    Args:
        asset_id: An Earth Engine asset id.

    Returns:
        The YAML stanza (2-space indented, ready to paste under
        `datasets:`), ending with a newline.

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
        lines.append("    source: republished")
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


def _yaml_str(value) -> str:
    """Render `value` as a single-line YAML scalar (safely quoted when needed).

    Delegates to `yaml.safe_dump` so non-ASCII text, embedded quotes, and YAML
    metacharacters are escaped correctly — and only the scalar's first line is
    returned, dropping the document-end marker (`\\n...\\n`) PyYAML adds when
    dumping a top-level scalar.
    """
    text = str(value).strip()
    return yaml.safe_dump(text, default_flow_style=False, allow_unicode=True).split("\n", 1)[0]


def _num(value) -> str:
    """Render a number compactly (drop a trailing `.0` only when harmless)."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value) if isinstance(value, float) else str(value)


def _cmd_refresh(args: argparse.Namespace) -> int:
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
        text = args.catalog_index.read_text(encoding="utf-8")
        args.catalog_index.write_text(splice_into_index(text, block), encoding="utf-8")
        print(f"updated {args.catalog_index}")

    if args.with_bands:
        for asset_id in args.with_bands:
            print(f"\n# ---- paste under `datasets:` ----\n{stanza_for(asset_id)}", end="")
    return 0


# ---------------------------------------------------------------------------
# compact — clean up raw `refresh --with-bands` output for in-place pasting
# ---------------------------------------------------------------------------

_REDUCER_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"CHIRP|GPM|IMERG|PERSIANN|GSMaP|TRMM|precip|rainfall", re.I), "mean"),
    (re.compile(r"CHIRTS|TEMP|temperature|LST|TMIN|TMAX|GRIDMET|DAYMET|PRISM|MACAv2|GDDP|TerraClimate|MERRA|GLDAS|FLDAS|NLDAS|GFS|CFS|ERA5|RTMA|HRRR|NCEP|reanalysis|FLUX|RAD|radiation|SMAP|SPL3|SPL4|soil_moist|moist|GRACE|water_storage|TWS|drought|PDSI|SPI|SPEI|HEAT_FLUX|GRIDSAT|PATMOS|SST_PATHFINDER|SST_WHOI|GCOM-C", re.I), "mean"),
    (re.compile(r"NLCD|landcover|land_cover|forest_age|forest_change|hansen|primary|landform|topo|mTPI|CHILI|GIMP|DEM|GMTED|SRTM|NASADEM|TOPO|ETOPO|GTOPO|NAIP|GHSL|BUILT|SMOD|POP|population|WorldPop|GFSAD|EUCROPMAP|CORINE|WorldCover|CGLS|SLGA|SoilGrids|FROM-GLC|GAIA|fnf|FNF|NALCMS|RCMAP|GFCC|TCC|TC_v|GEDI04_B|landscape|reef|FireCCI|MCD64|MOD14|MYD14|burned|biomass|WSF|GFC2020|EVT|GHS|GRIDDED|forest|ALOS_landform|landforms|GIMP", re.I), "mosaic"),
    (re.compile(r"S2$|S2/|Sentinel-2|HARMONIZED|Landsat|MOD09|MYD09|MOD13|MYD13|MOD43|MYD43|MCD43|MCD19|MCD18|HLS|MOD15|MYD15|VNP09|VNP13|MODIS|surface[_ ]reflectance|TOA|reflectance|optical|MSI|HYPERION|AVHRR/SR|AVHRR/NDVI|AVHRR_PHENOLOGY|AVNIR|VIIRS|GOES|ASTER|MAIAC|BRDF|albedo", re.I), "median"),
    (re.compile(r"S5P|NO2|/CO/|/CO_|HCHO|SO2|/O3|CH4|aerosol|AER_AI|cloud|atmosphere|MOD08|MYD08|atmos|CAMS|methane|MethaneAIR|sea_surface|SST|salinity|salin|ocean|HYCOM|GLOBathy|chla|CHLA|chlorophyll|Rrs|RRS|biomass_carbon|forest_carbon|GEDI|MOD17|MYD17|MOD16|MYD16|MOD15|MYD15|LAI|FPAR|EVI|NDVI|GPP|NPP|NEE|/ET/|evapotrans|productivity|vegetation_index|gridded|MARINE", re.I), "mean"),
]


def _pick_reducer(asset_id: str) -> str:
    """First-match-wins reducer pick from asset-id substrings."""
    for pattern, reducer in _REDUCER_RULES:
        if pattern.search(asset_id):
            return reducer
    return "mosaic"


_HTML_ENTITY = {
    "&deg;": "deg_",
    "&micro;": "u",
    "&mu;": "u",
    "&amp;": "&",
}

_BOOL_BANDS = {
    "y", "Y", "yes", "Yes", "YES",
    "n", "N", "no", "No", "NO",
    "true", "True", "TRUE",
    "false", "False", "FALSE",
    "on", "On", "ON",
    "off", "Off", "OFF",
}


def _clean_value(text: str) -> str:
    text = re.sub(r"\s*#\s*TODO:.*$", "", text)
    for k, v in _HTML_ENTITY.items():
        text = text.replace(k, v)
    return text


def _quote_int_band_name(text: str) -> str:
    """Quote 6-space-indented numeric or YAML-bool-like band names as strings."""
    text = re.sub(r"^(      )(\d+):\s*$", r'\1"\2":', text)
    m = re.match(r"^(      )([A-Za-z]+):\s*$", text)
    if m and m.group(2) in _BOOL_BANDS:
        return f'{m.group(1)}"{m.group(2)}":'
    return text


def _close_truncated_quote(text: str) -> str:
    """Repair lines like `key: 'unterminated` by appending a closing quote.

    Single-quoted YAML strings need a closing quote; otherwise the
    parser swallows everything up to the next quote.
    """
    m = re.match(r"^(\s*[A-Za-z_][A-Za-z_0-9-]*: ')(.*)$", text)
    if not m:
        return text
    body = m.group(2)
    if body.endswith("'") and not body.endswith("''"):
        return text
    return m.group(1) + body.rstrip() + "'"


def compact_text(raw_text: str) -> str:
    """Normalise raw `refresh --with-bands` output into the catalog's style.

    Operations applied per line:

    * drop `# TODO:` tail comments,
    * pick `default_reducer` from asset-id keywords (see `_REDUCER_RULES`),
    * repair truncated single-quoted strings,
    * normalise CRLF/CR → LF, collapse 3+ blank lines to 2,
    * drop `estimated_range: true` markers (cosmetic),
    * quote numeric / YAML-bool band names so the YAML loader doesn't
      coerce them.
    """
    out_lines: list[str] = []
    asset_id: str | None = None
    in_stanza = False

    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    raw_text = re.sub(r"\n{3,}", "\n\n", raw_text)

    for line in raw_text.split("\n"):
        if line.startswith("# ---- paste under"):
            in_stanza = False
            continue
        if not in_stanza:
            m = re.match(r"  ([A-Za-z0-9/_.-]+):\s*$", line)
            if m:
                asset_id = m.group(1)
                in_stanza = True
                if out_lines and out_lines[-1] != "":
                    out_lines.append("")
                out_lines.append(line)
                continue
            continue

        if "default_reducer:" in line:
            reducer = _pick_reducer(asset_id or "")
            indent = line[: line.index("default_reducer:")]
            out_lines.append(f"{indent}default_reducer: {reducer}")
            continue
        if "end_date:" in line and "null" in line:
            out_lines.append(re.sub(r"\s*#.*$", "", line))
            continue
        if line.strip() == "estimated_range: true":
            continue
        cleaned = _clean_value(line)
        cleaned = _close_truncated_quote(cleaned)
        cleaned = _quote_int_band_name(cleaned)
        out_lines.append(cleaned)

    text = "\n".join(out_lines)
    return re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"


def _cmd_compact(args: argparse.Namespace) -> int:
    sys.stdout.write(compact_text(sys.stdin.read()))
    return 0


# ---------------------------------------------------------------------------
# minimal-stanza — synthesise empty-bands placeholder for ids the STAC misses
# ---------------------------------------------------------------------------

_PROJECT_PROVIDER: dict[str, tuple[str, str]] = {
    "edf-methanesat-ee": ("Environmental Defense Fund (EDF) — MethaneSAT", "Free for use under EDF MethaneSAT terms."),
    "ee-kbas-in-gee": ("Key Biodiversity Areas (KBAs) republication", "Free for use under KBAs terms."),
    "ee-pkurelab": ("Peking University Remote Sensing Lab (PKU REL)", "Free for use with citation (PKU REL)."),
    "forestdatapartnership": ("Forest Data Partnership", "Free for use under the Forest Data Partnership terms."),
    "gcp-public-data-weathernext": ("Google WeatherNext / GCP Public Data", "Free for use under Google Cloud public-data terms."),
    "gcpm041u-lemur": ("Lemur Forest Inventory (gcpm041u)", "Free for use with citation."),
    "global-pasture-watch": ("Global Pasture Watch", "Free for use (Global Pasture Watch)."),
    "global-precipitation-nowcast": ("Google Global Precipitation Nowcast", "Free for use under Google Cloud public-data terms."),
    "gtac-data-publish": ("USDA Forest Service GTAC", "Public domain (USDA Forest Service)."),
    "landandcarbon": ("WRI Land and Carbon", "CC-BY-4.0."),
    "malariaatlasproject": ("Oxford Malaria Atlas Project", "CC-BY-NC-SA-4.0."),
    "mapbiomas-public": ("MapBiomas", "CC-BY-SA-4.0."),
    "nature-trace": ("Nature Trace", "Free for use (Nature Trace)."),
    "neon-prod-earthengine": ("National Ecological Observatory Network (NEON)", "Public domain (NEON Science)."),
    "ngis-cat": ("Geoscience Australia / DEA (NGIS catalogue)", "CC-BY-4.0."),
    "openet": ("OpenET, Inc.", "CC-BY-4.0."),
    "planet-nicfi": ("Planet Labs (NICFI programme)", "Free for non-commercial use under Planet NICFI terms."),
    "pml_evapotranspiration": ("PML_V2 Evapotranspiration team", "Free for use with citation (PML_V2)."),
    "sat-io": ("Sat-IO open-datasets republication", "Varies — see source."),
}


def _provider_and_terms(asset_id: str) -> tuple[str, str]:
    parts = asset_id.split("/")
    if len(parts) >= 2 and parts[0] == "projects":
        prj = parts[1]
        if prj in _PROJECT_PROVIDER:
            return _PROJECT_PROVIDER[prj]
        return (f"GEE project: {prj}", "See source for licence terms.")
    return ("Earth Engine catalog publisher", "See source for licence terms.")


def minimal_stanza(asset_id: str) -> str:
    """Synthesise a placeholder catalog stanza for `asset_id` (empty bands).

    Title / provider / terms are derived heuristically. The
    `default_reducer` is picked by the same rules as `compact`.
    """
    provider, terms = _provider_and_terms(asset_id)
    reducer = _pick_reducer(asset_id)
    indent = "    "
    title = f"{asset_id} (community-published catalog reference)".replace("'", "''")
    return (
        f"  {asset_id}:\n"
        f"{indent}title: '{title}'\n"
        f"{indent}provider: {provider}\n"
        f"{indent}ee_type: image_collection\n"
        f"{indent}extent:\n"
        f"{indent}  start_date: \"2020-01-01\"\n"
        f"{indent}  end_date: null\n"
        f"{indent}default_reducer: {reducer}\n"
        f"{indent}terms: {terms}\n"
        f"{indent}source: community\n"
        f"{indent}bands: {{}}\n"
    )


def _cmd_minimal_stanza(args: argparse.Namespace) -> int:
    sys.stdout.write("\n".join(minimal_stanza(aid) for aid in args.asset_ids))
    return 0


# ---------------------------------------------------------------------------
# add-ids — fetch --with-bands, compact, append to the right category file
# ---------------------------------------------------------------------------

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


def _cmd_add_ids(args: argparse.Namespace) -> int:
    sys.path.insert(0, "src")
    from earthlens.gee import Catalog
    from earthlens.gee.catalog import clear_catalog_cache

    cat = Catalog()
    existing = set(cat.datasets)
    fresh = [i for i in args.asset_ids if i not in existing]
    if not fresh:
        print("nothing to add — all ids already curated")
        return 0
    skipped = sorted(set(args.asset_ids) - set(fresh))
    if skipped:
        print(f"skipping already-curated: {skipped}")

    raw = io.StringIO()
    for aid in fresh:
        raw.write(f"\n# ---- paste under `datasets:` ----\n")
        raw.write(stanza_for(aid))
    compact = compact_text(raw.getvalue())

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
            existing_bytes = (
                f"# Auto-grouped slice of the GEE catalog: {category}.\n"
                f"# Created by refresh_gee_catalog.py add-ids. Edit in place.\n\n"
                f"datasets:\n"
            ).encode("utf-8")
        target.write_bytes(existing_bytes + b"".join(s.encode("utf-8") for s in stanzas))

    clear_catalog_cache()
    cat2 = Catalog()
    print(f"appended {len(fresh)} stanzas — total curated: {len(cat2.datasets)}")
    return 0


# ---------------------------------------------------------------------------
# hydrate-live — fill empty-bands placeholders via live ee.data.getAsset
# ---------------------------------------------------------------------------

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
    return re.sub(r"\s+", " ", text).strip()


def _looks_like_placeholder_title(title: str | None) -> bool:
    if not title:
        return True
    return "(community-published catalog reference)" in title or title.strip() == ""


def _get_band_info(asset_id: str, asset: dict[str, Any], ee_mod) -> list[dict[str, Any]]:
    bands = asset.get("bands") or []
    if bands:
        return list(bands)
    et = asset.get("type", "").upper()
    try:
        if et == "IMAGE":
            band_names = ee_mod.Image(asset_id).bandNames().getInfo()
        elif et == "IMAGE_COLLECTION":
            band_names = ee_mod.ImageCollection(asset_id).first().bandNames().getInfo()
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


def _hydrate_one(asset_id: str, ee_mod) -> dict[str, Any] | None:
    try:
        asset = ee_mod.data.getAsset(asset_id)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {asset_id}: getAsset failed: {exc}")
        return None
    ee_type = _EE_TYPE_MAP.get(asset.get("type", "").upper(), "image_collection")
    bands = _get_band_info(asset_id, asset, ee_mod) if ee_type in {"image", "image_collection"} else []
    sd, ed = _date_window(asset)
    raw_title = _properties_text(asset, "title", "system:asset_title", "system:title")
    if not raw_title:
        return {"ee_type": ee_type, "title": None, "start_date": sd, "end_date": ed, "bands": bands}
    cleaned_title = _strip_html(raw_title)
    if len(cleaned_title) > 180:
        cleaned_title = cleaned_title[:180].rstrip()
    return {"ee_type": ee_type, "title": cleaned_title or None, "start_date": sd, "end_date": ed, "bands": bands}


def _rewrite_stanza(text: str, asset_id: str, payload: dict[str, Any], existing_title: str | None) -> str:
    """Replace the placeholder bands / ee_type / dates / title for `asset_id`."""
    key = re.escape(asset_id)
    pattern = re.compile(rf"(?ms)^  {key}:\n(.*?)(?=^  [A-Za-z0-9/_.-]+:|\Z)")
    m = pattern.search(text)
    if not m:
        print(f"  ! {asset_id}: stanza not found in YAML")
        return text
    block = m.group(1)
    new_block = block

    if payload["ee_type"]:
        new_block = re.sub(r"(    ee_type: )[^\n]+", rf"\1{payload['ee_type']}", new_block, count=1)

    if payload["title"] and _looks_like_placeholder_title(existing_title):
        title_yaml = payload["title"].replace("'", "''")
        new_block = re.sub(r"(    title: ).*", rf"\1'{title_yaml}'", new_block, count=1)

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

    if payload["bands"]:
        bands_yaml_lines = []
        for b in payload["bands"]:
            bid = b.get("id") or b.get("name") or ""
            if not bid:
                continue
            quote_id = bid in _BOOL_BAND_NAMES or bid.isdigit()
            bid_yaml = f'"{bid}"' if quote_id else bid
            bands_yaml_lines.append(f"      {bid_yaml}:")
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
        replaced = re.sub(
            r"(?ms)^    bands:[ \t]*(\{\}\s*)?\n(?=^  [A-Za-z0-9/_.-]+:|\Z|\s*$)",
            bands_yaml,
            new_block,
            count=1,
        )
        if replaced == new_block:
            replaced = re.sub(
                r"(?ms)^    bands:[ \t]*(\{\}\s*)?\n",
                bands_yaml,
                new_block,
                count=1,
            )
        new_block = replaced

    return text[: m.start()] + f"  {asset_id}:\n" + new_block + text[m.end():]


def _cmd_hydrate_live(args: argparse.Namespace) -> int:
    sa = os.environ.get("GEE_SERVICE_ACCOUNT")
    key = os.environ.get("GEE_SERVICE_KEY")
    if not sa or not key:
        print("set GEE_SERVICE_ACCOUNT and GEE_SERVICE_KEY", file=sys.stderr)
        return 1

    import ee  # type: ignore[import-untyped]

    sys.path.insert(0, "src")
    from earthlens.gee import Catalog
    from earthlens.gee.auth import EarthEngineAuth
    from earthlens.gee.catalog import clear_catalog_cache

    EarthEngineAuth.initialize(sa, key)

    cat = Catalog()
    placeholders = sorted(k for k, d in cat.datasets.items() if not d.bands)
    print(f"placeholders: {len(placeholders)}")
    if args.limit:
        placeholders = placeholders[: args.limit]
        print(f"limiting to first {args.limit}")
    existing_titles = {k: cat.datasets[k].title for k in placeholders}

    file_texts: dict[Path, str] = {}
    dirty: set[Path] = set()

    def _load(path: Path) -> str:
        if path not in file_texts:
            file_texts[path] = path.read_text(encoding="utf-8")
        return file_texts[path]

    def _flush() -> None:
        for path in list(dirty):
            path.write_text(file_texts[path], encoding="utf-8")
        dirty.clear()

    success = 0
    skipped = 0
    for i, aid in enumerate(placeholders, start=1):
        try:
            payload = _hydrate_one(aid, ee)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {aid}: unexpected error: {exc}")
            payload = None
        if not payload:
            skipped += 1
            continue
        path = _file_for_existing_asset(aid)
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

    clear_catalog_cache()
    cat2 = Catalog()
    placeholders_after = [k for k, d in cat2.datasets.items() if not d.bands]
    print(f"placeholders after: {len(placeholders_after)}")
    return 0


def _file_for_existing_asset(asset_id: str) -> Path:
    """Find the per-category file that already contains `asset_id`'s stanza."""
    for path in sorted(CATALOG_DIR.glob("*.yaml")):
        if path.name == "_index.yaml":
            continue
        if find_stanza_span(path.read_text(encoding="utf-8"), asset_id):
            return path
    raise ValueError(f"{asset_id!r} not found in any catalog/*.yaml")


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_refresh = sub.add_parser("refresh", help="STAC walk + rewrite _index.yaml (+ optional --with-bands stanzas)")
    p_refresh.add_argument("--catalog-index", type=Path, default=CATALOG_INDEX_PATH, help="path to catalog/_index.yaml")
    p_refresh.add_argument("--dry-run", action="store_true", help="print the new available_datasets: block instead of writing")
    p_refresh.add_argument("--with-bands", nargs="+", metavar="ASSET_ID", help="also print a ready-to-paste datasets: stanza for each id")
    p_refresh.add_argument("-v", "--verbose", action="store_true", help="print STAC-walk progress")
    p_refresh.set_defaults(func=_cmd_refresh)

    p_add = sub.add_parser("add-ids", help="fetch + compact + append stanzas to the right per-category catalog file")
    p_add.add_argument("asset_ids", nargs="+", metavar="ASSET_ID")
    p_add.set_defaults(func=_cmd_add_ids)

    p_hyd = sub.add_parser("hydrate-live", help="fill empty-bands stanzas via live ee.data.getAsset")
    p_hyd.add_argument("--limit", type=int, default=0, help="hydrate at most N assets (0 = no limit)")
    p_hyd.add_argument("--save-every", type=int, default=25, help="save YAML every N assets")
    p_hyd.set_defaults(func=_cmd_hydrate_live)

    p_min = sub.add_parser("minimal-stanza", help="emit placeholder stanzas for asset ids the STAC walker can't reach")
    p_min.add_argument("asset_ids", nargs="+", metavar="ASSET_ID")
    p_min.set_defaults(func=_cmd_minimal_stanza)

    p_cpt = sub.add_parser("compact", help="stdin -> stdout post-processor for raw `refresh --with-bands` output")
    p_cpt.set_defaults(func=_cmd_compact)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
