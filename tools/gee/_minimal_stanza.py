"""Generate minimal catalog stanzas for asset ids the EE STAC walker cannot reach.

The STAC walker only sees ids registered in the public Earth Engine catalog
tree. Many community-republished ``projects/...`` assets are absent from that
tree, so we synthesise placeholder stanzas with empty bands (the catalog
loader accepts ``bands: {}``).

Usage::

    pixi run -e dev python tools/gee/_minimal_stanza.py id1 id2 ... > stanzas.yaml

The default_reducer is picked from asset-id keywords (same rules as
``_compact_stanzas``). Title / provider / terms are derived heuristically.

Not part of the installed package — bulk-curation helper only.
"""

from __future__ import annotations

import re
import sys


_REDUCER_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"CHIRP|GPM|IMERG|PERSIANN|GSMaP|TRMM|precip|rainfall|nowcast|gpp|ggpp", re.I), "mean"),
    (re.compile(r"CHIRTS|TEMP|temperature|LST|TMIN|TMAX|GRIDMET|DAYMET|PRISM|MACAv2|GDDP|MERRA|GLDAS|FLDAS|NLDAS|GFS|CFS|ERA5|RTMA|HRRR|NCEP|reanalysis|FLUX|RAD|radiation|SMAP|SPL3|SPL4|moist|GRACE|TWS|drought|PDSI|SPI|SPEI|HEAT_FLUX|GRIDSAT|PATMOS|SST_PATHFINDER|SST_WHOI|GCOM-C|weathernext|methaneair|methanesat|EVI|TCB|TCW", re.I), "mean"),
    (re.compile(r"NLCD|landcover|land_cover|forest|grassland|pasture|hansen|topo|mTPI|CHILI|GIMP|DEM|GMTED|SRTM|NASADEM|TOPO|ETOPO|GTOPO|NAIP|GHSL|BUILT|SMOD|POP|population|WorldPop|GFSAD|EUCROPMAP|CORINE|WorldCover|CGLS|SLGA|SoilGrids|FROM-GLC|GAIA|fnf|FNF|NALCMS|RCMAP|GFCC|TCC|biomass|carbon|WSF|GFC2020|EVT|GHS|GRIDDED|LCMS|TreeMap|cocoa|coffee|palm|rubber|model_|natural_forest|farmscapes|species_distribution|lulc|mapbiomas|naturalLands|landandcarbon|wri_gdm|veg-height|vegetation|scanfi|EARTHENGINE|landform", re.I), "mosaic"),
    (re.compile(r"reflectance|HSI|RGB|MULTISPECTRAL|HYPERION|optical|MSI|VIIRS|MODIS|NEON_RGB|aviris", re.I), "median"),
]


def _pick_reducer(asset_id: str) -> str:
    for pattern, reducer in _REDUCER_RULES:
        if pattern.search(asset_id):
            return reducer
    return "mosaic"


_PROJECT_PROVIDER = {
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


def _title(asset_id: str) -> str:
    """Derive a human title from the asset id tail."""
    tail = asset_id.split("/", 2)[-1] if asset_id.startswith("projects/") else asset_id
    # Use the asset id itself as the title — readable enough.
    return f"{asset_id} (community-published catalog reference)"


def _stanza(asset_id: str) -> str:
    provider, terms = _provider_and_terms(asset_id)
    reducer = _pick_reducer(asset_id)
    indent = "    "
    title = _title(asset_id).replace("'", "''")  # YAML single-quote escape
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
        f"{indent}user_uploaded: true\n"
        f"{indent}bands: {{}}\n"
    )


if __name__ == "__main__":
    out = "\n".join(_stanza(i) for i in sys.argv[1:])
    sys.stdout.write(out)
