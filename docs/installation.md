# Installation

## Stable Release

Please install earthly in a virtual environment so that its requirements don't tamper with your system's Python.

### conda

The easiest way to install `earthly` is using the `conda` package manager. `earthly` is available in the [conda-forge](https://conda-forge.org) channel:

```bash
conda install -c conda-forge earthly
```

If this works, it will install earthly with all dependencies including Python and GDAL, and you can skip the rest of the installation instructions.

### pixi

You can also use [pixi](https://pixi.sh) to manage the environment:

```bash
pixi add earthly
```

### Installing Python and GDAL dependencies

The main dependencies for earthly are Python 3.11+ and GDAL.

For Python we recommend using the [Anaconda Distribution](https://www.anaconda.com/download/) for Python 3.

### Install as a conda environment

The easiest and most robust way to install earthly is in a separate conda environment. In the root repository directory there is an `environment.yml` file that lists all dependencies:

```bash
conda env create -f environment.yml
```

This creates a new environment with the name `earthly`. To activate it:

```bash
conda activate earthly
```

Then install a release of earthly from PyPI. Each backend's SDK
is an optional extra — pick the ones you actually need:

```bash
pip install earthly[ecmwf]    # ECMWF / Copernicus CDS (cdsapi)
pip install earthly[s3]       # ERA5 on AWS S3 (boto3)
pip install earthly[gee]      # Google Earth Engine
pip install earthly[all]      # everything
```

A bare `pip install earthly` installs only the core dependencies
(numpy, pandas, etc.) plus the CHIRPS FTP backend (no SDK needed).
Asking the facade for `data_source="ecmwf"` (or `"amazon-s3"`,
or `"gee"`) without the matching extra raises a clear
`ImportError` naming the missing extra.

## From Sources

The sources for earthly can be downloaded from the [GitHub repo](https://github.com/serapeum-org/earthly).

Clone the public repository:

```bash
git clone https://github.com/serapeum-org/earthly.git
```

Or download the tarball:

```bash
curl -OJL https://github.com/serapeum-org/earthly/tarball/main
```

Once you have a copy of the source, you can install it with the
extras you need:

```bash
pip install -e ".[ecmwf]"
# or all backends at once:
pip install -e ".[all]"
```

To install directly from GitHub (from the HEAD of the main branch):

```bash
pip install "earthly[ecmwf] @ git+https://github.com/serapeum-org/earthly.git"
```

Or from a specific release:

```bash
pip install "earthly[ecmwf] @ git+https://github.com/serapeum-org/earthly.git@{release}"
```

Now you should be able to start Python and try `import earthly` to verify the installation.

## Install using pip

Besides the recommended conda environment setup, you can also install earthly with `pip`. For the more difficult to install Python dependencies, it is best to use conda:

```bash
conda install numpy scipy gdal pyproj
```

Then install earthly with pip, picking the backend extras you
need (see "From PyPI" above for the available extras):

```bash
pip install earthly[ecmwf]
```

## Development install

If you are planning to contribute to earthly, do an editable install
with the `[all]` extra so the full test suite (which exercises every
backend) can run:

```bash
git clone https://github.com/serapeum-org/earthly.git
cd earthly
conda activate earthly
pip install -e ".[all]"
```

More details on conda environments: [Managing environments](https://conda.io/docs/user-guide/tasks/manage-environments.html)
