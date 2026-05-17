"""Generate one Jupyter notebook per `src/earthlens/gee/catalog/*.yaml`.

One notebook per catalog category, each showing the
metadata-explore -> tiny-AOI download -> quick plot path against a
representative dataset of that category. Each notebook reads the
service-account credentials from `GEE_SERVICE_ACCOUNT` / `GEE_SERVICE_KEY`
environment variables.

Re-run this script (then `jupyter nbconvert --execute *.ipynb`) to
refresh all notebooks at once after the catalog changes shape or a
representative dataset moves.
"""

from __future__ import annotations

import json
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).parent

#: One config per catalog file. `band` is whichever single band makes
#: the simplest preview (so the notebook plot can use one-band logic).
#: AOIs are kept small enough that the GeoTIFF write completes in a few
#: seconds even at native resolution. `scale_m` is set to keep the
#: synchronous URL download under EE's 32768-px per-axis cap.
CATALOGS: list[dict] = [
    {
        "category": "atmosphere-chemistry",
        "title": "Atmosphere & chemistry — Sentinel-5P NO2",
        "asset_id": "COPERNICUS/S5P/NRTI/L3_NO2",
        "band": "tropospheric_NO2_column_number_density",
        "lat_lim": [48.5, 49.5],
        "lon_lim": [2.0, 3.0],
        "start": "2024-01-05",
        "end": "2024-01-10",
        "temporal_resolution": "raw",
        "scale_m": 7000.0,
        "reducer": "mean",
        "blurb": (
            "Tropospheric NO2 column density from TROPOMI on Sentinel-5P "
            "(near-real-time, ~7 km native pixel). A short window over "
            "Paris, reduced to one composite scene."
        ),
    },
    {
        "category": "climate-reanalysis",
        "title": "Climate reanalysis — ERA5-Land monthly 2 m temperature",
        "asset_id": "ECMWF/ERA5_LAND/MONTHLY_AGGR",
        "band": "temperature_2m",
        "lat_lim": [29.0, 32.0],
        "lon_lim": [30.0, 33.0],
        "start": "2023-01-01",
        "end": "2023-12-31",
        "temporal_resolution": "yearly",
        "scale_m": 11132.0,
        "reducer": "mean",
        "blurb": (
            "ERA5-Land monthly aggregates resampled to a yearly mean "
            "2 m temperature over the Nile delta — a typical "
            "climate-reanalysis pipeline."
        ),
    },
    {
        "category": "community",
        "title": "Community — Planet NICFI 2024 basemap",
        "asset_id": "projects/planet-nicfi/assets/basemaps/africa",
        "band": "R",
        "lat_lim": [-1.5, -1.4],
        "lon_lim": [29.5, 29.6],
        "start": "2024-01-01",
        "end": "2024-03-31",
        "temporal_resolution": "raw",
        "scale_m": 100.0,
        "reducer": "median",
        "blurb": (
            "Planet NICFI tropical basemap (one of the public "
            "user-contributed `projects/...` assets in the catalog) over a "
            "small AOI in central Africa. Falls back to a catalog-only "
            "exploration if EE returns a permission error."
        ),
        "tolerate_ee_error": True,
    },
    {
        "category": "elevation-terrain",
        "title": "Elevation & terrain — SRTM 30 m",
        "asset_id": "USGS/SRTMGL1_003",
        "band": "elevation",
        "lat_lim": [29.9, 30.1],
        "lon_lim": [31.1, 31.3],
        "start": "2000-02-11",
        "end": "2000-02-12",
        "temporal_resolution": "raw",
        "scale_m": 90.0,
        "reducer": "mean",
        "blurb": (
            "NASA SRTM Global 1 arc-second DEM over the Giza plateau — a "
            "single static `ee.Image`, so the pipeline composites the "
            "one image and writes it as a GeoTIFF."
        ),
    },
    {
        "category": "hydrology-water",
        "title": "Hydrology & water — JRC Global Surface Water (v1.4)",
        "asset_id": "JRC/GSW1_4/GlobalSurfaceWater",
        "band": "occurrence",
        "lat_lim": [29.9, 30.1],
        "lon_lim": [31.1, 31.3],
        "start": "1984-03-16",
        "end": "1984-03-17",
        "temporal_resolution": "raw",
        "scale_m": 90.0,
        "reducer": "mean",
        "blurb": (
            "JRC's Global Surface Water occurrence band (1984-2021) over a "
            "small AOI near the Nile delta — a static `ee.Image` with no "
            "temporal cadence."
        ),
    },
    {
        "category": "land-cover-change",
        "title": "Land cover & change — ESA WorldCover v200 (2021)",
        "asset_id": "ESA/WorldCover/v200",
        "band": "Map",
        "lat_lim": [29.9, 30.1],
        "lon_lim": [31.1, 31.3],
        "start": "2021-01-01",
        "end": "2021-12-31",
        "temporal_resolution": "raw",
        "scale_m": 30.0,
        "reducer": "mosaic",
        "blurb": (
            "ESA WorldCover 2021 (10 m, 11 classes) — a one-image "
            "land-cover map; the default reducer is `mosaic` because the "
            "collection is tiled rather than time-stepped."
        ),
    },
    {
        "category": "optical-multispectral",
        "title": "Optical / multispectral — Sentinel-2 SR Harmonized",
        "asset_id": "COPERNICUS/S2_SR_HARMONIZED",
        "band": "B4",
        "lat_lim": [29.95, 30.05],
        "lon_lim": [31.15, 31.25],
        "start": "2024-06-01",
        "end": "2024-06-30",
        "temporal_resolution": "monthly",
        "scale_m": 30.0,
        "reducer": "median",
        "blurb": (
            "Sentinel-2 Surface Reflectance Harmonized — June 2024 median "
            "of the red band (`B4`) over Giza at 30 m. Median reduction "
            "is the canonical cloud-screened composite."
        ),
    },
    {
        "category": "other",
        "title": "Other — CIESIN GPWv4 population density",
        "asset_id": "CIESIN/GPWv4/population-density",
        "band": "population-density",
        "lat_lim": [29.0, 32.0],
        "lon_lim": [29.0, 33.0],
        "start": "2020-01-01",
        "end": "2020-12-31",
        "temporal_resolution": "raw",
        "scale_m": 5000.0,
        "reducer": "mean",
        "blurb": (
            "Gridded Population of the World v4 (CIESIN, ~1 km) over the "
            "Nile delta, reduced to a single mean over the 2020 entry."
        ),
    },
    {
        "category": "precipitation",
        "title": "Precipitation — CHIRPS daily rainfall",
        "asset_id": "UCSB-CHG/CHIRPS/DAILY",
        "band": "precipitation",
        "lat_lim": [-2.0, 2.0],
        "lon_lim": [29.0, 33.0],
        "start": "2024-06-01",
        "end": "2024-06-30",
        "temporal_resolution": "monthly",
        "scale_m": 5566.0,
        "reducer": "mean",
        "blurb": (
            "CHIRPS daily rainfall (5.5 km, 1981-present) over the Lake "
            "Victoria region, reduced to one monthly mean over June 2024."
        ),
    },
    {
        "category": "sar-radar",
        "title": "SAR / radar — Sentinel-1 GRD VV backscatter",
        "asset_id": "COPERNICUS/S1_GRD",
        "band": "VV",
        "lat_lim": [29.95, 30.05],
        "lon_lim": [31.15, 31.25],
        "start": "2024-06-01",
        "end": "2024-06-30",
        "temporal_resolution": "monthly",
        "scale_m": 30.0,
        "reducer": "mean",
        "blurb": (
            "Sentinel-1 Ground Range Detected, VV-polarisation, monthly "
            "mean over Giza. Sentinel-1 carries `VV`/`VH` over land and "
            "`HH`/`HV` over polar regions; `VV` is the universal pick."
        ),
    },
]


def _md(source: str) -> dict:
    return nbf.v4.new_markdown_cell(source.strip())


def _code(source: str) -> dict:
    return nbf.v4.new_code_cell(source.strip())


def _build(cfg: dict) -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb["metadata"] = {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python"},
    }

    asset_id = cfg["asset_id"]
    band = cfg["band"]
    tolerate = cfg.get("tolerate_ee_error", False)

    cells = [
        _md(f"# {cfg['title']}\n\n{cfg['blurb']}"),
        _md(
            "## Setup\n\n"
            "The notebook reads the GEE service-account credentials from "
            "`GEE_SERVICE_ACCOUNT` / `GEE_SERVICE_KEY` environment variables. "
            "Both must be set before running this cell."
        ),
        _code(
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "import matplotlib.pyplot as plt\n"
            "import numpy as np\n"
            "from pyramids.dataset import Dataset as PyramidsDataset\n"
            "\n"
            "from earthlens.gee import GEE, Catalog\n"
            "\n"
            f"OUT_DIR = Path('out') / {cfg['category']!r}\n"
            "OUT_DIR.mkdir(parents=True, exist_ok=True)\n"
            "\n"
            "SERVICE_ACCOUNT = os.environ['GEE_SERVICE_ACCOUNT']\n"
            "SERVICE_KEY = os.environ['GEE_SERVICE_KEY']\n"
            "print(f'output directory: {OUT_DIR.resolve()}')"
        ),
        _md(
            "## Inspect the catalog entry\n\n"
            "Before downloading anything, look at what the bundled catalog "
            "knows about the asset — bands, cadence, license, provider."
        ),
        _code(
            "cat = Catalog()\n"
            f"ds = cat.get_dataset({asset_id!r})\n"
            "print(f'title:               {ds.title}')\n"
            "print(f'ee_type:             {ds.ee_type}')\n"
            "print(f'spatial_resolution:  {ds.spatial_resolution} m')\n"
            "print(f'extent.start_date:   {ds.extent.start_date}')\n"
            "print(f'extent.end_date:     {ds.extent.end_date}')\n"
            "print(f'default_reducer:     {ds.default_reducer}')\n"
            "print(f'license:             {ds.license}')\n"
            "print(f'provider:            {ds.provider}')\n"
            f"print(f'#bands:              {{len(ds.bands)}}')\n"
            f"print(f'band ids (first 5):  {{list(ds.bands)[:5]}}')"
        ),
        _md(
            f"## Download\n\n"
            f"Tiny AOI ({cfg['lat_lim']} lat, {cfg['lon_lim']} lon) at "
            f"{cfg['scale_m']} m, `{cfg['temporal_resolution']}` cadence — "
            f"keeps the synchronous download under EE's 32768-px per-axis cap."
        ),
        _code(
            "try:\n"
            "    gee = GEE(\n"
            f"        start={cfg['start']!r},\n"
            f"        end={cfg['end']!r},\n"
            f"        variables={{{asset_id!r}: [{band!r}]}},\n"
            f"        lat_lim={cfg['lat_lim']},\n"
            f"        lon_lim={cfg['lon_lim']},\n"
            f"        temporal_resolution={cfg['temporal_resolution']!r},\n"
            "        path=str(OUT_DIR),\n"
            "        service_account=SERVICE_ACCOUNT,\n"
            "        service_key=SERVICE_KEY,\n"
            f"        scale={cfg['scale_m']},\n"
            f"        reducer={cfg['reducer']!r},\n"
            "    )\n"
            "    paths = gee.download(progress_bar=False)\n"
            "    print(f'wrote {len(paths)} GeoTIFF(s):')\n"
            "    for p in paths:\n"
            "        print(f'  {p}  ({p.stat().st_size / 1024:.1f} KB)')\n"
            "    download_ok = True\n"
            "except Exception as exc:\n"
            f"    if {tolerate!r}:\n"
            "        print(f'live EE call failed (tolerated for this category): {type(exc).__name__}: {exc}')\n"
            "        paths = []\n"
            "        download_ok = False\n"
            "    else:\n"
            "        raise"
        ),
        _md(
            "## Quick preview\n\n"
            "Load the first written GeoTIFF through pyramids and render the "
            "single band. (`pyramids.dataset.Dataset` is the project's "
            "GeoTIFF/NetCDF wrapper.)"
        ),
        _code(
            "if not download_ok or not paths:\n"
            "    print('Skipping preview — no GeoTIFF was written.')\n"
            "else:\n"
            "    pds = PyramidsDataset.read_file(str(paths[0]))\n"
            "    arr = pds.read_array()\n"
            "    if arr.ndim == 3:\n"
            "        arr = arr[0]\n"
            "    # Mask the dataset's nodata so the colormap doesn't get pinned to it.\n"
            "    nodata = pds.no_data_value\n"
            "    if nodata is not None:\n"
            "        try:\n"
            "            arr = np.ma.masked_equal(arr, float(nodata[0] if isinstance(nodata, (list, tuple)) else nodata))\n"
            "        except (TypeError, ValueError):\n"
            "            pass\n"
            "    fig, ax = plt.subplots(figsize=(6, 5))\n"
            "    im = ax.imshow(arr, cmap='viridis')\n"
            f"    ax.set_title(f'{{{asset_id!r}}} / {{{band!r}}}')\n"
            "    ax.set_xlabel('x (px)')\n"
            "    ax.set_ylabel('y (px)')\n"
            "    fig.colorbar(im, ax=ax, shrink=0.8)\n"
            "    plt.tight_layout()\n"
            "    plt.show()\n"
            "    print(f'value range: [{float(np.nanmin(arr)):.4g}, {float(np.nanmax(arr)):.4g}]')"
        ),
        _md(
            "## Tracking submitted jobs (asynchronous export)\n\n"
            "The download above uses `export_via=\"url\"` — a synchronous "
            "`getDownloadURL` round-trip. Nothing was queued, so there's "
            "no Earth Engine job to track.\n\n"
            "To track an export instead, switch to an asynchronous sink "
            "(`drive` / `gcs` / `asset`) and pass `wait_for_export=False` "
            "so `.download()` returns a `TaskInfo` at submission time "
            "rather than blocking until completion. The cells below "
            "submit the same `(asset_id, band, AOI, scale)` request as "
            "an `export_via=\"asset\"` task into the service account's own "
            "asset folder, then walk the four jobs-API calls (`list_recent_tasks` "
            "→ `wait_for_task_id` → `ee.data.getAsset` → `ee.data.deleteAsset`) "
            "to make the job finish *and* tidy up. See "
            "`track-batch-exports.ipynb` for a deeper worked example."
        ),
        _code(
            "import ee\n"
            "from earthlens.gee import list_recent_tasks, wait_for_task_id, cancel_task\n"
            "\n"
            "# The asset goes into a `Folder` asset that we own. `GEE._export_via_batch`\n"
            "# writes the actual image at `<asset_id>/<prefix>`, so `asset_id` here is\n"
            "# the parent FOLDER (not the final image path). Both must be cleaned up.\n"
            "_proj = ee.data._get_projects_path().removeprefix('projects/')\n"
            f"DEMO_FOLDER = f'projects/{{_proj}}/assets/earthlens-demo-{cfg['category']}'\n"
            "print(f'demo folder: {DEMO_FOLDER}')\n"
            "\n"
            "# Best-effort cleanup of leftover children from a previous run (so the\n"
            "# folder is empty before we try to delete it below).\n"
            "try:\n"
            "    for child in ee.data.listAssets({'parent': DEMO_FOLDER}).get('assets', []):\n"
            "        ee.data.deleteAsset(child['name'])\n"
            "        print(f'cleared leftover child: {child[\"name\"]}')\n"
            "    ee.data.deleteAsset(DEMO_FOLDER)\n"
            "    print(f'cleared leftover folder: {DEMO_FOLDER}')\n"
            "except Exception:\n"
            "    pass\n"
            "# Create the parent folder — EE requires it to exist before a child write.\n"
            "ee.data.createAsset({'type': 'Folder'}, DEMO_FOLDER)\n"
            "print(f'created folder: {DEMO_FOLDER}')"
        ),
        _md(
            "### Submit\n\n"
            "Same `(asset_id, band, AOI, scale)` request as the sync download "
            "above, just routed through `export_via=\"asset\"` + "
            "`wait_for_export=False`. `download()` returns a `TaskInfo` per "
            "submitted bucket at the moment the task is queued — no blocking."
        ),
        _code(
            "submitted_ok = False\n"
            "task_info = None\n"
            "try:\n"
            "    async_gee = GEE(\n"
            f"        start={cfg['start']!r},\n"
            f"        end={cfg['end']!r},\n"
            f"        variables={{{asset_id!r}: [{band!r}]}},\n"
            f"        lat_lim={cfg['lat_lim']},\n"
            f"        lon_lim={cfg['lon_lim']},\n"
            f"        temporal_resolution={cfg['temporal_resolution']!r},\n"
            "        path=str(OUT_DIR),\n"
            "        service_account=SERVICE_ACCOUNT,\n"
            "        service_key=SERVICE_KEY,\n"
            f"        scale={cfg['scale_m']},\n"
            f"        reducer={cfg['reducer']!r},\n"
            "        export_via='asset',\n"
            "        asset_id=DEMO_FOLDER,\n"
            "        wait_for_export=False,\n"
            "    )\n"
            "    submitted = async_gee.download(progress_bar=False)\n"
            "    task_info = submitted[0]\n"
            "    submitted_ok = True\n"
            "    print(f'submitted: id={task_info.id} state={task_info.state}')\n"
            "    print(f'           description={task_info.description}')\n"
            "except Exception as exc:\n"
            f"    if {tolerate!r}:\n"
            "        print(f'async submission failed (tolerated): {type(exc).__name__}: {exc}')\n"
            "    else:\n"
            "        raise"
        ),
        _md(
            "### List + wait\n\n"
            "`list_recent_tasks(description_prefix=...)` returns every "
            "matching task across the current project; "
            "`wait_for_task_id` blocks until the one we care about reaches "
            "a terminal state. A real workflow would just poll later from "
            "a separate process — the wait here exists so the notebook "
            "shows the full success path end-to-end."
        ),
        _code(
            "if submitted_ok and task_info is not None:\n"
            "    recent = list_recent_tasks(\n"
            "        description_prefix=task_info.description,\n"
            "        max_age_min=10,\n"
            "    )\n"
            "    print(f'list_recent_tasks matched {len(recent)} task(s):')\n"
            "    for t in recent:\n"
            "        print(f'  {t.id}  {t.state:<12} {t.description}')\n"
            "    try:\n"
            "        final = wait_for_task_id(\n"
            "            task_info.id, poll_seconds=10, progress_bar=False,\n"
            "        )\n"
            "        print(f'\\nfinal state: {final.state}')\n"
            "    except RuntimeError as exc:\n"
            "        # Raised on FAILED / CANCELLED — cancel-if-still-running\n"
            "        # so we don't leak an in-flight task on notebook restart.\n"
            "        print(f'wait_for_task_id raised: {exc}')\n"
            "        try:\n"
            "            cancel_task(task_info.id)\n"
            "        except Exception:\n"
            "            pass\n"
            "else:\n"
            "    print('Skipping list/wait — async submission did not succeed.')"
        ),
        _md(
            "### Verify + clean up\n\n"
            "Confirm the produced asset exists on Earth Engine, then delete "
            "it (and the surrounding demo folder) so we don't leak storage "
            "between notebook runs. The backend wrote the image at "
            "`<DEMO_FOLDER>/<task description>`."
        ),
        _code(
            "if submitted_ok and task_info is not None:\n"
            "    produced = f'{DEMO_FOLDER}/{task_info.description}'\n"
            "    try:\n"
            "        meta = ee.data.getAsset(produced)\n"
            "        print(f'asset exists: type={meta.get(\"type\")} '\n"
            "              f'name={meta.get(\"name\")}')\n"
            "        ee.data.deleteAsset(produced)\n"
            "        print('asset deleted')\n"
            "    except Exception as exc:\n"
            "        print(f'verify/delete skipped: {exc}')\n"
            "# Always try to tear down the parent folder.\n"
            "try:\n"
            "    ee.data.deleteAsset(DEMO_FOLDER)\n"
            "    print(f'folder deleted: {DEMO_FOLDER}')\n"
            "except Exception as exc:\n"
            "    print(f'folder delete skipped: {exc}')"
        ),
        _md(
            "## What's on disk\n\n"
            "The GeoTIFF (or empty list, if the EE call was tolerated) "
            "is left under the per-notebook `out/` directory for you to "
            "inspect. That directory is `.gitignore`d — re-running the "
            "notebook overwrites it."
        ),
        _code(
            "for p in sorted(OUT_DIR.iterdir()) if OUT_DIR.exists() else []:\n"
            "    print(f'{p}  ({p.stat().st_size / 1024:.1f} KB)')"
        ),
    ]
    nb["cells"] = cells
    return nb


def main() -> None:
    for cfg in CATALOGS:
        nb = _build(cfg)
        out = HERE / f"{cfg['category']}.ipynb"
        with out.open("w", encoding="utf-8") as f:
            nbf.write(nb, f)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
