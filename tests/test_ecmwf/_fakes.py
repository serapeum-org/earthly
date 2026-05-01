"""Shared in-memory fakes for the ECMWF test suite.

Pulled out of the per-test files so each split file has the same
mocking primitives without copy-pasting. None of these are pytest
fixtures — they are plain helper classes / functions imported where
needed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class _SentinelClient:
    """Stand-in for :class:`cdsapi.Client` used in initialize tests.

    Empty by design — tests that need to assert "the constructed
    client is exactly this one" use `is` identity comparison
    against an instance of this class.
    """


class _FakeVariable:
    """Variable subset returned by :class:`_FakeNetCDFDataset.variables`.

    Mirrors the small slice of :class:`pyramids.netcdf.NetCDF` that
    `post_download` needs from a per-variable subset — only
    `read_array()` returning the in-memory numpy array.
    """

    def __init__(self, array):
        self._array = array

    def read_array(self, **_kwargs):
        return self._array


class _FakeVariableInfo:
    """Per-variable metadata returned by :class:`_FakeMetadata.variables`.

    Mirrors the slice of :class:`pyramids.netcdf.NetCDFMetadata`
    that :func:`_read_time_axis` reads: the `unit` attribute on
    the time variable carries the CF-style `"<unit> since
    <epoch>"` string and is the source of truth for parsing the
    raw integer values.
    """

    def __init__(self, unit):
        self.unit = unit


class _FakeMetadata:
    """`meta_data` stand-in carrying just `.variables`."""

    def __init__(self, variables):
        self.variables = variables


class _FakeNetCDFDataset:
    """In-memory stand-in for :class:`pyramids.netcdf.NetCDF`.

    Mimics the small subset of the pyramids API that
    :meth:`ECMWF.post_download` consumes:

    * `read_array(variable=name)` returns the data array
    * `meta_data.variables[name].unit` returns the CF-style
      `"<unit> since <epoch>"` string for time parsing
    * `_read_variable(name)` returns coordinate values
    * `lon` / `lat` properties return 1-D coordinate axes
    * `file_name` returns the path the fake was opened with
    * `close()` is a no-op
    * Supports `with` (`__enter__` / `__exit__`)

    Each instance records the path it was opened with on the
    class-level `instances` list so tests can assert which file
    was opened.
    """

    instances = []

    def __init__(self, path, mode="r"):
        type(self).instances.append((path, mode))
        # Time axis: 16 six-hourly samples spanning 2022-01-01 through
        # 2022-01-04, expressed as "seconds since 1970-01-01" — the
        # CDS-Beta units now in use. Span chosen to cover the
        # `ecmwf_stub` fixture's three-day download window with one
        # day of head-room either side.
        epoch = pd.Timestamp("1970-01-01")
        sample_dates = pd.date_range("2022-01-01", periods=16, freq="6h")
        time_axis = np.array(
            [(d - epoch).total_seconds() for d in sample_dates], dtype=float
        )
        self._lon = np.linspace(-75.0, -74.0, 9)
        self._lat = np.linspace(5.0, 4.0, 9)
        self._time_axis = time_axis
        self.variables = {}
        self.meta_data = _FakeMetadata(
            {"valid_time": _FakeVariableInfo("seconds since 1970-01-01")}
        )
        self.file_name = path
        self._fake_data = np.full((len(time_axis), 9, 9), 273.15, dtype=float)
        self._arrays_by_variable = {}

    def _read_variable(self, name):
        if name == "valid_time":
            return self._time_axis
        return None

    def read_array(self, variable=None, **_kwargs):
        if variable in self._arrays_by_variable:
            return self._arrays_by_variable[variable]
        return self._fake_data

    def set_variable(self, name, array):
        self._arrays_by_variable[name] = array

    @property
    def lon(self):
        return self._lon

    @property
    def lat(self):
        return self._lat

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def install_fake_netcdf(monkeypatch, var_value=273.15):
    """Patch `earth2observe.ecmwf.NetCDF` to return :class:`_FakeNetCDFDataset`.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        var_value: Constant the fake will fill the t2m variable array
            with (in Kelvin for temperature; the post_download factors
            then convert to Celsius).

    Returns:
        list: `_FakeNetCDFDataset.instances` — captures
        `(path, mode)` for every constructor call so tests can
        assert which file was opened.
    """
    _FakeNetCDFDataset.instances = []

    def _read_file(path, read_only=True, **_kwargs):
        ds = _FakeNetCDFDataset(path, "r" if read_only else "w")
        ds.set_variable(
            "t2m", np.full(ds._fake_data.shape, var_value, dtype=float)
        )
        ds.set_variable(
            "tp", np.full(ds._fake_data.shape, 0.001, dtype=float)
        )
        return ds

    fake_class = type(
        "_FakeNetCDFFactory", (), {"read_file": staticmethod(_read_file)}
    )
    monkeypatch.setattr("earth2observe.ecmwf.backend.NetCDF", fake_class)
    return _FakeNetCDFDataset.instances


def captured_request(stub):
    """Return the request dict from the most recent `client.retrieve` call.

    Args:
        stub: An `ECMWF` stub whose `client` is a `MagicMock`.

    Returns:
        dict: The `request` positional argument passed to
        `client.retrieve(dataset, request, target)`.
    """
    return stub.client.retrieve.call_args[0][1]
