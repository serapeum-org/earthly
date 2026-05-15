"""Tests for the `EarthLens` facade's Google Earth Engine path (plan tasks H9 / M4).

The `GEE` backend itself is not re-exercised here (see `tests/gee/test_backend.py`);
these tests check the facade wiring — registry resolution, kwarg forwarding,
delegation, and the missing-`[gee]`-extra `ImportError` — with `earthlens.gee.GEE`
monkeypatched to a `MagicMock` so no real auth happens.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

import earthlens.gee
from earthlens.earthlens import _LazyRegistry  # noqa: F401 - imported for completeness
from earthlens.earthlens import EarthLens


@pytest.fixture(scope="function")
def fake_gee(monkeypatch):
    """Replace `earthlens.gee.GEE` with a `MagicMock` class.

    Returns:
        MagicMock: The stand-in for the `GEE` backend class. Its
        `call_args` records how the facade constructed it, and
        `.return_value` is the (mock) backend instance the facade binds
        to `EarthLens.datasource`.
    """
    fake = MagicMock(name="GEE", __name__="GEE")
    monkeypatch.setattr(earthlens.gee, "GEE", fake)
    return fake


def _gee_kwargs(**overrides):
    """Return a baseline `EarthLens(data_source="gee", ...)` kwargs dict."""
    params = dict(
        data_source="gee",
        variables={"USGS/SRTMGL1_003": ["elevation"]},
        start="2000-02-11",
        end="2000-02-12",
        lat_lim=[29.9, 30.0],
        lon_lim=[31.2, 31.3],
        temporal_resolution="raw",
        path="out",
        fmt="%Y-%m-%d",
    )
    params.update(overrides)
    return params


class TestRegistry:
    """Tests for the `"gee"` / `"google-earth-engine"` registry entries."""

    def test_keys_present(self):
        """Both GEE keys are registered alongside the other backends.

        Test scenario:
            `"gee"` and `"google-earth-engine"` are in `EarthLens.DataSources`,
            and `sorted(...)` lists them with chirps/amazon-s3/ecmwf.
        """
        assert "gee" in EarthLens.DataSources
        assert "google-earth-engine" in EarthLens.DataSources
        assert sorted(EarthLens.DataSources) == [
            "amazon-s3", "chc", "chirps", "ecmwf", "gee", "google-earth-engine",
        ]

    def test_keys_resolve_to_gee_class(self):
        """Both keys resolve to `earthlens.gee.GEE`.

        Test scenario:
            Indexing the registry returns the same `GEE` class for both
            the canonical key and the alias.
        """
        assert EarthLens.DataSources["gee"] is earthlens.gee.GEE
        assert EarthLens.DataSources["google-earth-engine"] is earthlens.gee.GEE
        assert EarthLens.DataSources["gee"].__name__ == "GEE"


class TestFacadeConstruction:
    """Tests for `EarthLens(data_source="gee", ...)`."""

    def test_constructs_backend_with_standard_args(self, fake_gee):
        """The facade builds the `GEE` backend with the standard arguments.

        Test scenario:
            `EarthLens(data_source="gee", ...)` calls `GEE(...)` once with
            `start`/`end`/`variables`/`lat_lim`/`lon_lim`/`temporal_resolution`/
            `path`/`fmt`, and binds the result to `datasource`.
        """
        el = EarthLens(**_gee_kwargs())
        fake_gee.assert_called_once()
        kwargs = fake_gee.call_args.kwargs
        assert kwargs["start"] == "2000-02-11" and kwargs["end"] == "2000-02-12"
        assert kwargs["variables"] == {"USGS/SRTMGL1_003": ["elevation"]}
        assert kwargs["lat_lim"] == [29.9, 30.0] and kwargs["lon_lim"] == [31.2, 31.3]
        assert kwargs["temporal_resolution"] == "raw" and kwargs["path"] == "out"
        assert kwargs["fmt"] == "%Y-%m-%d"
        assert el.datasource is fake_gee.return_value

    def test_forwards_backend_kwargs(self, fake_gee):
        """GEE-specific keyword arguments are forwarded verbatim to `GEE(...)`.

        Test scenario:
            `service_account` / `service_key` / `project` / `scale` / `crs` /
            `reducer` / `export_via` / `drive_folder` / `gcs_bucket` / `region`
            all appear in the `GEE` constructor call.
        """
        extra = dict(
            service_account="sa@x.iam", service_key="key.json", project="p",
            scale=90, crs="EPSG:3857", reducer="median", export_via="drive",
            drive_folder="ee_out", gcs_bucket="b", region="a-geodataframe-sentinel",
        )
        EarthLens(**_gee_kwargs(**extra))
        kwargs = fake_gee.call_args.kwargs
        for name, value in extra.items():
            assert kwargs.get(name) == value, f"{name} not forwarded: {kwargs.get(name)!r}"

    def test_alias_builds_same_backend(self, fake_gee):
        """`data_source="google-earth-engine"` constructs the `GEE` backend too.

        Test scenario:
            Using the alias instantiates the same mock class.
        """
        EarthLens(**_gee_kwargs(data_source="google-earth-engine"))
        fake_gee.assert_called_once()

    def test_default_bbox_when_omitted(self, fake_gee):
        """Omitting `lat_lim` / `lon_lim` passes the whole-Earth defaults.

        Test scenario:
            `EarthLens(data_source="gee", variables=...)` with no bbox →
            `GEE(..., lat_lim=[-90, 90], lon_lim=[-180, 180])`.
        """
        params = _gee_kwargs()
        params.pop("lat_lim")
        params.pop("lon_lim")
        EarthLens(**params)
        kwargs = fake_gee.call_args.kwargs
        assert kwargs["lat_lim"] == [-90, 90]
        assert kwargs["lon_lim"] == [-180, 180]

    def test_unknown_data_source_raises_value_error(self):
        """An unknown `data_source` is rejected before any backend import.

        Test scenario:
            `EarthLens(variables=[], data_source="nope")` → `ValueError`.
        """
        with pytest.raises(ValueError, match="nope not supported"):
            EarthLens(variables=[], data_source="nope")


class TestFacadeDownloadDelegation:
    """Tests for `EarthLens.download` routing to the GEE backend."""

    def test_download_delegates(self, fake_gee):
        """`download()` is forwarded to the bound backend.

        Test scenario:
            `EarthLens(data_source="gee", ...).download(progress_bar=False)` →
            `GEE(...).download(progress_bar=False)`.
        """
        el = EarthLens(**_gee_kwargs())
        el.download(progress_bar=False)
        fake_gee.return_value.download.assert_called_once_with(progress_bar=False)

    def test_download_forwards_aggregate(self, fake_gee):
        """A non-`None` `aggregate=` is forwarded as a keyword.

        Test scenario:
            `download(aggregate=<sentinel>)` reaches the backend as
            `download(progress_bar=..., aggregate=<sentinel>)`.
        """
        sentinel = object()
        el = EarthLens(**_gee_kwargs())
        el.download(progress_bar=False, aggregate=sentinel)
        called = fake_gee.return_value.download.call_args.kwargs
        assert called["aggregate"] is sentinel


class TestMissingExtra:
    """Tests for the friendly error when the `[gee]` SDK is not installed."""

    def test_missing_earthengine_api_raises_friendly_importerror(self, monkeypatch):
        """A missing `earthengine-api` surfaces as a `pip install` hint.

        Test scenario:
            With `importlib.import_module("earthlens.gee")` made to raise
            `ImportError`, `EarthLens(data_source="gee", ...)` raises an
            `ImportError` naming the backend and the `earthlens[gee]` extra.
        """
        real_import = importlib.import_module

        def fake_import(name, *args, **kwargs):
            if name == "earthlens.gee":
                raise ImportError("No module named 'ee'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr("earthlens.earthlens.importlib.import_module", fake_import)
        with pytest.raises(ImportError, match=r"Backend 'gee' is unavailable.*earthlens\[gee\]"):
            EarthLens(variables={"USGS/SRTMGL1_003": ["elevation"]}, data_source="gee")
