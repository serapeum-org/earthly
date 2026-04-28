# Data Catalog

Each data source provides some datasets/climate variables, and the `Catalog` class is the way to discover what data is available at a certain date at a specific location.

The data catalog is a dictionary with the available datasets as keys and the attributes that describe each dataset stored in a nested dictionary.

## CHIRPS

```python
from earth2observe.chirps import Catalog

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
from earth2observe.ecmwf import Catalog

catalog = Catalog()
list(catalog.catalog)[:5]
```

```python
["2m-temperature", "2m-dewpoint-temperature", "surface-pressure", "total-precipitation", "evaporation"]
```

To get the attributes for a specific variable (e.g., 2-metre
temperature):

```python
catalog.get_dataset("2m-temperature")
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

The catalog ships ~338 ERA5 entries across three datasets:

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
  `Catalog().get_dataset(code)` resolves to ERA5-Land — the higher
  resolution land-surface field. Use the structural map
  (`Catalog().datasets["reanalysis-era5-single-levels"].variables[code]`)
  to address the single-levels variant explicitly.
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
`src/earth2observe/ecmwf/cds_data_catalog.yaml` following the schema
in the file's header comment.

`get_variable(var_name)` is provided as an alias of `get_dataset` so
either name works; it satisfies the abstract base class contract.

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
from earth2observe.s3 import Catalog

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
