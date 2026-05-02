[![Documentation Status](https://readthedocs.org/projects/earthly/badge/?version=latest)](https://earthly.readthedocs.io/en/latest/?badge=latest)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/earthly)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Language grade: Python](https://img.shields.io/lgtm/grade/python/g/MAfarrag/Hapi.svg?logo=lgtm&logoWidth=18)](https://lgtm.com/projects/g/MAfarrag/Hapi/context:python)


[![codecov](https://codecov.io/gh/Serapieum-of-alex/earthly/branch/main/graph/badge.svg?token=2nBcI5ijvB)](https://codecov.io/gh/Serapieum-of-alex/earthly)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/4c95cf4c0dd044e4b451e08355fe6ee7)](https://www.codacy.com/gh/Serapieum-of-alex/earthly/dashboard?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=Serapieum-of-alex/earthly&amp;utm_campaign=Badge_Grade)
![GitHub last commit](https://img.shields.io/github/last-commit/MAfarrag/earthobserve)
![GitHub forks](https://img.shields.io/github/forks/MAfarrag/earthobserve?style=social)
![GitHub Repo stars](https://img.shields.io/github/stars/MAfarrag/earthobserve?style=social)


Current release info
====================

| Name | Downloads                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Version | Platforms |
| --- |-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------| --- | --- |
| [![Conda Recipe](https://img.shields.io/badge/recipe-earthly-green.svg)](https://anaconda.org/conda-forge/earthly) | [![Conda Downloads](https://img.shields.io/conda/dn/conda-forge/earthly.svg)](https://anaconda.org/conda-forge/earthly) [![Downloads](https://pepy.tech/badge/earthly)](https://pepy.tech/project/earthly) [![Downloads](https://pepy.tech/badge/earthly/month)](https://pepy.tech/project/earthly)  [![Downloads](https://pepy.tech/badge/earthly/week)](https://pepy.tech/project/earthly)  ![PyPI - Downloads](https://img.shields.io/pypi/dd/earthly?color=blue&style=flat-square) ![GitHub all releases](https://img.shields.io/github/downloads/MAfarrag/earthly/total) | [![Conda Version](https://img.shields.io/conda/vn/conda-forge/earthly.svg)](https://anaconda.org/conda-forge/earthly) [![PyPI version](https://badge.fury.io/py/earthly.svg)](https://badge.fury.io/py/earthly) [![Anaconda-Server Badge](https://anaconda.org/conda-forge/earthly/badges/version.svg)](https://anaconda.org/conda-forge/earthly) | [![Conda Platforms](https://img.shields.io/conda/pn/conda-forge/earthly.svg)](https://anaconda.org/conda-forge/earthly) [![Join the chat at https://gitter.im/Hapi-Nile/Hapi](https://badges.gitter.im/Hapi-Nile/Hapi.svg)](https://gitter.im/Hapi-Nile/Hapi?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge&utm_content=badge) |

earthobserve - Remote Sensing package
=====================================================================
**earthobserve** is a Remote Sensing package

earthobserve

Main Features
-------------
  - ERA Interim Download
  - CHIRPS Rainfall data Download
  - ERA5 from Amason S3 data source


Future work
-------------
  - Google earth engine
  - ERA 5



Installing earthobserve
===============

Installing `earthobserve` from the `conda-forge` channel can be achieved by:

```
conda install -c conda-forge earthobserve
```

It is possible to list all of the versions of `earthobserve` available on your platform with:

```
conda search earthobserve --channel conda-forge
```

## Install from Github
to install the last development to time you can install the library from github
```
pip install git+https://github.com/MAfarrag/earthobserve
```

## pip
to install the last release you can easly use pip
```
pip install earthobserve==0.2.2
```

Quick start
===========

```
  >>> import earthobserve
```

[other code samples](https://earthobserve.readthedocs.io/en/latest/?badge=latest)
