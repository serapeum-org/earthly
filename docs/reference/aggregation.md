# `earthly.aggregate` — temporal aggregation reference

## Overview

`earthly.aggregate` turns a CDS-shaped NetCDF (one with a time
dimension) into per-window GeoTIFFs (daily mean, monthly sum, weekly
mean, seasonal climatology, ...). It runs against `pyramids` +
`numpy` + `pandas`; **`xarray` is not a runtime dependency**.

The feature is reachable two ways:

1. **Standalone.** `from earthly import aggregate_netcdf,
   AggregationConfig` and call against any pyramids-readable NetCDF.
   No `ECMWF` instance needed.
2. **Bundled with download.** `ECMWF.download(aggregate=...)` —
   when the parameter is set, every per-variable NetCDF that the
   backend retrieves is fed through the aggregator immediately
   after `_api()` returns.

The same module is also exposed via the user-facing facade:
`Earthly(...).download(aggregate=...)`.

## Public API

### `AggregationConfig`

A frozen pydantic model carrying the windowing frequency, reduction
operator, and output location. Frozen + `extra="forbid"` so a typo
in a field name (e.g. `freqency=`) fails loud at construction time
rather than silently using the default.

| Field | Type | Default | Purpose |
|---|---|---|---|
| `freq` | `str` (required) | — | Pandas offset alias defining the window. |
| `op` | `Literal["mean","sum","min","max","std","auto"]` | `"auto"` | Reduction within each window. `"auto"` reads `Variable.is_flux`. |
| `out_dir` | `Path \| None` | `None` | Where per-window GeoTIFFs are written. `None` skips the write step. |
| `cell_size` | `float` | `0.125` | Pixel size in degrees (informational; the geotransform is read off the NetCDF). |
| `level` | `int \| float \| None` | `None` | Pin a pressure level for 4-D inputs. |
| `skipna` | `bool` | `True` | NaN-aware reduction (`np.nanmean` etc.). |
| `min_count` | `int \| None` | `None` | Minimum non-NaN samples for a window to produce a non-NaN value. |

### `aggregate_netcdf(nc_path, var_info, config) -> list[tuple[...]]`

Slices a CDS-shaped NetCDF into per-window aggregated outputs.
Returns a list of `(window_label, array, geotiff_path)` tuples — one
per non-empty window. `geotiff_path` is `None` when
`config.out_dir` was `None`.

Arguments:

- `nc_path` — path to the NetCDF on disk.
- `var_info` — :class:`earthly.ecmwf.Variable` row (resolves
  `op="auto"` via `is_flux`, drives the output filename via
  `cds_variable`, picks the variable from the NetCDF via
  `nc_variable`).
- `config` — :class:`AggregationConfig` describing the window,
  reduction, and output location.

### `ECMWF.download(aggregate=...)`

Adds an `aggregate: AggregationConfig | None` keyword-only argument.
When supplied, every retrieved NetCDF is fed through
`aggregate_netcdf` immediately after `_api()` returns. When
`aggregate.out_dir` is `None`, it is defaulted to
`<self.root_dir>/aggregated/`. Aggregation failures surface
alongside retrieve failures in the per-variable failure summary, so
a single bad variable does not abort the rest of the loop.

## Supported reduction operators

| `op` | Reducer (skipna=True) | Reducer (skipna=False) |
|---|---|---|
| `"mean"` | `np.nanmean` | `np.mean` |
| `"sum"` | `np.nansum` | `np.sum` |
| `"min"` | `np.nanmin` | `np.min` |
| `"max"` | `np.nanmax` | `np.max` |
| `"std"` | `np.nanstd` | `np.std` |
| `"auto"` | resolves to `"mean"` (state) or `"sum"` (flux) | same |

## Supported `freq` values

Anything accepted by `pandas.Grouper(freq=...)` works. Common
choices:

| Alias | Window |
|---|---|
| `"1D"` | one calendar day |
| `"7D"` | seven days (rolling weekly) |
| `"1MS"` | one calendar month, anchored at month-start |
| `"QS-DEC"` | climatological seasons (DJF, MAM, JJA, SON) |
| `"AS"` | calendar year |

See the [pandas offset aliases reference](https://pandas.pydata.org/docs/user_guide/timeseries.html#offset-aliases)
for the full grammar (e.g. `"3H"`, `"30min"`, `"6MS"`, `"AS-OCT"`,
...).

## `op="auto"` semantics — flux vs state

`Variable.is_flux` is `True` for CDS flux variables (precipitation,
evaporation, runoff, radiation accumulations) and `False` for state
variables (temperature, pressure, humidity, ...).

- `op="auto"` + `is_flux=True` → `"sum"` over the window.
- `op="auto"` + `is_flux=False` → `"mean"` over the window.

This **replaces** the legacy `mean × days_later` flux scaling that
the pre-rewrite example script used. The two are equivalent only
when every slot inside a window has a sample; for partial windows,
true `sum` is correct and `mean × N` overcounts.

## Pressure-level support (`level=`)

Pyramids exposes `NetCDF.dimension_names` and `NetCDF.sel(...)`,
which `aggregate_netcdf` uses to handle 4-D
`(time, level, lat, lon)` NetCDFs:

| NetCDF shape | `level` set | Result |
|---|---|---|
| 3-D `(time, lat, lon)` | not set | aggregates as-is |
| 3-D `(time, lat, lon)` | set | `ValueError` ("no pressure-level dim") |
| 4-D `(time, level, lat, lon)` | not set | `ValueError` ("pass `level=...`") |
| 4-D `(time, level, lat, lon)` | set | `nc.sel(<dim>=level)`, then aggregate |

Aggregation across all levels at once is intentionally not
supported — the user must pick a level explicitly.

## Worked example — download to monthly mean GeoTIFFs

Single-call pipeline that downloads daily ERA5 2-metre temperature
for January 2022 over a 1° box and writes one monthly-mean GeoTIFF:

```python
from earthly import AggregationConfig
from earthly.earthly import Earthly

earthly = Earthly(
    data_source="ecmwf",
    temporal_resolution="daily",
    start="2022-01-01",
    end="2022-01-31",
    variables={"reanalysis-era5-single-levels": ["2m-temperature"]},
    lat_lim=[4.0, 5.0],
    lon_lim=[-75.0, -74.0],
    path="out/era5",
)
earthly.download(
    aggregate=AggregationConfig(freq="1MS", op="mean"),
)
```

The retrieved NetCDF lands at
`out/era5/2m_temperature_reanalysis-era5-single-levels.nc`; the
aggregated GeoTIFF lands at
`out/era5/aggregated/2m_temperature_1MS_20220101.tif` (default
`out_dir = <root_dir>/aggregated/`).

## Worked example — aggregate later, separately

If you already have the NetCDF on disk:

```python
from earthly import AggregationConfig, aggregate_netcdf
from earthly.ecmwf import Catalog

spec = Catalog().get_variable(
    "reanalysis-era5-single-levels", "2m-temperature"
)
results = aggregate_netcdf(
    "out/era5/2m_temperature_reanalysis-era5-single-levels.nc",
    spec,
    AggregationConfig(freq="1MS", op="mean", out_dir="out/era5/monthly"),
)
for window_label, arr, target in results:
    print(window_label, arr.shape, target.name)
```

## In-memory mode (`out_dir=None`)

Skip GeoTIFF writes entirely and inspect the per-window arrays:

```python
from earthly import AggregationConfig, aggregate_netcdf
from earthly.ecmwf import Catalog

spec = Catalog().get_variable(
    "reanalysis-era5-single-levels", "2m-temperature"
)
results = aggregate_netcdf(
    "out/era5/2m_temperature_reanalysis-era5-single-levels.nc",
    spec,
    AggregationConfig(freq="1D", op="mean"),
)
first_label, first_array, first_path = results[0]
assert first_path is None
```

## CLI demo

`examples/post_process_ecmwf_netcdf.py` is a thin CLI wrapper:

```bash
python examples/post_process_ecmwf_netcdf.py \
    out/era5/2m_temperature_reanalysis-era5-single-levels.nc \
    out/era5/daily \
    reanalysis-era5-single-levels \
    2m-temperature \
    --freq 1D --op auto
```

Flags map 1-to-1 to `AggregationConfig` fields. See `--help` for
the full list.

## Output filename convention

Per-window GeoTIFFs are named:

```
<cds_variable>_<freq>_<window-label-as-YYYYMMDD>.tif
```

Examples:

- `2m_temperature_1D_20220101.tif` — daily mean for 2022-01-01.
- `total_precipitation_1MS_20220101.tif` — monthly sum for 2022-01.
- `temperature_QS-DEC_20220301.tif` — MAM seasonal mean.

## Related

- :class:`earthly.aggregate.AggregationConfig` — frozen request
  payload.
- :func:`earthly.aggregate.aggregate_netcdf` — the core function.
- :class:`earthly.ecmwf.Catalog` — resolves `(dataset, code)` pairs
  to the `Variable` rows that drive `op="auto"` and the output
  filename.
- :meth:`earthly.ecmwf.ECMWF.download` — accepts the `aggregate`
  parameter for one-call download-and-aggregate.
