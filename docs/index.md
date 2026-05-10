# earthlens

[![Docs](https://img.shields.io/badge/docs-mkdocs-blue)](https://serapeum-org.github.io/earthlens/)
[![PyPI version](https://badge.fury.io/py/earthlens.svg)](https://badge.fury.io/py/earthlens)
[![Conda Version](https://img.shields.io/conda/vn/conda-forge/earthlens.svg)](https://anaconda.org/conda-forge/earthlens)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/earthlens)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![codecov](https://codecov.io/gh/serapeum-org/earthlens/branch/main/graph/badge.svg)](https://codecov.io/gh/serapeum-org/earthlens)

**earthlens** is a Python package providing a unified API for several remote sensing data sources.

## Main Features

- **ECMWF**: ERA Interim download from the ECMWF Climate Data Store
- **CHIRPS**: CHIRPS rainfall data download via FTP
- **Amazon S3**: ERA5 data from the public AWS `era5-pds` bucket
- **Google Earth Engine**: GEE data access (under development)

```mermaid
graph LR
    earthlens --> ECMWF
    earthlens --> CHIRPS
    earthlens --> Amazon-S3
    earthlens --> Google-Earth-Engine
```

## Quick Start

```python
from earthlens.earthlens import EarthLens

earthlens = EarthLens(
    data_source="chirps",
    temporal_resolution="daily",
    start="2009-01-01",
    end="2009-01-10",
    variables=["precipitation"],
    lat_lim=[4.19, 4.64],
    lon_lim=[-75.65, -74.73],
    path="examples/data/chirps",
)
earthlens.download()
```

## Installation

=== "conda"

    ```bash
    conda install -c conda-forge earthlens
    ```

=== "pip"

    ```bash
    pip install earthlens
    ```

=== "GitHub"

    ```bash
    pip install git+https://github.com/serapeum-org/earthlens.git
    ```
