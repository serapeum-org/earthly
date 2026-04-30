"""Demonstrate the pyramids ``NetCDF.meta_data.variables`` bug.

Reproduces the issue we hit during the CDS catalog autopilot work:
``pyramids.NetCDF.read_file(...).meta_data.variables`` only enumerates
*top-level* netCDF variables that happen to be exposed by the GDAL
NetCDF driver as bands. When a file packs its real data layers as
**subdatasets** (the GDAL-side way of exposing 3-D arrays such as
[time, lat, lon] grids), pyramids skips them and reports only the
lat / lon / time coordinate variables.

For us this manifested as the ``derived-utci-historical`` probe
returning 0-2 "data variables" (just lat / lon), even though the same
NetCDF held 22 derived UTCI statistics. The fix is to walk
``GetSubDatasets()`` recursively and read units / long_name from the
metadata of each subdataset, not just from the parent dataset's bands.

The script downloads nothing; it expects the cached UTCI probe at
``C:/tmp/cds_probe/_re_extract/derived-utci-historical/`` (created by
``tools/download_probe_results.py`` during the autopilot work).

Run::

    pixi run -e dev python tools/demo_pyramids_subdataset_bug.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from osgeo import gdal

gdal.UseExceptions()

# Skip-list copied from tools/probe_cds_netcdf.py -these are coords, not data.
COORD_SKIP = {
    "latitude",
    "longitude",
    "lat",
    "lon",
    "time",
    "valid_time",
    "time_bnds",
    "number",
    "expver",
    "realization",
    "forecast_period",
    "forecast_reference_time",
    "vertices_latitude",
    "vertices_longitude",
}

DEMO_FILE = Path(
    "C:/tmp/cds_probe/_re_extract/derived-utci-historical/"
    "ECMWF_utci_yearly_stats_1940_v1.1_con.nc"
)


def pyramids_style_enumeration(nc_path: Path) -> dict[str, dict[str, str]]:
    """Mimic ``pyramids.NetCDF.read_file(...).meta_data.variables``.

    Internally pyramids opens the NetCDF and iterates the bands GDAL
    exposes at the top level. For files whose data lives in
    subdatasets, this band list is just the 1-D coordinate variables.
    """
    out: dict[str, dict[str, str]] = {}
    ds = gdal.Open(str(nc_path))
    if ds is None:
        return out
    md = ds.GetMetadata()
    seen: set[str] = set()
    for key in md:
        if "#" not in key:
            continue
        var = key.split("#", 1)[0]
        if var in seen or var in COORD_SKIP or var.startswith("NC_GLOBAL"):
            continue
        seen.add(var)
        out[var] = {
            "long_name": md.get(f"{var}#long_name", ""),
            "units": md.get(f"{var}#units", ""),
        }
    return out


def correct_subdataset_enumeration(nc_path: Path) -> dict[str, dict[str, str]]:
    """Fixed version: walk every NetCDF subdataset GDAL reports.

    For each subdataset, open it as a separate dataset and read the
    variable's units / long_name directly from its metadata. This is
    the only way to surface 3-D data layers (the actual fields the
    user cares about).
    """
    out: dict[str, dict[str, str]] = {}
    ds = gdal.Open(f'NETCDF:"{nc_path}"')
    if ds is None:
        return out
    sub_list = ds.GetSubDatasets()
    if not sub_list:
        # No subdatasets: this *is* a band-style NetCDF, fall back
        # to the same enumeration pyramids does.
        return pyramids_style_enumeration(nc_path)
    for sub_uri, _description in sub_list:
        # Each URI looks like NETCDF:"path/to.nc":var_name. Pull the
        # variable name back out and skip coords / bounds.
        var_name = sub_uri.rsplit(":", 1)[-1]
        if var_name in COORD_SKIP or "_bnds" in var_name:
            continue
        sub = gdal.Open(sub_uri)
        if sub is None:
            continue
        sub_md = sub.GetMetadata()
        out[var_name] = {
            "long_name": sub_md.get(f"{var_name}#long_name", ""),
            "units": sub_md.get(f"{var_name}#units", ""),
        }
    return out


def _format(label: str, mapping: dict[str, dict[str, str]]) -> str:
    if not mapping:
        return f"{label}: (empty -no data variables found)"
    lines = [f"{label} -> {len(mapping)} variable(s):"]
    for name, attrs in sorted(mapping.items()):
        units = attrs["units"] or "?"
        ln = attrs["long_name"] or "?"
        lines.append(f"    {name:<35} units={units!r:<20} long_name={ln!r}")
    return "\n".join(lines)


def main() -> int:
    if not DEMO_FILE.exists():
        print(
            f"Demo file not found at {DEMO_FILE}.\n"
            "Run tools/download_probe_results.py during a CDS autopilot "
            "session, or substitute any UTCI / WFDE5 NetCDF that exposes "
            "subdatasets."
        )
        return 1

    print(f"File: {DEMO_FILE.name}\n")
    print(f"GDAL driver: {gdal.Open(str(DEMO_FILE)).GetDriver().LongName}")
    sub_count = len(gdal.Open(f'NETCDF:"{DEMO_FILE}"').GetSubDatasets())
    print(f"Subdataset count (the data layers GDAL exposes): {sub_count}\n")

    bug = pyramids_style_enumeration(DEMO_FILE)
    fix = correct_subdataset_enumeration(DEMO_FILE)

    print("--- Buggy behaviour (pyramids-style top-level enumeration) ---")
    print(_format("pyramids.NetCDF.meta_data.variables", bug))
    print()
    print("--- Correct behaviour (subdataset walk) ---")
    print(_format("GDAL subdatasets", fix))
    print()
    print("--- Diff ---")
    missed = set(fix) - set(bug)
    if missed:
        print(f"Pyramids missed {len(missed)} data variables:")
        for name in sorted(missed):
            print(f"    - {name}")
    else:
        print("No discrepancy on this file (subdataset count was 0).")

    print(
        "\nTakeaway: when GDAL reports subdatasets, the variables "
        "the user wants live there, NOT in the parent dataset's "
        "metadata block. Pyramids' meta_data.variables iterator must "
        "first call GetSubDatasets() and recurse into each subdataset "
        "before falling back to band-level enumeration."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
