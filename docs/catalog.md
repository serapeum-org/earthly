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

## ECMWF

```python
from earth2observe.ecmwf import Catalog

catalog = Catalog()
```

```python
{
    'version': 1,
    'datasets': [
        'cams_gfas', 'cams_nrealtime', 'cera20c', 'cera_sat', 'era15',
        'era20c', 'era20cm', 'era20cmv0', 'era40', 'geff_reanalysis',
        'icoads', 'interim', 'interim_land', 'ispd', 'macc',
        'macc_nrealtime', 's2s', 'tigge', 'uerra', 'yopp', 'yotc'
    ],
    'variables': [
        'T', '2T', 'SRO', 'SSRO', 'WIND', '10SI', 'SP', 'Q', 'SSR',
        'R', 'E', 'SUND', 'RO', 'TP', '10U', '10V', '2D', 'SR', 'AL', 'HCC'
    ],
    'T': {
        'descriptions': 'Temperature [K]',
        'units': 'C',
        'types': 'state',
        'temporal resolution': ['six hours', 'daily', 'monthly'],
        'file name': 'Tair2m',
        'download type': 3,
        'number_para': 130,
        'var_name': 't',
    },
    # ... more variables ...
}
```

To get the attributes for a specific variable (e.g., Evaporation `E`):

```python
var = "E"
catalog.get_variable(var)
```

```python
{
    'descriptions': 'Evaporation [m of water]',
    'units': 'mm',
    'types': 'flux',
    'temporal resolution': ['six hours', 'daily', 'monthly'],
    'file name': 'Evaporation',
    'download type': 2,
    'number_para': 182,
    'var_name': 'e'
}
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
