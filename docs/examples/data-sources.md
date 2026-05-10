# Data Sources

## Design Concept

earthlens is designed following the Template/Factory design pattern to create an abstract class as a template for different data sources.

The main objective is to provide a unified API for all remote sensing data sources, where you only have to worry about the domain of your data (date range and spatial extent) and the package does everything in the backend.

`earthlens` provides a unified API for the following data sources:

- ECMWF
- CHIRPS
- Amazon S3
- Google Earth Engine (under development)

!!! note
    Some data sources (Google Earth Engine, ECMWF) require authentication keys. See the [Authentication](authentication.md) page for setup instructions.

The API takes a few parameters to determine the domain of your data:

- **Date range**: `start`, `end`, and `temporal_resolution`
- **Spatial extent**: `lat_lim` (latitude limits) and `lon_lim` (longitude limits)
- If `lat_lim` and `lon_lim` are not provided, the `EarthLens` class defaults to longitude `[-180, 180]` and latitude `[-90, 90]`.

```python
from earthlens.earthlens import EarthLens

start = "2009-01-01"
end = "2009-01-10"
temporal_resolution = "daily"
latlim = [4.19, 4.64]
lonlim = [-75.65, -74.73]
```

Each data source has different climate variables/datasets. To discover available variables, use the `Catalog` class for each data source (see [Data Catalog](catalog.md)).

!!! info
    The downloaded data format differs based on the data source. CHIRPS and ECMWF have a `post_download` function that converts the NetCDF format into GeoTIFF using the [pyramids](https://github.com/serapeum-org/pyramids) GIS package.

!!! note
    In future versions, `lat_lim` and `lon_lim` will be deprecated and replaced by a GeoDataFrame containing a polygon geometry.

## ECMWF (Copernicus Climate Data Store)

The ECMWF backend talks to the Copernicus Climate Data Store via
`cdsapi`. ERA-Interim was retired in 2019 and the public-datasets
endpoint that hosted it was decommissioned in 2023; **ERA5 on CDS is
the production successor** and what every ECMWF retrieve in this
package now hits. Set up your `~/.cdsapirc` first
(see [Authentication](authentication.md)) and accept the licence for
the relevant ERA5 dataset on the CDS website.

```python
source = "ecmwf"
path = "examples/data/era5"
# Variables are addressed by (CDS dataset short name, variable code).
variables = {
    "reanalysis-era5-single-levels": ["2m-temperature"],
}

earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
earthlens.download()
```

!!! note "Expect to wait"
    `client.retrieve()` blocks until the request reaches the front of
    the CDS queue and the file is generated — typically minutes,
    occasionally longer for large requests. Pick a small bbox and date
    range to keep wait times bearable. In CI the cdsapi client is
    mocked; the live end-to-end suite is selected with `pytest -m e2e`.

## CHIRPS

```python
source = "chirps"
path = "examples/data/chirps"
variables = ["precipitation"]

earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
earthlens.download()
```

### Parallel Download

```python
path = "examples/data/chirps-cores"

earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
earthlens.download(cores=4)
```

## Amazon S3

```python
path = "examples/data/s3-backend"
source = "amazon-s3"
variables = ["precipitation"]

earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    temporal_resolution=temporal_resolution,
    path=path,
)
earthlens.download()
```
