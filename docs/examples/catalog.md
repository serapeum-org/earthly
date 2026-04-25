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
and exposes a per-variable map keyed by user-friendly short codes
(`E`, `T`, `2T`, `TP`, ...). Each entry tells `ECMWF.api()` which CDS
dataset hosts the variable, the official CDS variable name, and the
unit-conversion factors used during post-processing.

```python
from earth2observe.ecmwf import Catalog

catalog = Catalog()
list(catalog.catalog)[:5]
```

```python
['2T', '2D', 'SP', 'TP', 'E']
```

To get the attributes for a specific variable (e.g., 2-metre
temperature `2T`):

```python
catalog.get_dataset("2T")
```

```python
{
    'cds_dataset': 'reanalysis-era5-single-levels',
    'cds_dataset_monthly': 'reanalysis-era5-single-levels-monthly-means',
    'cds_variable': '2m_temperature',
    'units': 'C',
    'file_name': 'Tair',
    'factors_add': -273.15,
    'factors_mul': 1,
}
```

Key reference:

- `cds_dataset` — CDS dataset short name used for daily / sub-daily
  retrieves.
- `cds_dataset_monthly` — optional, used when
  `temporal_resolution="monthly"`. Falls back to `cds_dataset` when
  absent.
- `cds_variable` — the CDS variable name passed to
  `client.retrieve()`.
- `cds_pressure_level` — optional list of pressure levels (e.g.
  `["1000"]`). Present for pressure-level variables (`T`, `Q`, `R`).
- `units`, `file_name` — used to name output files.
- `factors_add`, `factors_mul` — unit-conversion offsets applied in
  `post_download()`.

The catalog ships short codes for ~18 ERA5 variables on
`reanalysis-era5-single-levels` (and its monthly-means counterpart),
plus a handful on `reanalysis-era5-pressure-levels`. Browse the full
list of CDS dataset short names at
<https://cds.climate.copernicus.eu/datasets?q=era5>. To add a new
variable, append an entry to `src/earth2observe/cds_data_catalog.yaml`
following the schema in the file's header comment.

`get_variable(var_name)` is provided as an alias of `get_dataset` so
either name works; it satisfies the abstract base class contract.

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
