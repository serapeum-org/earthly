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
    client is exactly this one" use ``is`` identity comparison
    against an instance of this class.
    """


class _FakeVariable:
    """Variable subset returned by :class:`_FakeNetCDFDataset.variables`.

    Mirrors the small slice of :class:`pyramids.netcdf.NetCDF` that
    ``post_download`` needs from a per-variable subset — only
    ``read_array()`` returning the in-memory numpy array.
    """

    def __init__(self, array):
        self._array = array

    def read_array(self, **_kwargs):
        return self._array


class _FakeNetCDFDataset:
    """In-memory stand-in for :class:`pyramids.netcdf.NetCDF`.

    Mimics the small subset of the pyramids API that
    :meth:`ECMWF.post_download` consumes:

    * ``read_array(variable=name)`` returns the data array
    * ``variables[name].read_array()`` returns coordinate / dim arrays
    * ``lon`` / ``lat`` properties return 1-D coordinate axes
    * ``close()`` is a no-op
    * Supports ``with`` (``__enter__`` / ``__exit__``)

    Each instance records the path it was opened with on the
    class-level ``instances`` list so tests can assert which file
    was opened.
    """

    instances = []

    def __init__(self, path, mode="r"):
        type(self).instances.append((path, mode))
        time_axis = np.arange(0, 24 * 4, 6, dtype=float) + (
            (
                pd.Timestamp("2022-01-01") - pd.Timestamp("1900-01-01")
            ).total_seconds()
            / 3600
        )
        self._lon = np.linspace(-75.0, -74.0, 9)
        self._lat = np.linspace(5.0, 4.0, 9)
        self.variables = {"time": _FakeVariable(np.array(time_axis))}
        self._fake_data = np.full((len(time_axis), 9, 9), 273.15, dtype=float)
        self._arrays_by_variable = {}

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
    """Patch ``earth2observe.ecmwf.NetCDF`` to return :class:`_FakeNetCDFDataset`.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        var_value: Constant the fake will fill the t2m variable array
            with (in Kelvin for temperature; the post_download factors
            then convert to Celsius).

    Returns:
        list: ``_FakeNetCDFDataset.instances`` — captures
        ``(path, mode)`` for every constructor call so tests can
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
    """Return the request dict from the most recent ``client.retrieve`` call.

    Args:
        stub: An ``ECMWF`` stub whose ``client`` is a ``MagicMock``.

    Returns:
        dict: The ``request`` positional argument passed to
        ``client.retrieve(dataset, request, target)``.
    """
    return stub.client.retrieve.call_args[0][1]
