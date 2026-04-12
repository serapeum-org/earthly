# Installation

## Stable Release

Please install earth2observe in a virtual environment so that its requirements don't tamper with your system's Python.

### conda

The easiest way to install `earth2observe` is using the `conda` package manager. `earth2observe` is available in the [conda-forge](https://conda-forge.org) channel:

```bash
conda install -c conda-forge earth2observe
```

If this works, it will install earth2observe with all dependencies including Python and GDAL, and you can skip the rest of the installation instructions.

### pixi

You can also use [pixi](https://pixi.sh) to manage the environment:

```bash
pixi add earth2observe
```

### Installing Python and GDAL dependencies

The main dependencies for earth2observe are Python 3.11+ and GDAL.

For Python we recommend using the [Anaconda Distribution](https://www.anaconda.com/download/) for Python 3.

### Install as a conda environment

The easiest and most robust way to install earth2observe is in a separate conda environment. In the root repository directory there is an `environment.yml` file that lists all dependencies:

```bash
conda env create -f environment.yml
```

This creates a new environment with the name `earth2observe`. To activate it:

```bash
conda activate earth2observe
```

Then install a release of earth2observe from PyPI:

```bash
pip install earth2observe
```

## From Sources

The sources for earth2observe can be downloaded from the [GitHub repo](https://github.com/serapeum-org/earth2observe).

Clone the public repository:

```bash
git clone https://github.com/serapeum-org/earth2observe.git
```

Or download the tarball:

```bash
curl -OJL https://github.com/serapeum-org/earth2observe/tarball/main
```

Once you have a copy of the source, you can install it with:

```bash
pip install -e .
```

To install directly from GitHub (from the HEAD of the main branch):

```bash
pip install git+https://github.com/serapeum-org/earth2observe.git
```

Or from a specific release:

```bash
pip install git+https://github.com/serapeum-org/earth2observe.git@{release}
```

Now you should be able to start Python and try `import earth2observe` to verify the installation.

## Install using pip

Besides the recommended conda environment setup, you can also install earth2observe with `pip`. For the more difficult to install Python dependencies, it is best to use conda:

```bash
conda install numpy scipy gdal netcdf4 pyproj
```

Then install earth2observe with pip:

```bash
pip install earth2observe
```

## Development install

If you are planning to contribute to earth2observe, do an editable install:

```bash
git clone https://github.com/serapeum-org/earth2observe.git
cd earth2observe
conda activate earth2observe
pip install -e .[dev,test]
```

More details on conda environments: [Managing environments](https://conda.io/docs/user-guide/tasks/manage-environments.html)
