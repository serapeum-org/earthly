# Using the Climate Hazards Center (CHC) backend

This page is the hands-on guide to the `earthlens` CHC backend —
picking a dataset from the catalog, building a download, and the
shape of the output. For background see the
[Introduction](introduction.md); for catalog internals see
[Catalog](catalog.md); the rendered API is on the
[Reference](chc.md) page.

> **Install:** the CHC backend has zero optional dependencies —
> `pip install earthlens` is enough. FTP support lives in Python's
> stdlib (`ftplib`); the rest of the chain (`pyramids`, `numpy`,
> `pandas`, `loguru`, `tqdm`, `joblib`) is part of the core install.

## 1. Find a dataset and its variables

The catalog (per-family `src/earthlens/chc/catalog/*.yaml` files,
loaded and merged by `earthlens.chc.Catalog`) maps dataset keys to
their FTP layout, spatial/temporal extent, and per-variable
metadata. Browse it from Python:

```python
from earthlens.chc import Catalog

cat = Catalog()
len(cat.datasets)                       # 97 curated entries
"global-daily" in cat.datasets          # True (CHIRPS-2.0 global daily)
"wbgt-monthly" in cat.datasets          # True (WBGT)

# Filter by region or temporal resolution
cat.list_datasets(region="africa")
# ['africa-2-monthly', 'africa-3-monthly', 'africa-6-hourly',
#  'africa-daily', 'africa-dekad', 'africa-monthly', 'africa-pentad']

cat.list_datasets(temporal_resolution="monthly")
# ['africa-monthly', 'centennial-trends-v1-monthly',
#  'central-america-caribbean-monthly', 'chc-cmip6-...', ...]

# Inspect one row
ds = cat.get_dataset("global-daily")
ds.region                # 'global'
ds.temporal_resolution   # 'daily'
ds.pandas_freq           # 'D'
ds.spatial_resolution    # [0.05]
ds.lat_boundaries        # [-50.0, 50.0]
ds.start_date            # '1981-01-01'
list(ds.variables)       # ['precipitation']

cat.get_variable("global-daily", "precipitation").units   # 'mm/day'
```

`cat.available_datasets` is the informational walk-order list (matches
`cat.datasets.keys()` 1:1 after the H1 / centennial-trends fix).
`cat.available_regions` is a `dict[str, dict[str, list[float]]]`
mapping each named region to its `lat_boundaries` / `lon_boundaries`.

For a one-shot dump of a dataset's full metadata:

```python
cat.describe("centennial-trends-v1-monthly")
# {'dataset': 'centennial-trends-v1-monthly',
#  'region': 'east-africa-centennial',
#  'temporal_resolution': 'monthly',
#  'pandas_freq': 'MS',
#  'spatial_resolution': [0.1],
#  'formats': ['netcdf'],
#  'ftp_bases': {'netcdf': 'pub/org/chc/products/CentennialTrends/'},
#  'file_patterns': None,
#  'discrete_files': {'netcdf': ['CenTrends_v1_monthly.nc']},
#  'is_discrete': True,
#  'lat_boundaries': [-12.25, 22.25],
#  'lon_boundaries': [21.25, 51.25],
#  'start_date': '1900-01-01',
#  'end_date': '2014-12-31',
#  'preliminary': False,
#  'variables': ['precipitation']}
```

## 2. Download

Two `variables=` shapes are accepted:

### Dict shape (any catalog dataset)

The canonical shape, mirroring the ECMWF and GEE backends. The key
is a catalog dataset key, the value is the list of variable codes
to fetch from that dataset:

```python
from earthlens.chc import CHIRPS

CHIRPS(
    variables={"africa-pentad": ["precipitation"]},
    start="2020-01-01", end="2020-02-01",
    lat_lim=[-5.0, 5.0], lon_lim=[30.0, 40.0],
    path="data/chc",
).download()
# -> writes data/chc/africa-pentad_precipitation_2020.01.{01,06,11,16,21,26}.tif
#    plus data/chc/africa-pentad_precipitation_2020.02.01.tif
```

### List shape (legacy, CHIRPS-2.0 only)

For backwards compatibility with the original CHIRPS-2.0-global API.
The dataset key is **derived from `temporal_resolution`** via the
two-entry legacy table:

| `temporal_resolution` | resolved dataset key |
|---|---|
| `"daily"` | `global-daily` |
| `"monthly"` | `global-monthly` |

```python
CHIRPS(
    variables=["precipitation"],
    temporal_resolution="daily",
    start="2009-01-01", end="2009-01-31",
    lat_lim=[4.0, 5.0], lon_lim=[-75.0, -74.0],
    path="data/chc",
).download()
# -> writes data/chc/global-daily_precipitation_2009.01.{01..31}.tif
```

Any `temporal_resolution` value outside `{"daily", "monthly"}` paired
with the list shape raises `ValueError` — switch to the dict shape
for anything else.

## 3. Output filenames

`<root>/<dataset_key>_<variable>_<date>.tif`, with the date suffix
following the dataset's cadence:

| Cadence | Date suffix |
|---|---|
| `annual` | `YYYY` |
| `monthly` / `monthly-climatology` / `2-monthly` / `3-monthly` | `YYYY.MM` |
| `6-hourly` | `YYYY.MM.DD.HH` |
| everything else (daily, dekadal, pentadal, 5-day, 10-day, 15-day, …) | `YYYY.MM.DD` |

Per-date GeoTIFFs are written after bbox-clipping with the actual
raster geo-affine (no hardcoded 0.05° / ±50° assumption), so any CHC
pixel size (0.05° CHIRPS, 0.1° CenTrends, 1° WBGT) is clipped
correctly.

## 4. Parallel per-date downloads

Pass `cores=` to use joblib for the date loop:

```python
CHIRPS(
    variables={"global-daily": ["precipitation"]},
    start="2009-01-01", end="2009-12-31",
    lat_lim=[4.0, 5.0], lon_lim=[-75.0, -74.0],
    path="data/chc",
).download(cores=8)
```

`cores=None` (default) or `cores=0` runs sequentially. Sequential
mode shares **one anonymous FTP login** across the whole date loop
(one login per `(dataset, variable)` batch instead of one per file);
parallel mode keeps per-file logins because joblib workers can't
share the unpicklable FTP socket.

## 5. Discrete-files datasets

CHPclim v2 (a 12-file static climatology) and CenTrends v1 (multi-year
NetCDF archives) publish a **fixed enumerated set of files**, not a
per-date partition. The catalog declares them with `discrete_files:`
instead of `file_patterns:`; the backend iterates the file list once
per request rather than doing date substitution:

```python
CHIRPS(
    variables={"chpclim-v2-monthly": ["precipitation"]},
    start="1981-01-01", end="1981-12-31",   # ignored — CHPclim is static
    lat_lim=[0.0, 30.0], lon_lim=[-20.0, 50.0],
    path="data/chc",
).download()
# -> writes data/chc/chpclim-v2-monthly_CHPclim2.90-90.{01..12}.tif
#    (12 files, one per calendar month; the date range is honoured
#    only at the catalog-window-overlap check)
```

CenTrends multi-year NetCDFs are passed through unmodified — the 2-D
bbox-clip math doesn't apply to a `(time, lat, lon)` variable.
Read the saved file with `xarray.open_dataset(...).sel(time=..., lat=..., lon=...)`.

## 6. Failure tolerance

Per-date FTP failures (TCP reset, 550, brief DNS glitch, one bad
raster) **do not abort the rest of the batch**. They are accumulated
and surfaced as a single WARNING per `(dataset, variable)`:

```
WARNING  CHIRPS download for global-daily/precipitation ...
WARNING  global-daily/precipitation: 3/365 dates failed; first 3:
         2020-03-15 (FTPError), 2020-07-22 (TimeoutError), ...
INFO     CHIRPS download summary: all 1 variables succeeded ...
```

If the broader `(dataset, variable)` call fails outright (catalog
resolution error, can't even open the FTP), `download()` logs it as
ERROR and continues with the next variable. Across a long batch the
final summary tells you what succeeded and what didn't.

## 7. Via the `EarthLens` facade

The CHC backend is registered in the facade under two keys:

```python
from earthlens import EarthLens

# Canonical key
EarthLens(
    data_source="chc",
    variables={"global-daily": ["precipitation"]},
    start="2024-01-01", end="2024-01-07",
    lat_lim=[0.0, 1.0], lon_lim=[0.0, 1.0],
    path="data/chc",
).download()

# Back-compat alias (the package used to live at `earthlens.chirps`)
EarthLens(data_source="chirps", ...).download()
```

Both keys resolve to the same `CHIRPS` backend class. The `"chirps"`
alias is kept indefinitely so existing user code continues to work
after the `chirps/` → `chc/` rename.

## 8. Debugging

- **Empty output directory.** Check the WARNING summary line for
  per-date failures. The most common cause is a bbox that doesn't
  overlap the requested dataset's region — `_clip_to_bbox` raises
  `ValueError` (caught by the per-date guard and logged) naming both
  extents.
- **Every date fails with FTP 550.** Either the file pattern in the
  YAML is wrong (the GEFS v3 rows shipped with a known-bad pattern
  and were withdrawn — see `gefs.yaml`'s header for the probe
  results) or your bbox / date is outside the dataset's actual
  coverage. Try `Catalog().describe(ds_key)` to compare the
  request's window against the dataset's `start_date` / `end_date`.
- **No outbound FTP allowed.** Some corporate firewalls block port 21.
  Run `python -c "from ftplib import FTP; FTP('data.chc.ucsb.edu').login()"`
  from the target host to confirm reachability before reaching for
  the backend.
- **Inspect what the catalog ships:** the [Catalog](catalog.md) page
  has the full breakdown of files, schema, and per-family contents.
