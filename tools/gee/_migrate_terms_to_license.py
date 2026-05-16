"""One-off migration: rewrite each catalog stanza's `terms:` as `license:` + optional `terms_note:`.

The catalog's ``terms`` field is unnormalised free text — 182 distinct
strings collapse onto roughly 10 SPDX-style licence ids. This script
classifies every stanza's existing ``terms:`` string and rewrites the
YAML so each entry has:

* ``license: <SPDX-id-or-conventional-name>`` — one of ``CC-BY-4.0``,
  ``CC-BY-SA-4.0``, ``CC-BY-NC-4.0``, ``CC-BY-NC-SA-4.0``, ``CC0-1.0``,
  ``ODbL-1.0``, ``OGL-Canada-2.0``, ``etalab-2.0``, ``CDLA-Permissive-1.0``,
  ``public-domain``, ``proprietary`` (publisher-specific terms-of-service),
  or ``unknown``.
* ``terms_note: '<original prose>'`` — preserved verbatim when the
  classification isn't a clean SPDX match. Omitted when ``license`` alone
  conveys everything.

After this script runs, every catalog stanza has both fields (or just
``license`` for the CC-BY-4.0 / CC0 majority). The pydantic model is
updated separately to add ``license`` and rename ``terms`` →
``terms_note`` (the rename must happen in lockstep with this rewrite).

Usage::

    pixi run -e dev python tools/gee/_migrate_terms_to_license.py

Idempotent: skips stanzas that already have a ``license:`` line.

Not part of the installed package — bulk-curation helper only.
"""

from __future__ import annotations

import re
from pathlib import Path

CATALOG_DIR = Path("src/earthlens/gee/catalog")


# Each rule is (substring_to_match_case_insensitive, license_id,
# whether_to_keep_the_original_as_terms_note). First match wins.
_RULES: list[tuple[str, str, bool]] = [
    # SPDX matches — original text is just the SPDX id, no extra note needed.
    ("[CC-BY-4.0](https://spdx.org/licenses/CC-BY-4.0.html)", "CC-BY-4.0", False),
    ("[CC-BY-SA-4.0](https://spdx.org/licenses/CC-BY-SA-4.0.html)", "CC-BY-SA-4.0", False),
    ("[CC-BY-NC-4.0](https://spdx.org/licenses/CC-BY-NC-4.0.html)", "CC-BY-NC-4.0", False),
    ("[CC-BY-NC-SA-4.0](https://spdx.org/licenses/CC-BY-NC-SA-4.0.html)", "CC-BY-NC-SA-4.0", False),
    ("[ODbL-1.0](https://spdx.org/licenses/ODbL-1.0.html)", "ODbL-1.0", False),
    ("[etalab-2.0](https://spdx.org/licenses/etalab-2.0.html)", "etalab-2.0", False),
    ("OGL-Canada-2.0.", "OGL-Canada-2.0", False),
    ("Community Data License (CDLA).", "CDLA-Permissive-1.0", False),

    # Conventional CC-BY-NC-SA-4.0
    ("CC-BY-NC-SA-4.0.", "CC-BY-NC-SA-4.0", False),
    # Conventional CC-BY-SA-4.0
    ("CC-BY-SA-4.0.", "CC-BY-SA-4.0", False),
    # Conventional CC-BY-4.0 (incl. variants)
    ("CC-BY-4.0.", "CC-BY-4.0", False),
    ("CC-BY-4.0 (Geoscience Australia).", "CC-BY-4.0", False),  # Geoscience Australia uses pure CC-BY-4.0
    ("Free for use (CC-BY-4.0).", "CC-BY-4.0", False),
    ("CC0-1.0.", "CC0-1.0", False),
    ("Public domain (CC0).", "CC0-1.0", False),

    # CC-BY-4.0 with Copernicus Marine attribution-style prose
    ("CC-BY (Copernicus Marine).", "CC-BY-4.0", True),

    # ---- public-domain (US-government works, NOAA/NASA/USGS) ----
    ("Public domain.", "public-domain", False),
    ("Public domain (USGS).", "public-domain", False),
    ("Public domain (USGS Landsat).", "public-domain", False),
    ("Public domain (NOAA).", "public-domain", False),
    ("Public domain (NOAA CDR).", "public-domain", False),
    ("Public domain (NASA).", "public-domain", False),
    ("Public domain (NASA / USFS).", "public-domain", False),
    ("Public domain (NASA GES DISC).", "public-domain", False),
    ("Public domain (NASA GSFC).", "public-domain", False),
    ("Public domain (NASA LP DAAC).", "public-domain", False),
    ("Public domain (NASA LAADS DAAC).", "public-domain", False),
    ("Public domain (NASA LAADS).", "public-domain", False),
    ("Public domain (NASA / NSIDC).", "public-domain", False),
    ("Public domain (NASA ORNL DAAC).", "public-domain", False),
    ("Public domain (NASA OB.DAAC).", "public-domain", False),
    ("Public domain (NASA GIMMS).", "public-domain", False),
    ("Public domain (NASA/JPL).", "public-domain", False),
    ("Public domain (NEON Science).", "public-domain", False),
    ("Public domain (LANDFIRE).", "public-domain", False),
    ("Public domain (USDA FSA).", "public-domain", False),
    ("Public domain (USDA Forest Service).", "public-domain", False),
    ("Public domain (USDA NASS).", "public-domain", False),
    ("Public domain (METDATA, John Abatzoglou).", "public-domain", False),
    ("Public domain (USDM).", "public-domain", False),
    ("Public (USDA Forest Service).", "public-domain", False),

    # NASA / USGS / Landsat / MODIS umbrella terms — fall back to public-domain + note
    ("NASA LP DAAC", "public-domain", True),
    ("MODIS data and products acquired through the LP DAAC", "public-domain", True),
    ("LP DAAC NASA data are freely accessible", "public-domain", True),
    ("Landsat datasets are federally created data", "public-domain", True),
    ("NOAA data, information, and products", "public-domain", True),
    ("These images are in the public domain", "public-domain", True),
    ("Most U.S. Geological Survey (USGS) information resides", "public-domain", True),
    ("There are no restrictions on use of this US public domain data.", "public-domain", False),
    ("Unless otherwise noted, all NASA-produced data may be used for any purpose", "public-domain", True),
    ("NASA promotes the full and open sharing of all data with the research and", "public-domain", True),
    ("NASA promotes the full and open sharing of all data with research and", "public-domain", True),
    ("All NASA-produced data from the GRACE mission is made freely available", "public-domain", True),
    ("NASA — freely available, no restrictions.", "public-domain", False),
    ("NASA EMIT data and products acquired through the LP DAAC", "public-domain", True),
    ("This dataset is in the public domain and is available", "public-domain", True),
    ("This work is in the public domain and is free of known copyright", "public-domain", True),
    ("These data are considered public domain.", "public-domain", False),
    ("LANDFIRE data are public domain data with no use restrictions", "public-domain", True),
    ("The SOLUS dataset is in the public domain", "public-domain", True),
    ("The U.S. Census Bureau offers some of its public data", "public-domain", True),
    ("Distribution of data from the Goddard Earth Sciences", "public-domain", True),
    ("The NOAA CDR Program's official distribution point for CDRs is NOAA's", "public-domain", True),
    ("The NOAA CPC datasets are available without restriction", "public-domain", True),
    ("Most materials published on the Earth Observatory", "public-domain", True),
    ("There are no restrictions on the use of JPSS data", "public-domain", True),
    ("Hyperion data are in the public domain", "public-domain", True),
    ("All NTSG data distributed through this", "public-domain", True),
    ("This work (METDATA, by John Abatzoglou) is in the public", "public-domain", True),
    ("These PRISM datasets are available without restriction", "public-domain", True),
    ("This work was authored as part of the Contributor's official duties", "public-domain", True),
    ("Free for any use (NCEP / NCAR).", "public-domain", False),
    ("Free for any use (NCEP-DOE).", "public-domain", False),
    ("Free for any use (NFIS).", "public-domain", False),
    ("Free for any use (MACA).", "public-domain", False),
    ("Free for any use (NASA GRACE).", "public-domain", False),
    ("Free for any use (NASA LP DAAC).", "public-domain", False),

    # FORMA / WRI (public-data style, attribution requested but no specific licence)
    ("The FORMA datasets are available without restriction", "public-domain", True),
    ("The WRI datasets are available without restriction", "public-domain", True),

    # ---- proprietary — publisher-specific terms-of-service ----
    ("Free for use under the Copernicus Sentinel data terms.", "proprietary", True),
    ("Free for use under Copernicus Marine SLA.", "proprietary", True),
    ("Free for use under the Copernicus Programme licence.", "proprietary", True),
    ("Free for use under Google Cloud public-data terms.", "proprietary", True),
    ("Free for use under ESA CCI data terms.", "proprietary", True),
    ("Free for use under EDF MethaneSAT terms.", "proprietary", True),
    ("Free for use under the Forest Data Partnership terms.", "proprietary", True),
    ("Free for use under KBAs terms.", "proprietary", True),
    ("Free for any use (JAXA GCOM-C terms).", "proprietary", True),
    ("Free with attribution (JAXA GSMaP terms).", "proprietary", True),
    ("Free with attribution (JAXA EORC).", "proprietary", True),
    ("Free with attribution (JAXA AVNIR-2 terms).", "proprietary", True),
    ("Free for any use, attribution required (JAXA AW3D30 terms).", "proprietary", True),
    ("Free for any use with attribution (JAXA EORC).", "proprietary", True),
    ("Free with attribution (FAO WAPOR).", "proprietary", True),
    ("Free for any use (PRISM Climate Group).", "proprietary", True),
    ("Free with attribution (Copernicus).", "proprietary", True),
    ("Free with attribution (CAMS).", "proprietary", True),
    ("Free for any use under the Copernicus DEM licence.", "proprietary", True),
    ("Copernicus C3S/CAMS licence — free with attribution.", "proprietary", True),
    ("Copernicus C3S/CAMS licence — acknowledge ERA5-Land.", "proprietary", True),
    ("Copernicus Sentinel Data Terms and Conditions.", "proprietary", True),
    ("HydroSHEDS data are free for non-commercial and commercial", "proprietary", True),
    ("Free for non-commercial and commercial use.", "proprietary", True),
    ("Free for non-commercial use under Planet NICFI terms.", "proprietary", True),
    ("Free for non-commercial use; see WHRC terms.", "proprietary", True),
    ("Free for non-commercial use; commercial requires attribution (CGIAR-CSI).", "proprietary", True),
    ("Open Data (AHN).", "proprietary", True),
    ("Free for any use (HYCOM Consortium).", "proprietary", True),
    ("Free for any use (JRC).", "proprietary", True),
    ("Free for use (JRC).", "proprietary", True),
    ("Free with citation (OSU Greenland Mapping Project).", "proprietary", True),
    ("Free with citation (Yamazaki et al., 2017).", "proprietary", True),
    ("Free with citation (Yamazaki et al., 2019).", "proprietary", True),
    ("Free with citation (NASA NSIDC).", "proprietary", True),
    ("Free with citation (CPOM).", "proprietary", True),
    ("Free with citation.", "proprietary", True),
    ("Free for use with attribution (Colorado School of Mines).", "proprietary", True),
    ("Free for use with citation (PML_V2).", "proprietary", True),
    ("Free for use with citation (PKU REL).", "proprietary", True),
    ("Free for use with citation (CSIRO).", "proprietary", True),
    ("Free for use (CSIRO).", "proprietary", True),
    ("Free for use (ORNL).", "proprietary", True),
    ("Free for use (Nature Trace).", "proprietary", True),
    ("Free for use (Global Pasture Watch).", "proprietary", True),
    ("ORNL LandScan terms — free for non-commercial use with citation.", "proprietary", True),
    ("Use of this dataset is subject to the [Brazil Forest Imagery Dataset 2008 license", "proprietary", True),
    ("Use of this data is subject to [MethaneSAT's Content License Terms of", "proprietary", True),
    ("The Food and Agriculture Organization of the United Nations (FAO) is", "proprietary", True),
    ("The GAUL dataset is distributed to the United Nations and other authorized", "proprietary", True),
    ("Citation to the paper is adequate if you simply use MERIT Hydro", "proprietary", True),
    ("PROBA-V 300m and 100m data are freely available", "proprietary", True),
    ("This dataset is freely available with no restrictions.", "proprietary", True),
    ("This dataset is released for use under Service Level Agreement (SLA),", "proprietary", True),
    ("This dataset is made available publicly under the Creative Commons by", "CC-BY-SA-4.0", True),
    ("All data here is produced under the Copernicus Programme and is provided", "proprietary", True),
    ("National Science Foundation (PGC's primary funding source) policy requires", "proprietary", True),
    ("This is a human-readable summary of (and not a substitute for) the [license](https://creativecommons.org/licenses/by-sa/4.0/).", "CC-BY-SA-4.0", False),
    ("geoBoundaries datasets are provided under the CC BY 4.0 license", "CC-BY-4.0", True),
    ("The HydroATLAS database is licensed under a Creative Commons Attribution", "CC-BY-4.0", True),
    ("This work is licensed under a Creative Commons Attribution-ShareAlike 4.0", "CC-BY-SA-4.0", True),
    ("This work is licensed under the Creative Commons Attribution Non Commercial 4.0", "CC-BY-NC-4.0", True),
    ("This dataset is licensed under a Creative Commons Attribution 4.0", "CC-BY-4.0", True),
    ("This work is licensed under a Creative Commons Attribution 4.0", "CC-BY-4.0", True),
    ("Licensed under the Creative Commons Attribution 4.0 International License.", "CC-BY-4.0", False),
    ("Anyone can use this data free of charge subject to non-commercial use only.", "proprietary", True),
    ("Mention the name of the Licensor (the National Land Survey of Finland),", "proprietary", True),
    ("European primary forest datasets are provided under the CC BY 4.0", "CC-BY-4.0", True),
    ("Global Ecosystem Typology datasets are provided under the CC BY 4.0 license,", "CC-BY-4.0", True),
    ("The data may be used by anyone, anywhere, anytime without permission,", "public-domain", True),
    ("You are free to: copy, publish, distribute and transmit the Information;", "proprietary", True),
    ("The user must ensure that the source note contains the following", "proprietary", True),
    ("The dataset is under a Creative Commons Attribution 4.0 International", "CC-BY-4.0", True),
    ("This dataset is openly shared, without restriction, in accordance with", "public-domain", True),
    ("This dataset is openly shared, without restriction, in accordance with the", "public-domain", True),
    ("The data is free to use for commercial and non-commercial purposes for a", "proprietary", True),
    ("The data is free and free use for any legitimate purpose,", "proprietary", True),
    ("The data is provided free of charge by the Copernicus Marine Service.", "proprietary", True),
    ("The free geodata and geoservices of swisstopo may be used, distributed and", "proprietary", True),
    ("The User is entitled to combine the Source Data with other data, use it in", "proprietary", True),
    ("You may download and use photographs, imagery, or text", "public-domain", True),
    ("For the creation of any reports, publications, new", "proprietary", True),
    ("Intellectual property rights to this dataset belong to", "proprietary", True),
    ("Intellectual property rights to this dataset belong to University of", "proprietary", True),
    ("The MACA datasets were created with funding from the", "public-domain", True),
    ("The Digital Object Identifier [doi:10.5066/P955KPLE]", "public-domain", True),
    ("Please visit the [full terms and conditions page](https://www.protectedplanet.net/c/terms-and-conditions)", "proprietary", True),
    ("The GlobCover products have been processed by ESA and by the Université", "proprietary", True),
    ("The USDA Forest Service makes no warranty, expressed or implied, including the warranties", "public-domain", True),
    ("The USDA Forest Service makes no warranty, expressed or implied, including the", "public-domain", True),
    ("The USDA Forest Service makes no warranty, expressed or implied, including", "public-domain", True),
    ("This work \"Iran Land Cover Map", "proprietary", True),
    ("NASA NSIDC DAAC — public, no restrictions.", "public-domain", False),
    ("Free for any use with attribution.", "proprietary", True),
    ("Free for any use (UMD/GLAD).", "proprietary", True),
    ("Free for use; cite the underlying CMIP6 GCM.", "proprietary", True),
    ("Colorado School of Mines data, information, and products,", "proprietary", True),
    ("Anyone can use this data free of charge subject to the to the attribution", "proprietary", True),
    ("Anyone can use this data free of charge subject to the attribution", "proprietary", True),
    ("Use a custom URL for the non-standard license", "proprietary", True),
    ("Licensed under the\n", "unknown", True),
    ("Licensed under the", "unknown", True),
    ("The use of Sentinel data is governed by the [Copernicus", "proprietary", True),
    ("The UN Geodata is a global geospatial database available for use by the UN", "proprietary", True),
    ("The images are made available with a CC BY 4.0 license. The user is required", "CC-BY-4.0", True),
    ("Acknowledgements", "unknown", True),
]


def _classify(text: str) -> tuple[str, str | None]:
    """Return ``(license_id, terms_note_or_None)`` for one terms-string.

    First-match-wins via the ``_RULES`` substring table.
    """
    stripped = text.strip()
    if not stripped:
        return ("unknown", None)
    for needle, license_id, keep_note in _RULES:
        if needle.lower() in stripped.lower():
            return (license_id, stripped if keep_note else None)
    return ("unknown", stripped)


_TERMS_LINE = re.compile(r"^(\s+)terms:\s*(.*?)\s*$")


def _rewrite(text: str) -> tuple[str, dict[str, int]]:
    """Walk every line, replace ``    terms: <foo>`` with license/terms_note.

    Multi-line terms strings (with continuation lines, single-quoted)
    aren't currently used in the catalog — every ``terms:`` value fits on
    one line. The regex captures the indent so the output keeps the same
    indentation level.
    """
    out_lines: list[str] = []
    counts: dict[str, int] = {}
    for line in text.splitlines(keepends=True):
        m = _TERMS_LINE.match(line.rstrip("\n"))
        if not m:
            out_lines.append(line)
            continue
        indent, raw = m.groups()
        raw = raw.strip()
        # Strip surrounding single quotes if present (YAML single-quoted scalar)
        if raw.startswith("'") and raw.endswith("'") and len(raw) >= 2:
            raw_unquoted = raw[1:-1].replace("''", "'")
        else:
            raw_unquoted = raw
        license_id, note = _classify(raw_unquoted)
        counts[license_id] = counts.get(license_id, 0) + 1
        out_lines.append(f"{indent}license: {license_id}\n")
        if note:
            # Single-quote the note; escape internal single quotes by doubling
            escaped = note.replace("'", "''")
            out_lines.append(f"{indent}terms_note: '{escaped}'\n")
    return "".join(out_lines), counts


def main() -> int:
    totals: dict[str, int] = {}
    files = sorted(p for p in CATALOG_DIR.glob("*.yaml") if p.name != "_index.yaml")
    for path in files:
        text = path.read_text(encoding="utf-8")
        new_text, counts = _rewrite(text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
        for license_id, n in counts.items():
            totals[license_id] = totals.get(license_id, 0) + n
    total = sum(totals.values())
    print(f"rewrote {total} terms: lines across {len(files)} files")
    for license_id, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {license_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
