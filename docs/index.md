# earth2observe

[![Docs](https://img.shields.io/badge/docs-mkdocs-blue)](https://serapeum-org.github.io/earth2observe/)
[![PyPI version](https://badge.fury.io/py/earth2observe.svg)](https://badge.fury.io/py/earth2observe)
[![Conda Version](https://img.shields.io/conda/vn/conda-forge/earth2observe.svg)](https://anaconda.org/conda-forge/earth2observe)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/earth2observe)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![codecov](https://codecov.io/gh/serapeum-org/earth2observe/branch/main/graph/badge.svg)](https://codecov.io/gh/serapeum-org/earth2observe)

**earth2observe** is a Python package providing a unified API for several remote sensing data sources.

## Main Features

- **ECMWF**: ERA Interim download from the ECMWF Climate Data Store
- **CHIRPS**: CHIRPS rainfall data download via FTP
- **Amazon S3**: ERA5 data from the public AWS `era5-pds` bucket
- **Google Earth Engine**: GEE data access (under development)

```mermaid
graph LR
    earth2observe --> ECMWF
    earth2observe --> CHIRPS
    earth2observe --> Amazon-S3
    earth2observe --> Google-Earth-Engine
```

## Quick Start

```python
from earth2observe.earth2observe import Earth2Observe

e2o = Earth2Observe(
    data_source="chirps",
    temporal_resolution="daily",
    start="2009-01-01",
    end="2009-01-10",
    variables=["precipitation"],
    lat_lim=[4.19, 4.64],
    lon_lim=[-75.65, -74.73],
    path="examples/data/chirps",
)
e2o.download()
```

## Installation

=== "conda"

    ```bash
    conda install -c conda-forge earth2observe
    ```

=== "pip"

    ```bash
    pip install earth2observe
    ```

=== "GitHub"

    ```bash
    pip install git+https://github.com/serapeum-org/earth2observe.git
    ```
