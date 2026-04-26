"""Integration tests for the H1 parent-class wiring.

After H1, :meth:`AbstractDataSource.__init__` captures the return
values of the abstract hooks (``initialize``, ``create_grid``,
``check_input_dates``) and exposes them as ``self.client`` /
``self.space`` / ``self.time``. It also adds ``self.root_dir`` and
keeps ``self.path`` as a legacy alias.

These tests construct a real :class:`ECMWF` instance (with cdsapi
mocked) and assert the wiring end-to-end across both modules — they
are integration-level rather than narrow unit tests.
"""

from __future__ import annotations

import cdsapi
import pytest

from earth2observe.ecmwf import ECMWF, VariableSpec

from tests.ecmwf._fakes import _SentinelClient

pytestmark = [pytest.mark.integration]


class TestParentClassWiring:
    """Tests for the H1 parent-class wiring in :class:`AbstractDataSource`."""

    def test_full_construction_populates_all_expected_attributes(
        self, tmp_path, monkeypatch
    ):
        """Constructing ECMWF wires up client/space/time/root_dir.

        Test scenario:
            With cdsapi.Client patched out, instantiating ECMWF must
            populate every attribute the api() method consumes —
            without the fixture having to set them by hand.
        """
        sentinel = _SentinelClient()
        monkeypatch.setattr(cdsapi, "Client", lambda: sentinel)

        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-03",
            variables=["2T"],
            lat_lim=[4.19, 4.64],
            lon_lim=[-75.65, -74.73],
            path=str(tmp_path),
        )

        assert ecmwf.client is sentinel
        assert ecmwf.space.latitude_min <= 4.19 <= ecmwf.space.latitude_max
        assert ecmwf.space.longitude_min <= -75.65 <= ecmwf.space.longitude_max
        assert ecmwf.time.dates is not None
        assert ecmwf.root_dir == tmp_path.resolve()

    def test_root_dir_and_path_are_aliases(self, tmp_path, monkeypatch):
        """``self.path`` is preserved as an alias of ``self.root_dir``.

        Test scenario:
            CHIRPS and S3 both still reference ``self.path``. The H1
            change must keep that name working alongside the new
            ``self.root_dir``.
        """
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables=["2T"],
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )
        assert ecmwf.path == ecmwf.root_dir

    def test_api_works_directly_off_a_real_constructed_instance(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: ECMWF().api(var_info) submits a real request.

        Test scenario:
            With cdsapi mocked, building an ECMWF instance and calling
            ``api(var_info)`` must:

            * route to client.retrieve(dataset, request, target)
            * write the target path under self.root_dir
            * return the target

            This is the H1 acceptance check — the api() rewrite from
            C1 actually runs against a normally-constructed instance,
            not a hand-stubbed one.
        """
        retrieved = []

        class FakeClient:
            def retrieve(self, dataset, request, target):
                retrieved.append((dataset, request, target))

        monkeypatch.setattr(cdsapi, "Client", FakeClient)

        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables=["2T"],
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )

        target = ecmwf.api(
            VariableSpec(
                cds_dataset="reanalysis-era5-single-levels",
                cds_variable="2m_temperature",
                nc_variable="t2m",
                file_name="Tair",
                units="C",
                factors_add=0,
                factors_mul=1,
            )
        )

        assert len(retrieved) == 1
        dataset, request, target_str = retrieved[0]
        assert dataset == "reanalysis-era5-single-levels"
        assert request["variable"] == ["2m_temperature"]
        assert target_str == str(target)
        assert target.parent == tmp_path.resolve()

    def test_api_uppercase_compatibility_shim_raises(
        self, tmp_path, monkeypatch
    ):
        """``API`` (uppercase) raises NotImplementedError on ECMWF.

        Test scenario:
            CHIRPS and S3 use ``API`` as a per-date download hook;
            ECMWF works at variable granularity and exposes ``api``
            (lowercase) instead. The uppercase method exists only to
            satisfy the abstract base class — calling it must surface
            a clear NotImplementedError.
        """
        monkeypatch.setattr(cdsapi, "Client", lambda: _SentinelClient())
        ecmwf = ECMWF(
            start="2022-01-01",
            end="2022-01-01",
            variables=["2T"],
            lat_lim=[4.0, 5.0],
            lon_lim=[-75.0, -74.0],
            path=str(tmp_path),
        )
        with pytest.raises(NotImplementedError, match="api"):
            ecmwf.API()
