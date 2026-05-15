"""Compact GEE catalog stanzas produced by ``refresh_gee_catalog.py --with-bands``.

Reads multi-stanza output from the refresh tool and normalises each stanza
into the project's terser conventions:

* drops "# TODO:" tail comments (we set explicit values).
* picks ``default_reducer`` from asset-id keywords.
* repairs truncated single-quoted ``title:`` / ``terms:`` / ``provider:``
  lines (the tool sometimes cuts mid-string with no closing quote).
* normalises CR / CRLF line endings and collapses runs of blank lines.
* drops "estimated_range: true" markers (cosmetic only).

Not part of the installed package — a one-off ``tools/`` helper used while
bulk-curating the GEE catalog.
"""

from __future__ import annotations

import re
import sys


_REDUCER_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"CHIRP|GPM|IMERG|PERSIANN|GSMaP|TRMM|precip|rainfall", re.I), "mean"),
    (re.compile(r"CHIRTS|TEMP|temperature|LST|TMIN|TMAX|GRIDMET|DAYMET|PRISM|MACAv2|GDDP|TerraClimate|MERRA|GLDAS|FLDAS|NLDAS|GFS|CFS|ERA5|RTMA|HRRR|NCEP|reanalysis|FLUX|RAD|radiation|SMAP|SPL3|SPL4|soil_moist|moist|GRACE|water_storage|TWS|drought|PDSI|SPI|SPEI|HEAT_FLUX|GRIDSAT|PATMOS|SST_PATHFINDER|SST_WHOI|GCOM-C", re.I), "mean"),
    (re.compile(r"NLCD|landcover|land_cover|forest_age|forest_change|hansen|primary|landform|topo|mTPI|CHILI|GIMP|DEM|GMTED|SRTM|NASADEM|TOPO|ETOPO|GTOPO|NAIP|GHSL|BUILT|SMOD|POP|population|WorldPop|GFSAD|EUCROPMAP|CORINE|WorldCover|CGLS|SLGA|SoilGrids|FROM-GLC|GAIA|fnf|FNF|NALCMS|RCMAP|GFCC|TCC|TC_v|GEDI04_B|landscape|reef|FireCCI|MCD64|MOD14|MYD14|burned|biomass|WSF|GFC2020|EVT|GHS|GRIDDED|forest|ALOS_landform|landforms|GIMP", re.I), "mosaic"),
    (re.compile(r"S2$|S2/|Sentinel-2|HARMONIZED|Landsat|MOD09|MYD09|MOD13|MYD13|MOD43|MYD43|MCD43|MCD19|MCD18|HLS|MOD15|MYD15|VNP09|VNP13|MODIS|surface[_ ]reflectance|TOA|reflectance|optical|MSI|HYPERION|AVHRR/SR|AVHRR/NDVI|AVHRR_PHENOLOGY|AVNIR|VIIRS|GOES|ASTER|MAIAC|BRDF|albedo", re.I), "median"),
    (re.compile(r"S5P|NO2|/CO/|/CO_|HCHO|SO2|/O3|CH4|aerosol|AER_AI|cloud|atmosphere|MOD08|MYD08|atmos|CAMS|methane|MethaneAIR|sea_surface|SST|salinity|salin|ocean|HYCOM|GLOBathy|chla|CHLA|chlorophyll|Rrs|RRS|biomass_carbon|forest_carbon|GEDI|MOD17|MYD17|MOD16|MYD16|MOD15|MYD15|LAI|FPAR|EVI|NDVI|GPP|NPP|NEE|/ET/|evapotrans|productivity|vegetation_index|gridded|MARINE", re.I), "mean"),
]


def _pick_reducer(asset_id: str) -> str:
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


def _clean_value(text: str) -> str:
    text = re.sub(r"\s*#\s*TODO:.*$", "", text)
    for k, v in _HTML_ENTITY.items():
        text = text.replace(k, v)
    return text


def _quote_int_band_name(text: str) -> str:
    """Quote 6-space-indented integer band names so YAML parses them as strings."""
    return re.sub(r"^(      )(\d+):\s*$", r'\1"\2":', text)


def _close_truncated_quote(text: str) -> str:
    """If a line looks like ``key: 'unterminated`` close it.

    Single-quoted YAML strings need a closing quote; otherwise the parser
    consumes everything until the next quote (often many lines down).
    """
    # match ``  key: 'something not ending in '``
    m = re.match(r"^(\s*[A-Za-z_][A-Za-z_0-9-]*: ')(.*)$", text)
    if not m:
        return text
    body = m.group(2)
    # already balanced if it ends in single quote (and isn't an empty '\'')
    if body.endswith("'") and not body.endswith("''"):
        return text
    # close the quote
    return m.group(1) + body.rstrip() + "'"


def _process(stream) -> str:
    out_lines: list[str] = []
    asset_id: str | None = None
    in_stanza = False

    raw_text = stream.read()
    # normalise line endings: CRLF + bare CR → LF
    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    # collapse 3+ consecutive newlines to 2
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
            else:
                continue

        # Inside a stanza
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
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"
    return text


if __name__ == "__main__":
    sys.stdout.write(_process(sys.stdin))
