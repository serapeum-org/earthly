# Data Catalog

Each data source provides some datasets/climate variables, and the `Catalog` class is the way to discover what data is available at a certain date at a specific location.

The data catalog is a dictionary with the available datasets as keys and the attributes that describe each dataset stored in a nested dictionary.

## CHIRPS

```python
from earthly.chirps import Catalog

chirps_catalog = Catalog()
print(chirps_catalog.catalog)
```

```python
{
    'Precipitation': {
        'descriptions': 'rainfall [mm/temporal_resolution]',
        'units': 'mm/temporal_resolution',
        'temporal resolution': ['daily', 'monthly'],
        'file name': 'rainfall',
        'var_name': 'R'
    }
}
```

## ECMWF (Copernicus Climate Data Store)

The ECMWF catalog is shipped as `cds_data_catalog.yaml` (package data)
and exposes a per-variable map keyed by slugified CDS variable names
(`evaporation`, `temperature`, `2m-temperature`, `total-precipitation`,
...). Each entry tells `ECMWF.api()` which CDS dataset hosts the
variable, the official CDS variable name, and the unit-conversion
factors used during post-processing.

```python
from earthly.ecmwf import Catalog

catalog = Catalog()
list(catalog.catalog)[:5]
```

```python
["2m-temperature", "2m-dewpoint-temperature", "surface-pressure", "total-precipitation", "evaporation"]
```

To get the attributes for a specific variable (e.g., 2-metre
temperature):

```python
catalog.get_variable("2m-temperature")
```

```python
{
    'cds_dataset': 'reanalysis-era5-single-levels',
    'cds_dataset_monthly': 'reanalysis-era5-single-levels-monthly-means',
    'cds_variable': '2m_temperature',
    'nc_variable': 't2m',
    'units': 'K',
}
```

Key reference:

- `cds_dataset` — CDS dataset short name used for daily / sub-daily
  retrieves.
- `cds_dataset_monthly` — optional, used when
  `temporal_resolution="monthly"`. Falls back to `cds_dataset` when
  absent.
- `cds_variable` — the CDS variable name passed to
  `client.retrieve()`. Also used as the output filename stem.
- `nc_variable` — short variable name inside the returned NetCDF
  (e.g. `t2m` for `2m_temperature`).
- `cds_pressure_level` — optional list of pressure levels (e.g.
  `["1000"]`). Present for pressure-level variables (`temperature`,
  `specific-humidity`, `relative-humidity`).
- `units` — raw ERA5 unit string emitted by CDS for this variable
  (used in the output filename).

The catalog ships **1364 entries across 34 datasets** as of the
current snapshot. ERA5-flavoured datasets contribute ~338 of those
across three core datasets:

- `reanalysis-era5-single-levels` — 261 atmospheric / surface
  variables on the global 0.25° ERA5 grid.
- `reanalysis-era5-pressure-levels` — 16 variables on the 1000 hPa
  level (extend `pressure_level:` in the dataset block to fetch more
  levels).
- `reanalysis-era5-land` — 60 land-surface variables on the
  higher-resolution 0.1° land-only grid; `monthly:` resolves to
  `reanalysis-era5-land-monthly-means`. Where a variable code
  (e.g. `2m-temperature`, `total-precipitation`) appears in both
  ERA5-Land and ERA5 single-levels, the flat
  `Catalog().get_variable(code)` resolves to ERA5 single-levels —
  the first dataset that declared the code in YAML order. To pull
  the ERA5-Land variant, pass the dataset explicitly:
  `Catalog().get_variable(code, dataset="reanalysis-era5-land")`.
  Every code that lives in more than one dataset is listed under
  `Catalog().duplicates` for audit.
- `derived-era5-land-daily-statistics` — 31 daily-aggregated state
  variables from ERA5-Land. Keys are the ERA5-Land code suffixed with
  `-daily` (e.g. `2m-temperature-daily`). The dataset-level `extras`
  carry sensible defaults (`daily_statistic: daily_mean`,
  `frequency: 1_hourly`, `time_zone: utc+00:00`); pass overrides via
  the per-row `extras:` map to fetch min / max / range or a different
  output frequency.
- `derived-era5-single-levels-daily-statistics` — 262 daily-aggregated
  variables from ERA5 single-levels. Same nc_variable and units as
  the underlying single-levels rows; daily aggregation selected at
  request time via `daily_statistic` (mean / min / max). Keys use
  the `-daily` suffix.
- `derived-era5-pressure-levels-daily-statistics` — 16 daily-aggregated
  variables from ERA5 pressure-levels (default `["1000"]` hPa).
  Same `daily_statistic` / `frequency` / `time_zone` extras as the
  single-levels variant.
- `projections-cmip5-monthly-single-levels` — 9 of 39 CMIP5 monthly
  surface variables (CMOR-named: `tas`, `pr`, `tasmax`, `tasmin`,
  `psl`, `rsds`, `sfcWind`, `huss`, `prsn`). Default extras pin
  the EC-Earth `r12i1p1` historical run (1950-2012); override
  per-request to switch model/scenario. Keys are `-cmip5m`
  suffixed.
- `projections-cmip5-monthly-pressure-levels` — 5 of 6 CMIP5
  monthly upper-air variables (`ta`, `ua`, `va`, `hus`, `zg`) at
  the default `["1000"]` hPa level. Same EC-Earth historical
  defaults; same `-cmip5m` suffix. `relative_humidity` is not
  present in EC-Earth's CMIP5 archive — probe a different model
  if needed. The CMIP5 daily variants
  (`projections-cmip5-daily-single-levels`,
  `projections-cmip5-daily-pressure-levels`) are listed in
  `available_datasets:` but are **not yet curated** — their
  probes were still queued at CDS when this row landed; add them
  in a follow-up PR.
- `projections-cordex-domains-single-levels` — 16 of 25 catalogued
  CORDEX regional climate projection variables. Default extras pin
  the EURO-CORDEX EC-Earth/RACMO22E historical run on the 0.11°
  grid (`domain: europe`, `experiment: historical`,
  `gcm_model: ichec_ec_earth`, `rcm_model: knmi_racmo22e`). Override
  per-request to switch scenario (`experiment`), model pair
  (`gcm_model`/`rcm_model`), or domain. Keys are suffixed with
  `-cordex` to avoid colliding with same-named ERA5 single-levels
  rows. The 9 missing vars (10m u/v wind, surface pressure, total
  runoff, 200hPa upper-air fields, land-area fraction, orography)
  are not present in the default model combination — probe a
  different gcm/rcm pair if you need them.
- `reanalysis-oras5` — 27-variable monthly ocean reanalysis (NEMO
  3.4.1 on the ORCA025 grid). 21 vars are 2-D surface fields; 6 vars
  (velocities, temperature, salinity, rotated velocities) are 3-D and
  carry `vertical_resolution: all_levels` per row. Dataset-level
  `extras` defaults to `product_type: [consolidated]` (1958-2014);
  override to `operational` for 2015-onwards. Note: ORAS5 returns
  NEMO short names (`vomecrty`, `votemper`, `vosaline`, …) rather
  than the ECMWF GRIB short names used elsewhere in the catalog.

Browse the full list of CDS dataset short names at
<https://cds.climate.copernicus.eu/datasets?q=era5>. To add a new
variable, append an entry to
`src/earthly/ecmwf/cds_data_catalog.yaml` following the schema
in the file's header comment.

`get_variable(var_name)` is provided as an alias of `get_dataset` so
either name works; it satisfies the abstract base class contract.

### Regional reanalysis families: CARRA / CERRA / PAN-CARRA

The three high-resolution regional reanalysis families now ship
fully curated:

- **CARRA** (Arctic, ~2.5 km on East/West Greenland) — 5/5 sub-datasets:
    - `reanalysis-carra-pressure-levels` (14)
    - `reanalysis-carra-height-levels` (7)
    - `reanalysis-carra-model-levels` (11)
    - `reanalysis-carra-single-levels` (67)
    - `reanalysis-carra-means` (112 entries spanning `analysis_based`
      and `forecast_based` × `level_type` (single/height/model)
      × `time_aggregation` (daily/monthly), with `request_kind:
      carra_means` to drop the `time` selector at request time).
- **CERRA** (European, ~5.5 km) — 5/5 sub-datasets:
    - `reanalysis-cerra-single-levels` (39)
    - `reanalysis-cerra-height-levels` (10)
    - `reanalysis-cerra-pressure-levels` (11)
    - `reanalysis-cerra-model-levels` (4)
    - `reanalysis-cerra-land` (36)
- **PAN-CARRA** (pan-Arctic) — 2/2 sub-datasets:
    - `reanalysis-pan-carra` (90 entries spanning all four
      `level_type` × `product_type` combos via per-row `extras`
      override).
    - `reanalysis-pan-carra-means` (86 entries).

Variable-key suffixes namespace each family / level_type to avoid
collisions in the flat catalog: `-carra` / `-carra-h` / `-carra-m`,
`-cerra` / `-cerra-h` / `-cerra-m` / `-cerra-p`,
`-pancarra` / `-pancarra-h` / `-pancarra-p` / `-pancarra-m`,
`-carra-means` / `-carra-means-h` / `-carra-means-m`
(plus `-analysisbased` for analysis-based product type).

Default extras pin the most common combo (e.g. `domain: east_domain`,
`leadtime_hour: 3` for CARRA; `data_type: reanalysis`,
`product_type: reanalysis` for CERRA). Override per-request when
needed.

### UERRA Europe reanalysis (blocked upstream)

**UERRA Europe** (4 datasets, ~34 vars: single / pressure / height /
soil-levels; `origin` extra picks between UERRA-HARMONIE and
MESCAN-SURFEX surface analyses; ECMWF deprecated this dataset in
2025-01 but it is still served as-is) is **not curated** because
upstream MARS returns `Ambiguous: ur could be UNITED KINGDOM or
UNITED UERRA HACK` — a server-side catalog ambiguity bug at ECMWF
that no client-side fix can work around. Tracked under M4. Wait for
ECMWF to disambiguate or accept that UERRA stays unaddressable.

### Seasonal forecast family (7 sub-datasets)

All seven seasonal forecast datasets ship fully curated:

- `seasonal-monthly-single-levels` (38), `seasonal-monthly-pressure-levels`
  (5), `seasonal-monthly-ocean` (13) — monthly aggregates.
- `seasonal-postprocessed-single-levels` (38),
  `seasonal-postprocessed-pressure-levels` (5) — anomaly products
  with `*_anomaly` cds_variables and `a` / `da` / `ra` suffix
  nc_variables.
- `seasonal-original-single-levels` (40),
  `seasonal-original-pressure-levels` (5) — raw (non-aggregated)
  forecasts.

Variable-key suffixes: `-seasonal`, `-seasonal-p`, `-seasonal-orig`,
`-seasonal-orig-p`, `-seasonal-pp`, `-seasonal-pp-p`,
`-seasonal-ocean`. Default extras carry sensible
`originating_centre` / `system` / `leadtime_month` defaults per
sub-dataset; override per-request to switch issuing centre.

### CMIP6 datasets (partially deferred)

The two CMIP6 entries on CDS — `projections-cmip6` (51 vars) and
`projections-cmip6-decadal-prototype` (6 vars; adds `base_year`
on top of the standard CMIP6 extras and offers shorter ensemble
runs) — sit behind a
ROOCS-flavoured retrieval pipeline that is **infrastructurally
unstable** today. With every licence accepted, single-variable
requests for `near_surface_air_temperature` succeeded once, but
the same request for any other variable, or any 2+ variable
request, returns `RoocsRuntimeError` server-side without an actionable
diagnostic. Repeated attempts across multiple models
(`access_cm2`, `mpi_esm1_2_lr`, `ec_earth3`) and years showed the
same pattern.

**These datasets are not curated by this package today.** A future
addition should:

1. Probe one variable at a time per model — the ROOCS pipeline
   appears to fail on batch requests but tolerates singletons.
2. Possibly fall back to direct ESGF retrieval if CDS continues to
   be unstable; the ROOCS layer is the part that wraps ESGF, and
   ESGF itself can be queried via dedicated clients
   (`pyesgf`, `intake-esm`).

For now, `projections-cmip6*` datasets remain in
`available_datasets:` for discovery only. CMIP6 climate
projection vars are commonly delivered via CMIP-standard CMOR
short names (`tas`, `pr`, `ta`, `ua`, `va`, `hus`, `hur`, `psl`,
…), the same pattern the CMIP5 / CORDEX rows in this catalog use.

### Climate Atlas datasets (deferred)

The two Climate Atlas products on CDS — `projections-climate-atlas`
(22 vars) and `multi-origin-c3s-atlas` (37 vars; adds
`bias_adjustment` on top of Atlas's
`{domain, experiment, origin, period}` extras) — return their data
as Zarr-flavoured ZIP stores rather than the NetCDF-in-zip that
the rest of the catalog uses. Standard `zipfile` / GDAL readers
reject the file ("end-of-central-directory signature not found").
The atlas family is interactive-viewer-oriented rather than
programmatic-pipeline-oriented; ECMWF surfaces it through the
[Climate Atlas web UI](https://atlas.climate.copernicus.eu/atlas)
rather than the cdsapi-friendly NetCDF path.

**These datasets are not curated by this package today.** Adding
them would require:

1. A new `request_kind="atlas"` branch that drops the ERA5
   template defaults (year/month/day/time/area/product_type) since
   the Atlas requests do not accept any of them.
2. A Zarr-aware reader that knows how to peel the ECMWF
   `RoocsZarrFile` envelope.

If a downstream user needs Atlas access programmatically, file an
issue. Until then both stay in `available_datasets:` for
discovery.

### ERA5 timeseries datasets (deferred)

CDS publishes two single-cell timeseries endpoints that share the
ERA5 / ERA5-Land variable lists but use a fundamentally different
request shape:

* `reanalysis-era5-land-timeseries` — 19 variables.
* `reanalysis-era5-single-levels-timeseries` — 20 variables.

Their constraints surface only `date` (an ISO range) and `variable`
— there is no `year` / `month` / `day` / `time` selector. The
return format is Zarr / CSV rather than NetCDF, so the existing
`post_download` pipeline (which reads NetCDF via pyramids) does not
apply.

**These datasets are not curated by this package today.** Adding
them would require:

1. A new `request_kind="timeseries"` branch in `ECMWF.api()` that
   builds `{date: f"{start}/{end}"}` from `self.time.start_date` /
   `self.time.end_date` instead of the year/month/day arrays.
2. A new reader for the Zarr/CSV output (parallel to the NetCDF
   path in `post_download`).

If a downstream user needs single-cell timeseries access, file an
issue and the deferral can be lifted; until then both datasets
remain in `available_datasets:` for discovery only.

### Datasets without a `variable` field (22)

Twenty-two CDS datasets do not expose a `variable` selector at all
— their request shape identifies a **product** instead, and the
"variable" concept is implicit (often baked into the dataset name
or split across one of the extras fields). The current `Variable`
catalog model is variable-shaped, so these datasets do not fit it
without a per-dataset adapter.

The table below names each one, the field that plays the
"identifies the data column" role in our model, and a recommended
catalog row shape if the package adds support later. **No catalog
expansion has shipped for any of these — this is a research
inventory.**

| Dataset | Implicit-variable field(s) | Suggested row shape |
|---|---|---|
| `insitu-observations-gnss` | `network_type` (e.g. `epn_repro2`) | One row per network; key on the network short code; `extras: {network_type: ...}`. |
| `insitu-observations-gruan-reference-network` | (none — single product) | One row keyed `gruan-reference-network`; no extras. |
| `satellite-fire-radiative-power` | `satellite` × `time_aggregation` | One row per satellite + aggregation; `extras: {satellite, time_aggregation, observation_time, version}`. |
| `satellite-greenland-ice-sheet-velocity` | `period` (multi-year campaigns) | One row per period; `extras: {domain, period, version}`. |
| `satellite-humidity-profiles` | (none — single product) | One row; no extras. |
| `satellite-ice-sheet-elevation-change` | `domain` (`antarctic` / `greenland`) | One row per domain; `extras: {domain, climate_data_record_type, version}`. |
| `satellite-ist-sst-global` | `temporal_resolution` (`daily` / `monthly`) | One row per resolution; `extras: {temporal_resolution, climate_data_record_type, version}`. |
| `satellite-ist-sst-polar` | `region` × `temporal_aggregation` | One row per region+agg; `extras: {region, temporal_aggregation, version}`. |
| `satellite-lake-water-level` | `lake` (named lake basin) | One row per lake; key by basin name; `extras: {lake, region}`. |
| `satellite-lake-water-temperature` | (none — single product) | One row; `extras: {version}`. |
| `satellite-land-cover` | (none — single product) | One row; `extras: {version}`. |
| `satellite-precipitation` | `time_aggregation` (`daily` / `monthly` / `3_hourly`) | One row per aggregation; `extras: {time_aggregation, version}`. |
| `satellite-precipitation-microwave` | `time_aggregation` | Same as above. |
| `satellite-precipitation-microwave-infrared` | `time_aggregation` | Same. |
| `satellite-sea-ice-concentration` | `cdr_type` × `sensor` × `region` | One row per cdr+sensor+region; `extras: {cdr_type, region, sensor, temporal_aggregation, version}`. |
| `satellite-sea-ice-drift` | `region` (`northern` / `southern`) | One row per region; `extras: {region, version}`. |
| `satellite-sea-surface-temperature` | `processinglevel` × `sensor_on_satellite` | One row per (level, sensor); `extras: {processinglevel, sensor_on_satellite, temporal_resolution, version}`. |
| `satellite-sea-surface-temperature-ensemble-product` | (none — single product) | One row; no extras. |
| `satellite-total-column-water-vapour-land-ocean` | `product` (`water_vapour_in_total_column`) | One row per product; `extras: {product, horizontal_aggregation, temporal_aggregation}`. |
| `satellite-total-column-water-vapour-ocean` | (single product) | One row; `extras: {climate_data_record_type, origin, temporal_aggregation}`. |
| `sis-european-wind-storm-indicators` | `product` (storm metric) | One row per product; `extras: {product, spatial_aggregation, time_aggregation}`. |
| `sis-european-wind-storm-reanalysis` | `product` × `tracking_algorithm` | One row per (product, algorithm); `extras: {product, tracking_algorithm, event_aggregation, spatial_extent, windstorm_footprint_resolution}`. |

The recurring pattern: the slot that *would have been* a CDS
`variable` list is always one of the extras (`product`,
`network_type`, `cdr_type`, `lake`, …). When (if) the package adds
support, the cleanest path is to **synthesise a synthetic
"variable" key per row** — slugified from the dominant
distinguishing extra — and stash the rest under `Variable.extras`.
This avoids inventing a new model just to absorb 22 datasets, but
does mean the `cds_variable` field on those rows is *empty* (the
request omits the `variable` key entirely). That requires a small
api() branch parallel to `M15`'s strategy work. Treat as Phase 4.

### Non-addressable CDS datasets

A handful of entries in `available_datasets:` are **listed for
discovery but cannot be requested through cdsapi**. They surface in
the index because users who browse the YAML may want to know they
exist, but the package will not be able to download them — either
because CDS exposes them through a different protocol, or because
the dataset's `/constraints.json` endpoint returns empty / 404.

| Dataset | Reason | Workaround |
|---|---|---|
| `reanalysis-era5-complete` | MARS-only — accepts the full ECMWF MARS request language, not the cdsapi form. No public constraints file. | Use the MARS-ECMWFAPI client directly; this package does not wrap it. |
| `reanalysis-uerra-europe-complete` | Same — MARS-only sibling of the UERRA Europe family. The `single-levels` / `pressure-levels` / `height-levels` / `soil-levels` siblings are addressable (covered by `M4`). | Use the addressable UERRA siblings, or fall back to MARS. |
| `derived-reanalysis-energy-moisture-budget` | `/constraints.json` returns an empty list — the dataset is not currently published for retrieval. | Watch the dataset page; re-add when constraints surface. |
| `derived-utci-historical-timeseries` | Empty constraints — see also `M7` (the form-based `derived-utci-historical` is addressable). | Use the form-based variant. |
| `insitu-gridded-observations-alpine-precipitation` | Empty constraints — likely admin-restricted. | None today; track upstream. |
| `satellite-ice-sheet-mass-balance` | Empty constraints — provider-specific download protocol. | Fetch directly from the provider site linked on CDS. |
| `satellite-terrestrial-water-storage` | Empty constraints — same. | Same. |
| `sis-health-vector` | Empty constraints — likely admin-restricted. | None. |
| `sis-temperature-statistics` | Empty constraints. | None. |
| `provider-c3s-data-rescue-without` | No `/constraints.json` endpoint — placeholder collection. | Skip; ignore if the dataset re-emerges with proper constraints. |

These ten remain in the `available_datasets:` index as a forward
pointer for users; promoting any of them to a curated row under
`datasets:` is a no-op until CDS publishes constraints, so don't
attempt it speculatively.

### Building a known-valid request

For any addressable CDS dataset, `Catalog.minimal_valid_request(dataset_name)`
returns a request dict drawn from the dataset's published
`constraints.json`. Useful for verifying account setup, exploring a
new dataset's extras schema, and seeding tests:

```python
>>> from earthly.ecmwf import Catalog
>>> request = Catalog().minimal_valid_request("reanalysis-cerra-land")
>>> sorted(request)
['data_format', 'day', 'leadtime_hour', 'level_type', 'month',
 'product_type', 'time', 'variable', 'year']
```

The returned dict is the inverse of what `validate_request()`
checks — submit it via `cdsapi.Client.retrieve()` directly and CDS
should accept it without `400`.

### Per-row opt-out via `extras: None`

Setting any key in a variable's `extras` to `None` drops it from
the request at api() build time. The most common use is suppressing
the default bbox for datasets that reject `area` (CMIP6 atlases,
projections on rotated grids, ORAS5):

```yaml
my-3d-variable:
  nc_variable: foo
  units: m
  extras:
    area:           # null → skip the bbox subset entirely
```

This works alongside `request_kind` — a `request_kind` strip is
applied first, then per-row `None` values, then the constraints
validator runs on the final request.

### Pre-flight constraints validation

Every CDS request passes through
`earthly.ecmwf.constraints.validate_request()` before
:meth:`cdsapi.Client.retrieve` is called. The validator runs five
phases (cheap → expensive); the first failure is reported, so the
user gets the most specific error possible:

1. **Date sanity** — `month` must be 01-12, `day` must be 01-31,
   `year` must be in 1850-2100, and the (year, month, day) triple
   must be a real calendar date (no Feb 30, no Apr 31).
2. **Area bbox sanity** — `area` must be a 4-element list
   `[north, west, south, east]` with `south <= north`, latitudes
   in [-90, 90], longitudes in [-360, 360].
3. **Variable spell-check** — every requested `variable` must
   appear in the catalogued set; near-misses surface
   `did you mean X?` suggestions via `difflib`.
4. **Required-field check** — keys present in *every* constraint
   entry are reported as missing if absent from the request
   (catches "you forgot to set `experiment`" for CMIP6).
5. **Full combinatorial walk** — fetches the dataset's
   `constraints.json` (cached per-process) and rejects any
   request whose extras / variable / year combination falls
   outside the published validity matrix.

This catches typos and mis-guesses (e.g. CERRA-land's
`level_type: surface` requires `product_type: forecast`, not
`analysis`) **before** the request takes a CDS queue slot —
saving 1–30 minutes per failure.

Bypass the check by passing `skip_constraints=True` to
`ECMWF(...)` (or `RequestValidator(..., skip=True)` if calling the
validator directly) when a dataset's constraints endpoint is
missing or outdated. The unit-test suite sets `skip_constraints`
on its synthetic backend instances so test requests aren't
penalised by network-only validation logic.

### Refreshing `available_datasets`

CDS adds and retires datasets a few times a year; the
`available_datasets:` block at the top of `cds_data_catalog.yaml` is
the package's pinned snapshot. Refresh it before each release with:

```bash
pixi run -e dev python tools/refresh_available_datasets.py
```

The script pulls the live STAC catalogue from
`https://cds.climate.copernicus.eu/api/catalogue/v1/collections`,
groups the entries by family (Reanalyses / Derived / Projections /
Seasonal / In-situ / Satellite / SIS), and rewrites the
`available_datasets:` block in place. It does **not** touch the
curated `datasets:` map — those stay hand-authored. Inspect the diff
before committing; new entries that fit the existing schema can be
promoted to `datasets:` in a follow-up PR.

### Unit conversions

The package returns values in their **native ERA5 units** — the same
strings CDS writes to the NetCDF. Most workflows want a different
output unit (Celsius instead of Kelvin, mm instead of metres, etc.).
The conversion is `output = factors_mul * raw + factors_add`. The
factors below cover the common ERA5 variables a typical user would
ever apply a non-identity transform to:

| Variable                        | Raw ERA5 unit          | `factors_add` | `factors_mul` | Converted unit |
|---------------------------------|------------------------|--------------:|--------------:|----------------|
| `2m-temperature`                | K                      | −273.15       | 1             | °C             |
| `2m-dewpoint-temperature`       | K                      | −273.15       | 1             | °C             |
| `temperature` (pressure-level)  | K                      | −273.15       | 1             | °C             |
| `surface-pressure`              | Pa                     | 0             | 0.001         | kPa            |
| `total-precipitation`           | m                      | 0             | 1000          | mm             |
| `evaporation`                   | m of water equivalent  | 0             | 1000          | mm             |
| `runoff`                        | m                      | 0             | 1000          | mm             |
| `surface-runoff`                | m                      | 0             | 1000          | mm             |
| `sub-surface-runoff`            | m                      | 0             | 1000          | mm             |

Variables not in this table are returned as-is (wind speeds in
`m s**-1`, fluxes in `J m**-2`, fractions in `(0 - 1)`, etc.).

```python
import numpy as np

raw_kelvin = ...                                      # from post_download()
celsius = 1 * raw_kelvin + (-273.15)                  # 2m-temperature

raw_metres = ...                                      # from post_download()
millimetres = 1000 * raw_metres + 0                   # total-precipitation
```

## Amazon S3

For Amazon S3, the data depends on the AWS bucket, so the catalog object initializes a connection to the bucket and checks the data inside:

```python
from earthly.s3 import Catalog

s3_catalog = Catalog()
print(s3_catalog.catalog)
```

```python
{
    'precipitation': {
        'descriptions': 'rainfall [mm/temporal_resolution]',
        'units': 'mm/temporal_resolution',
        'temporal resolution': ['daily', 'monthly'],
        'file name': 'rainfall',
        'var_name': 'R',
        'bucket_name': 'precipitation_amount_1hour_Accumulation'
    }
}
```

The attributes for a specific climate variable (like precipitation) differ from one data source to another.

To get the attributes for a specific variable:

```python
s3_catalog.get_variable("precipitation")
```

To get the time span of the precipitation data:

```python
years = s3_catalog.get_available_years()
print(years)
```

```python
[
    '1979', '1980', '1981', ..., '2021', '2022', 'QA', 'zarr'
]
```

!!! note
    The catalog is still in the development phase. Ideally the catalog will be a JSON file containing all the available data provided by each data source.
