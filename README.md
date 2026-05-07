[![Documentation Status](https://readthedocs.org/projects/earthly/badge/?version=latest)](https://earthly.readthedocs.io/en/latest/?badge=latest)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/earthly)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)


[![codecov](https://codecov.io/gh/Serapieum-of-alex/earthly/branch/main/graph/badge.svg?token=2nBcI5ijvB)](https://codecov.io/gh/Serapieum-of-alex/earthly)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/4c95cf4c0dd044e4b451e08355fe6ee7)](https://www.codacy.com/gh/Serapieum-of-alex/earthly/dashboard?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=Serapieum-of-alex/earthly&amp;utm_campaign=Badge_Grade)
![GitHub last commit](https://img.shields.io/github/last-commit/Serapieum-of-alex/earthly)
![GitHub forks](https://img.shields.io/github/forks/Serapieum-of-alex/earthly?style=social)
![GitHub Repo stars](https://img.shields.io/github/stars/Serapieum-of-alex/earthly?style=social)


Current release info
====================

| Name                                                                                                               | Downloads                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Version                                                                                                                                                                                                                                                                                                                                           | Platforms                                                                                                               |
|--------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------|
| [![Conda Recipe](https://img.shields.io/badge/recipe-earthly-green.svg)](https://anaconda.org/conda-forge/earthly) | [![Conda Downloads](https://img.shields.io/conda/dn/conda-forge/earthly.svg)](https://anaconda.org/conda-forge/earthly) [![Downloads](https://pepy.tech/badge/earthly)](https://pepy.tech/project/earthly) [![Downloads](https://pepy.tech/badge/earthly/month)](https://pepy.tech/project/earthly) [![Downloads](https://pepy.tech/badge/earthly/week)](https://pepy.tech/project/earthly) ![PyPI - Downloads](https://img.shields.io/pypi/dd/earthly?color=blue&style=flat-square) ![GitHub all releases](https://img.shields.io/github/downloads/Serapieum-of-alex/earthly/total) | [![Conda Version](https://img.shields.io/conda/vn/conda-forge/earthly.svg)](https://anaconda.org/conda-forge/earthly) [![PyPI version](https://badge.fury.io/py/earthly.svg)](https://badge.fury.io/py/earthly) [![Anaconda-Server Badge](https://anaconda.org/conda-forge/earthly/badges/version.svg)](https://anaconda.org/conda-forge/earthly) | [![Conda Platforms](https://img.shields.io/conda/pn/conda-forge/earthly.svg)](https://anaconda.org/conda-forge/earthly) |

earthly — a unified Python client for satellite & climate data
=====================================================================

**earthly** gives you one consistent Python API for downloading satellite and
climate data from four very different providers — UCSB CHIRPS, ERA5 on AWS,
the ECMWF Climate Data Store, and Google Earth Engine — and turning the
results into analysis-ready GeoTIFFs.

It is part of the [Serapieum-of-alex](https://github.com/Serapieum-of-alex)
open-source ecosystem and is built on top of
[`pyramids-gis`](https://github.com/Serapieum-of-alex/pyramids) for raster I/O.


Why earthly?
------------

Each provider speaks its own dialect: CHIRPS is anonymous FTP with date-coded
filenames, ERA5-on-S3 is unsigned object storage with a per-month layout, the
ECMWF CDS expects a JSON request body validated against a constraints graph,
and Google Earth Engine is a server-side image-collection model. **earthly**
flattens that into one call:

```python
from earthly import Earthly

earthly = Earthly(
    data_source="ecmwf",          # or "chirps", "amazon-s3", "gee"
    temporal_resolution="monthly",
    start="2022-01-01",
    end="2022-12-01",
    variables={
        "reanalysis-era5-single-levels-monthly-means": [
            "2m-temperature",
            "total-precipitation",
        ],
    },
    lat_lim=[37.0, 38.0],
    lon_lim=[23.0, 24.0],
    path="data/era5",
)
earthly.download()
```

You get back per-date, per-variable GeoTIFFs in `data/era5/` — clipped to your
bbox, ready to feed into a hydrology model, a PV-yield notebook, a heat-wave
study, or anything else downstream.


Features
--------

- **Four backends, one facade.** `Earthly(data_source=...)` routes to CHIRPS,
  ERA5-on-S3, ECMWF/CDS, or Google Earth Engine without changing the rest of
  your code.
- **YAML variable catalogs** for ECMWF and GEE — every variable carries
  metadata: NetCDF name, units, accumulation semantics (`is_flux`), allowed
  pressure levels, monthly counterparts. Browseable with `Catalog().get_variable(...)`.
- **Pre-flight request validation** against the live CDS `constraints.json`
  graph. Bad date / area / variable combinations are rejected before bytes
  go over the wire, with actionable error messages.
- **Temporal aggregation built in.** Pass an `AggregationConfig` to
  `download()` and earthly emits aggregated GeoTIFFs alongside the raw
  NetCDFs. `op="auto"` reduces **state** variables (temperature, SST, soil
  moisture) by mean and **flux** variables (precipitation, radiation,
  evaporation) by sum — the physically correct choice driven by catalog
  metadata.
- **Pressure-level support.** ERA5 pressure-level fields (4-D NetCDFs) can be
  sliced to a specific level on download.
- **Bbox cropping & NetCDF→GeoTIFF conversion** are handled by `pyramids-gis`
  under the hood.
- **Modular install extras** — only install the SDK for the backend you need
  (`pip install earthly[ecmwf]`, `[s3]`, `[gee]`).
- **Strictly typed.** Pydantic v2 models for catalog rows and request specs;
  modern PEP 585/604 type hints; Python 3.11 / 3.12 tested in CI.


Supported data sources
----------------------

| Source       | Provider                          | Protocol            | Resolutions     | Auth             | Install extra        |
|--------------|-----------------------------------|---------------------|-----------------|------------------|----------------------|
| CHIRPS       | UCSB Climate Hazards Center       | FTP (anonymous)     | daily, monthly  | none             | core                 |
| Amazon S3    | ERA5 on AWS `era5-pds`            | S3 (unsigned)       | monthly         | none             | `[s3]`               |
| ECMWF / CDS  | Copernicus Climate Data Store     | `cdsapi` (HTTPS)    | daily, monthly  | `~/.cdsapirc`    | `[ecmwf]`            |
| GEE          | Google Earth Engine               | `earthengine-api`   | varies          | service account  | `[gee]`              |


Installation
------------

`earthly` is published on conda-forge and PyPI.

```bash
# conda (recommended — pulls GDAL automatically)
conda install -c conda-forge earthly

# pip — latest release
pip install earthly==0.3.0

# pip — bleeding edge
pip install git+https://github.com/Serapieum-of-alex/earthly
```

To list all available versions on your platform:

```bash
conda search earthly --channel conda-forge
```

GDAL is required and is **not** on PyPI. If you install via pip, get GDAL from
the [large-image-wheels](https://girder.github.io/large_image_wheels) index:

```bash
pip install --find-links=https://girder.github.io/large_image_wheels --no-cache GDAL==3.10.0
```

Backend SDKs are optional and pulled in by extras:

```bash
pip install earthly[ecmwf]   # cdsapi
pip install earthly[s3]      # boto3 + unicloud
pip install earthly[gee]     # earthengine-api
pip install earthly[dev,test]  # full dev environment
```


Quick examples per backend
--------------------------

**CHIRPS daily rainfall** — anonymous FTP, no credentials.

```python
from earthly import Earthly

Earthly(
    data_source="chirps",
    temporal_resolution="daily",
    start="2009-01-01",
    end="2009-01-10",
    variables=["precipitation"],
    lat_lim=[4.19, 4.64],
    lon_lim=[-75.65, -74.73],
    path="data/chirps",
).download(cores=4)  # parallel FTP fetch
```

**ERA5 monthly via AWS public S3** — unsigned, fast, no API key.

```python
Earthly(
    data_source="amazon-s3",
    temporal_resolution="monthly",
    start="2020-01-01",
    end="2020-12-01",
    variables=["air_temperature_at_2_metres", "precipitation_amount_1hour_Accumulation"],
    lat_lim=[30.0, 35.0],
    lon_lim=[28.0, 35.0],
    path="data/era5-s3",
).download()
```

**ECMWF CDS with on-the-fly aggregation.** Downloads daily ERA5, then writes
monthly GeoTIFFs aggregated with the right reduction per variable (mean for
temperature, sum for precipitation):

```python
from earthly import Earthly, AggregationConfig

Earthly(
    data_source="ecmwf",
    temporal_resolution="daily",
    start="2022-06-01",
    end="2022-08-31",
    variables={
        "reanalysis-era5-single-levels": [
            "2m-temperature",
            "total-precipitation",
        ],
    },
    lat_lim=[37.0, 38.0],
    lon_lim=[23.0, 24.0],
    path="data/athens-summer",
).download(aggregate=AggregationConfig(freq="1MS", op="auto"))
```

**Google Earth Engine** — server-side collection, downloaded as GeoTIFFs.

```python
Earthly(
    data_source="gee",
    temporal_resolution="daily",
    start="2023-01-01",
    end="2023-01-10",
    variables=["MODIS/061/MOD13Q1/NDVI"],
    lat_lim=[30.0, 31.0],
    lon_lim=[31.0, 32.0],
    path="data/gee-ndvi",
).download()
```


Aggregation: state vs flux
--------------------------

ERA5 mixes two physically distinct kinds of variables:

- **State** variables are instantaneous samples — temperature, SST, soil
  moisture, snow depth. Aggregating in time means **averaging**.
- **Flux** variables are accumulated over each timestep — precipitation,
  radiation, evaporation, surface heat fluxes. Aggregating in time means
  **summing**.

Mixing those up produces silently wrong results (a "monthly mean" of
precipitation under-reports rainfall by ~30×). earthly's catalog tags every
variable with `is_flux`, and `op="auto"` reads that flag to pick the right
reduction:

```python
from earthly.ecmwf import Catalog
spec = Catalog().get_variable(
    "reanalysis-era5-single-levels", "total-precipitation"
)
print(spec.is_flux)  # True  -> auto-aggregate by SUM
```

You can override with `op="mean" | "sum" | "max" | "min"` when you know
better than the catalog.


Authentication
--------------

| Source       | What you need                                                                  |
|--------------|--------------------------------------------------------------------------------|
| CHIRPS       | Nothing — anonymous FTP.                                                       |
| Amazon S3    | Nothing — unsigned, public bucket.                                             |
| ECMWF / CDS  | A free CDS account and a `~/.cdsapirc` with your API key.                     |
| GEE          | A Google Earth Engine project and a service-account JSON key.                  |


Documentation
-------------

Full docs, API reference, architecture diagrams, and a gallery of domain-specific
example notebooks (hydrology, oceanography, agriculture, solar/wind energy,
heat waves, drought, snow & cryosphere, climate-change anomalies) live at:

> **<https://earthly.readthedocs.io/>**


Contributing
------------

Issues, PRs, and discussions are welcome on
[GitHub](https://github.com/Serapieum-of-alex/earthly). The repo uses
pre-commit (black, isort, flake8, bandit, pydocstyle) — install hooks once
with `pre-commit install`.


License
-------

GPL v3. See [LICENSE](LICENSE).
