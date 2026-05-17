# GEE per-catalog example notebooks

One Jupyter notebook per `src/earthlens/gee/catalog/*.yaml` category.
Each notebook walks the **synchronous URL** pipeline against a
representative dataset of that category, then re-submits the same
request through the **asynchronous asset sink** so the job-tracking
surface (`earthlens.gee.jobs`) gets demonstrated end-to-end against a
real task:

1. **Catalog inspect** — `Catalog().get_dataset(asset_id)` and read its
   bands, cadence, extent, license, default reducer.
2. **Sync download** — `GEE(...).download(progress_bar=False)` with a
   tiny AOI and a scale chosen to keep the synchronous URL download
   under Earth Engine's 32768-px per-axis cap. No job is queued — the
   GeoTIFF is streamed back over HTTP.
3. **Preview** — open the written GeoTIFF with `pyramids.dataset.Dataset`
   and render the single band with matplotlib.
4. **Track submitted jobs** — re-submit the same request as
   `export_via="asset"` with `wait_for_export=False`, list the task via
   `list_recent_tasks(description_prefix=...)`, poll it to `COMPLETED`
   via `wait_for_task_id`, verify the asset with
   `ee.data.getAsset`, then `ee.data.deleteAsset` so storage doesn't
   leak between runs. See `track-batch-exports.ipynb` for a
   deeper-dive notebook on the same surface.

## Notebooks

| File | Catalog | Asset id | Band shown |
|------|---------|----------|------------|
| `atmosphere-chemistry.ipynb` | atmosphere & chemistry | `COPERNICUS/S5P/NRTI/L3_NO2` | `tropospheric_NO2_column_number_density` |
| `climate-reanalysis.ipynb` | climate reanalysis | `ECMWF/ERA5_LAND/MONTHLY_AGGR` | `temperature_2m` |
| `community.ipynb` | community (`projects/...`) | `projects/planet-nicfi/assets/basemaps/africa` | `R` |
| `elevation-terrain.ipynb` | elevation & terrain | `USGS/SRTMGL1_003` | `elevation` |
| `hydrology-water.ipynb` | hydrology & water | `JRC/GSW1_4/GlobalSurfaceWater` | `occurrence` |
| `land-cover-change.ipynb` | land cover & change | `ESA/WorldCover/v200` | `Map` |
| `optical-multispectral.ipynb` | optical / multispectral | `COPERNICUS/S2_SR_HARMONIZED` | `B4` |
| `other.ipynb` | other | `CIESIN/GPWv4/population-density` | `population-density` |
| `precipitation.ipynb` | precipitation | `UCSB-CHG/CHIRPS/DAILY` | `precipitation` |
| `sar-radar.ipynb` | SAR / radar | `COPERNICUS/S1_GRD` | `VV` |

## Running

Each notebook reads the GEE service-account credentials from the
environment:

```bash
export GEE_SERVICE_ACCOUNT="my-sa@my-project.iam.gserviceaccount.com"
export GEE_SERVICE_KEY="/path/to/service-account.json"
```

Then open any notebook in JupyterLab / VS Code and run all cells, or
execute the whole batch from the command line:

```bash
python examples/notebooks/gee/_execute.py
```

(or just one: `python examples/notebooks/gee/_execute.py elevation-terrain`).

The script re-writes each notebook in place with the new outputs.

## Regenerating the notebooks

The notebooks are emitted from a single config table in `_generate.py`
so a catalog rename / new representative dataset only needs the table
edited, then:

```bash
python examples/notebooks/gee/_generate.py
```

## Output directory

Each notebook writes to `out/<category>/` (gitignored). The directory
is created on first run and re-used on subsequent runs — re-running a
notebook overwrites the previous tile rather than wiping the
directory, so file handles held by the matplotlib preview don't trip
the cleanup on Windows.

## EE asset side-effects (jobs tracking section)

The "Track submitted jobs" section in each notebook creates an EE
asset at
`projects/<your-project>/assets/earthlens-demo-<category>` and deletes
it at the end of the same notebook run. If a previous run crashed
between submit and cleanup, the next run's first cell deletes any
leftover (best-effort) before re-submitting.

The asset path is derived from
`ee.data._get_projects_path()` — whichever project your EE SDK was
initialised on. To suppress the jobs-tracking demo entirely (e.g. for
a service account without asset-write permission), remove the four
trailing cells under the "Tracking submitted jobs" heading.
