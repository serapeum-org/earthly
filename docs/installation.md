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

Then install a release of earthly from PyPI:

```bash
pip install earthly
```

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

Once you have a copy of the source, you can install it with:

```bash
pip install -e .
```

To install directly from GitHub (from the HEAD of the main branch):

```bash
pip install git+https://github.com/serapeum-org/earthly.git
```

Or from a specific release:

```bash
pip install git+https://github.com/serapeum-org/earthly.git@{release}
```

Now you should be able to start Python and try `import earthly` to verify the installation.

## Install using pip

Besides the recommended conda environment setup, you can also install earthly with `pip`. For the more difficult to install Python dependencies, it is best to use conda:

```bash
conda install numpy scipy gdal pyproj
```

Then install earthly with pip:

```bash
pip install earthly
```

## Development install

If you are planning to contribute to earthly, do an editable install:

```bash
git clone https://github.com/serapeum-org/earthly.git
cd earthly
conda activate earthly
pip install -e .[dev,test]
```

More details on conda environments: [Managing environments](https://conda.io/docs/user-guide/tasks/manage-environments.html)
