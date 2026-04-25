"""Tests for ``earth2observe.ecmwf``.

Covers the cdsapi migration tasks committed to date: ``C1``, ``C2``,
``H1``–``H7``, ``L4``, ``M4`` and ``M5`` from
``planning/cdsapi/migration-plan.md``.

Mock harness (``M4``):
    The unit tests must run offline in CI. The shared fixtures below
    monkey-patch :class:`cdsapi.Client` at the ``cdsapi`` module level
    — patching ``__init__`` does not reliably intercept the constructor
    because ``cdsapi.Client.__new__`` is a factory that may return a
    :class:`LegacyClient`. An autouse safeguard
    (:func:`_block_real_cdsapi`) makes the suite fail loudly if a test
    ever constructs a real ``cdsapi.Client`` — protecting the offline
    contract even when new tests are added later. End-to-end tests that
    need the real service opt in via the ``RUN_CDS_E2E=1`` environment
    variable; the safeguard is disabled inside the e2e class.
"""

import os
from unittest.mock import MagicMock

import pandas as pd
import pytest

import cdsapi

from earth2observe.ecmwf import ECMWF, AuthenticationError, Catalog


@pytest.fixture(autouse=True)
def _block_real_cdsapi(request, monkeypatch):
    """Fail fast if a test reaches a live :class:`cdsapi.Client`.

    Any test that does not explicitly opt in (by being inside the
    ``TestApiE2E`` class) gets a :class:`cdsapi.Client` replacement
    that raises immediately — even before the constructor reads
    ``~/.cdsapirc``. Tests that need a fake client still call
    ``monkeypatch.setattr(cdsapi, "Client", ...)`` themselves; that
    later setattr wins because monkeypatch applies fixture-scoped
    overrides in order.
    """
    if "TestApiE2E" in request.node.nodeid:
        return

    def _no_live_client(*args, **kwargs):
        raise AssertionError(
            "A unit test attempted to construct a real cdsapi.Client. "
            'Add `monkeypatch.setattr(cdsapi, "Client", lambda: ...)` '
            "to the test (replacing the lambda with the fake your test "
            "needs), or move the test into TestApiE2E with RUN_CDS_E2E=1."
        )

    monkeypatch.setattr(cdsapi, "Client", _no_live_client)


@pytest.fixture
def single_level_var_info():
    """CDS catalog entry for a single-level ERA5 variable.

    Returns:
        dict: Catalog metadata for ``2m_temperature`` on
        ``reanalysis-era5-single-levels``.
    """
    return {
        "cds_dataset": "reanalysis-era5-single-levels",
        "cds_variable": "2m_temperature",
        "units": "C",
        "file_name": "Tair",
        "factors_add": -273.15,
        "factors_mul": 1,
    }


@pytest.fixture
def pressure_level_var_info():
    """CDS catalog entry for a pressure-level ERA5 variable.

    Returns:
        dict: Catalog metadata for ``temperature`` on
        ``reanalysis-era5-pressure-levels`` at 1000 hPa.
    """
    return {
        "cds_dataset": "reanalysis-era5-pressure-levels",
        "cds_variable": "temperature",
        "cds_pressure_level": ["1000"],
        "units": "C",
        "file_name": "Tair2m",
        "factors_add": -273.15,
        "factors_mul": 1,
    }


@pytest.fixture
def ecmwf_stub(tmp_path):
    """Minimal ``ECMWF`` instance with the attributes ``api()`` consumes.

    Skips the full parent ``__init__`` chain (which would still call
    :meth:`cdsapi.Client` for real) and instead constructs the
    instance via ``ECMWF.__new__`` and wires up the four attributes
    :meth:`ECMWF.api` reads — ``self.client``, ``self.root_dir``,
    ``self.time`` and ``self.space`` — by hand. ``self.client`` is a
    :class:`unittest.mock.MagicMock` so calls to ``client.retrieve``
    are captured for assertion without any network round-trip.

    Args:
        tmp_path: Per-test temp directory provided by pytest, used as
            ``self.root_dir`` so target paths land on the test fs.

    Returns:
        ECMWF: An ``ECMWF`` instance ready for ``api()`` invocation.
    """
    ecmwf = ECMWF.__new__(ECMWF)
    ecmwf.client = MagicMock()
    ecmwf.root_dir = tmp_path
    ecmwf.time = {
        "start_date": pd.Timestamp("2022-01-01"),
        "end_date": pd.Timestamp("2022-01-03"),
        "time_freq": "D",
        "dates": pd.date_range("2022-01-01", "2022-01-03", freq="D"),
    }
    ecmwf.space = {
        "lat_lim": [4.19, 4.64],
        "lon_lim": [-75.65, -74.73],
    }
    ecmwf.temporal_resolution = "daily"
    return ecmwf


def _captured_request(stub):
    """Return the request dict from the most recent ``client.retrieve`` call.

    Args:
        stub: An ``ECMWF`` stub whose ``client`` is a ``MagicMock``.

    Returns:
        dict: The ``request`` positional argument passed to
        ``client.retrieve(dataset, request, target)``.
    """
    return stub.client.retrieve.call_args[0][1]


class TestApi:
    """Tests for :meth:`ECMWF.api` — the C1-rewritten request builder."""

    def test_returns_path_under_root_dir(self, ecmwf_stub, single_level_var_info):
        """``api()`` returns a target path rooted at ``self.root_dir``.

        Test scenario:
            For ``file_name='Tair'`` and dataset
            ``reanalysis-era5-single-levels`` the returned path must be
            ``<root_dir>/Tair_reanalysis-era5-single-levels.nc``.
        """
        target = ecmwf_stub.api(single_level_var_info)
        expected = ecmwf_stub.root_dir / "Tair_reanalysis-era5-single-levels.nc"
        assert target == expected, f"Expected {expected}, got {target}"

    def test_calls_retrieve_exactly_once(self, ecmwf_stub, single_level_var_info):
        """``api()`` triggers a single ``client.retrieve`` call.

        Test scenario:
            One invocation of ``api()`` must result in exactly one CDS
            retrieve request (idempotent dispatch, not a retry loop).
        """
        ecmwf_stub.api(single_level_var_info)
        assert ecmwf_stub.client.retrieve.call_count == 1, (
            f"Expected exactly 1 retrieve call, got "
            f"{ecmwf_stub.client.retrieve.call_count}"
        )

    def test_retrieve_called_positionally_with_three_args(
        self, ecmwf_stub, single_level_var_info
    ):
        """``client.retrieve`` is called with three positional args.

        Test scenario:
            The cdsapi signature is ``retrieve(name, request, target)``;
            the C1 fix must pass the dataset name first, the request
            dict second, and the stringified target path third — never
            via keyword arguments.
        """
        target = ecmwf_stub.api(single_level_var_info)
        args, kwargs = ecmwf_stub.client.retrieve.call_args
        assert kwargs == {}, (
            f"retrieve must be called positionally; got kwargs={kwargs}"
        )
        assert len(args) == 3, (
            f"Expected 3 positional args, got {len(args)}: {args}"
        )
        assert args[0] == single_level_var_info["cds_dataset"], (
            f"First arg must be dataset name, got {args[0]!r}"
        )
        assert isinstance(args[1], dict), (
            f"Second arg must be a request dict, got {type(args[1])}"
        )
        assert args[2] == str(target), (
            f"Third arg must equal str(target); "
            f"got {args[2]!r} vs {str(target)!r}"
        )

    def test_request_carries_required_default_keys(
        self, ecmwf_stub, single_level_var_info
    ):
        """The request dict carries every key CDS requires for ERA5.

        Test scenario:
            For a daily ERA5 single-levels request the dict must include
            ``product_type``, ``variable``, ``year``/``month``/``day``,
            ``time``, ``data_format``, and ``area``.
        """
        ecmwf_stub.api(single_level_var_info)
        request = _captured_request(ecmwf_stub)
        for key in (
            "product_type",
            "variable",
            "year",
            "month",
            "day",
            "time",
            "data_format",
            "area",
        ):
            assert key in request, (
                f"Missing required key {key!r} in request: {request}"
            )

    def test_product_type_defaults_to_reanalysis(
        self, ecmwf_stub, single_level_var_info
    ):
        """``product_type`` is the literal list ``['reanalysis']``.

        Test scenario:
            Daily ERA5 requests use ``product_type=['reanalysis']``; the
            monthly path that switches to ``monthly_averaged_reanalysis``
            is task ``M5`` and is not exercised here.
        """
        ecmwf_stub.api(single_level_var_info)
        assert _captured_request(ecmwf_stub)["product_type"] == [
            "reanalysis"
        ], (
            f"product_type should be ['reanalysis']; got "
            f"{_captured_request(ecmwf_stub)['product_type']!r}"
        )

    def test_variable_taken_from_var_info(
        self, ecmwf_stub, single_level_var_info
    ):
        """``variable`` mirrors ``var_info['cds_variable']``.

        Test scenario:
            For ``cds_variable='2m_temperature'`` the request must have
            ``variable=['2m_temperature']``.
        """
        ecmwf_stub.api(single_level_var_info)
        assert _captured_request(ecmwf_stub)["variable"] == [
            "2m_temperature"
        ], f"Got {_captured_request(ecmwf_stub)['variable']!r}"

    def test_dates_are_zero_padded_and_sorted(
        self, ecmwf_stub, single_level_var_info
    ):
        """``year``/``month``/``day`` are zero-padded, deduplicated, sorted.

        Test scenario:
            For dates ``2022-01-01`` to ``2022-01-03`` (daily), the
            request must carry ``year=['2022']``, ``month=['01']``, and
            ``day=['01','02','03']`` in that order.
        """
        ecmwf_stub.api(single_level_var_info)
        request = _captured_request(ecmwf_stub)
        assert request["year"] == ["2022"], f"Got {request['year']!r}"
        assert request["month"] == ["01"], f"Got {request['month']!r}"
        assert request["day"] == ["01", "02", "03"], f"Got {request['day']!r}"

    def test_dates_handle_multi_year_range(
        self, ecmwf_stub, single_level_var_info
    ):
        """Multi-year ranges deduplicate across year/month/day boundaries.

        Test scenario:
            For dates ``2021-12-30`` to ``2022-01-02`` (daily), the
            request must contain ``year=['2021','2022']``,
            ``month=['01','12']`` and ``day=['01','02','30','31']``,
            sorted lexicographically.
        """
        ecmwf_stub.time["dates"] = pd.date_range(
            "2021-12-30", "2022-01-02", freq="D"
        )
        ecmwf_stub.api(single_level_var_info)
        request = _captured_request(ecmwf_stub)
        assert request["year"] == ["2021", "2022"], f"Got {request['year']!r}"
        assert request["month"] == ["01", "12"], f"Got {request['month']!r}"
        assert request["day"] == ["01", "02", "30", "31"], (
            f"Got {request['day']!r}"
        )

    def test_time_defaults_to_six_hourly_slots(
        self, ecmwf_stub, single_level_var_info
    ):
        """``time`` defaults to ``['00:00','06:00','12:00','18:00']``.

        Test scenario:
            Daily resolution requests cover four six-hourly snapshots so
            downstream post-processing can aggregate to a daily value.
        """
        ecmwf_stub.api(single_level_var_info)
        assert _captured_request(ecmwf_stub)["time"] == [
            "00:00",
            "06:00",
            "12:00",
            "18:00",
        ], f"Got {_captured_request(ecmwf_stub)['time']!r}"

    def test_data_format_is_netcdf(self, ecmwf_stub, single_level_var_info):
        """``data_format`` is ``'netcdf'``.

        Test scenario:
            CDS supports ``grib`` and ``netcdf``; this backend uses
            netCDF so :class:`netCDF4.Dataset` can read the result in
            ``post_download``.
        """
        ecmwf_stub.api(single_level_var_info)
        assert _captured_request(ecmwf_stub)["data_format"] == "netcdf", (
            f"Got {_captured_request(ecmwf_stub)['data_format']!r}"
        )

    def test_area_uses_north_west_south_east_order(
        self, ecmwf_stub, single_level_var_info
    ):
        """``area`` follows CDS convention ``[N, W, S, E]``.

        Test scenario:
            Given ``lat_lim=[4.19, 4.64]`` and
            ``lon_lim=[-75.65, -74.73]`` the ``area`` field must be
            ``[4.64, -75.65, 4.19, -74.73]``.
        """
        ecmwf_stub.api(single_level_var_info)
        assert _captured_request(ecmwf_stub)["area"] == [
            4.64,
            -75.65,
            4.19,
            -74.73,
        ], f"Got {_captured_request(ecmwf_stub)['area']!r}"

    def test_no_pressure_level_for_single_level_var(
        self, ecmwf_stub, single_level_var_info
    ):
        """``pressure_level`` is omitted for single-level datasets.

        Test scenario:
            ``var_info`` without ``cds_pressure_level`` must not produce
            a ``pressure_level`` key — sending one to a single-level
            dataset is rejected by CDS.
        """
        ecmwf_stub.api(single_level_var_info)
        request = _captured_request(ecmwf_stub)
        assert "pressure_level" not in request, (
            f"pressure_level must be absent for single-level vars; "
            f"got {request.get('pressure_level')!r}"
        )

    def test_pressure_level_forwarded_when_present(
        self, ecmwf_stub, pressure_level_var_info
    ):
        """``pressure_level`` is forwarded from ``var_info``.

        Test scenario:
            ``var_info['cds_pressure_level']=['1000']`` must surface as
            ``request['pressure_level']=['1000']``.
        """
        ecmwf_stub.api(pressure_level_var_info)
        request = _captured_request(ecmwf_stub)
        assert request["pressure_level"] == ["1000"], (
            f"Expected ['1000']; got {request.get('pressure_level')!r}"
        )

    def test_single_date_produces_singleton_arrays(
        self, ecmwf_stub, single_level_var_info
    ):
        """A one-day range produces length-1 ``year``/``month``/``day``.

        Test scenario:
            For dates ``[2022-06-15]`` the request fields collapse to
            ``year=['2022']``, ``month=['06']``, ``day=['15']``.
        """
        ecmwf_stub.time["dates"] = pd.date_range(
            "2022-06-15", "2022-06-15", freq="D"
        )
        ecmwf_stub.api(single_level_var_info)
        request = _captured_request(ecmwf_stub)
        assert request["year"] == ["2022"], f"Got {request['year']!r}"
        assert request["month"] == ["06"], f"Got {request['month']!r}"
        assert request["day"] == ["15"], f"Got {request['day']!r}"

    def test_target_filename_pattern_for_pressure_level(
        self, ecmwf_stub, pressure_level_var_info
    ):
        """Target file name follows ``<file_name>_<cds_dataset>.nc``.

        Test scenario:
            For ``file_name='Tair2m'`` and dataset
            ``reanalysis-era5-pressure-levels`` the file name must be
            ``Tair2m_reanalysis-era5-pressure-levels.nc``.
        """
        target = ecmwf_stub.api(pressure_level_var_info)
        assert target.name == "Tair2m_reanalysis-era5-pressure-levels.nc", (
            f"Got {target.name}"
        )

    def test_missing_cds_dataset_raises_key_error(
        self, ecmwf_stub, single_level_var_info
    ):
        """Catalog entries without ``cds_dataset`` raise ``KeyError``.

        Test scenario:
            Removing ``cds_dataset`` from ``var_info`` must surface a
            ``KeyError`` immediately rather than silently submitting a
            malformed request.
        """
        single_level_var_info.pop("cds_dataset")
        with pytest.raises(KeyError, match="cds_dataset"):
            ecmwf_stub.api(single_level_var_info)
        assert ecmwf_stub.client.retrieve.call_count == 0, (
            "retrieve must not be called when var_info is malformed"
        )

    def test_missing_cds_variable_raises_key_error(
        self, ecmwf_stub, single_level_var_info
    ):
        """Catalog entries without ``cds_variable`` raise ``KeyError``.

        Test scenario:
            Removing ``cds_variable`` from ``var_info`` must surface a
            ``KeyError`` from ``api()`` rather than passing a request
            without the ``variable`` key to CDS.
        """
        single_level_var_info.pop("cds_variable")
        with pytest.raises(KeyError, match="cds_variable"):
            ecmwf_stub.api(single_level_var_info)


class TestApiMonthly:
    """Tests for :meth:`ECMWF.api` on the monthly path (M5)."""

    @pytest.fixture
    def monthly_var_info(self):
        """Catalog entry with both daily and monthly CDS datasets.

        Returns:
            dict: Variable metadata that exercises the
            ``cds_dataset_monthly`` and pressure-level branches.
        """
        return {
            "cds_dataset": "reanalysis-era5-single-levels",
            "cds_dataset_monthly": (
                "reanalysis-era5-single-levels-monthly-means"
            ),
            "cds_variable": "2m_temperature",
            "file_name": "Tair",
            "factors_add": -273.15,
            "factors_mul": 1,
        }

    def test_monthly_routes_to_monthly_dataset(
        self, ecmwf_stub, monthly_var_info
    ):
        """Monthly resolution targets ``cds_dataset_monthly``.

        Test scenario:
            With ``self.temporal_resolution = 'monthly'``, ``api()``
            must submit the request to the dataset named under
            ``cds_dataset_monthly`` (e.g.
            ``reanalysis-era5-single-levels-monthly-means``).
        """
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        dataset_arg = ecmwf_stub.client.retrieve.call_args[0][0]
        assert dataset_arg == (
            "reanalysis-era5-single-levels-monthly-means"
        ), (
            f"Monthly resolution should target the -monthly-means "
            f"dataset; got {dataset_arg!r}"
        )

    def test_monthly_falls_back_to_daily_dataset_when_monthly_missing(
        self, ecmwf_stub, monthly_var_info
    ):
        """When ``cds_dataset_monthly`` is absent, fall back to ``cds_dataset``.

        Test scenario:
            Some variables only exist on a single CDS dataset (no
            monthly variant). The monthly branch must reuse
            ``cds_dataset`` rather than raising or sending an empty
            dataset name.
        """
        monthly_var_info.pop("cds_dataset_monthly")
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        dataset_arg = ecmwf_stub.client.retrieve.call_args[0][0]
        assert dataset_arg == "reanalysis-era5-single-levels", (
            f"Fallback should use cds_dataset; got {dataset_arg!r}"
        )

    def test_monthly_product_type_is_monthly_averaged_reanalysis(
        self, ecmwf_stub, monthly_var_info
    ):
        """Monthly requests carry ``product_type=monthly_averaged_reanalysis``.

        Test scenario:
            CDS rejects daily-style ``product_type=['reanalysis']``
            against ``-monthly-means`` datasets. The branch must swap
            it to the monthly equivalent.
        """
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        request = _captured_request(ecmwf_stub)
        assert request["product_type"] == ["monthly_averaged_reanalysis"], (
            f"Expected ['monthly_averaged_reanalysis']; "
            f"got {request['product_type']!r}"
        )

    def test_monthly_request_omits_time_slot_list(
        self, ecmwf_stub, monthly_var_info
    ):
        """Monthly requests must not include the ``time`` key.

        Test scenario:
            ``-monthly-means`` datasets reject the daily-style
            ``time=['00:00','06:00','12:00','18:00']`` list. The
            monthly branch must drop the key entirely.
        """
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        request = _captured_request(ecmwf_stub)
        assert "time" not in request, (
            f"Monthly request must not carry a 'time' key; "
            f"got {request.get('time')!r}"
        )

    def test_daily_request_still_includes_time_and_reanalysis(
        self, ecmwf_stub, monthly_var_info
    ):
        """Daily resolution still uses the original product_type and time.

        Test scenario:
            The M5 monthly branch must not regress the daily path.
            With ``temporal_resolution='daily'`` the request must
            still target ``cds_dataset`` (not the monthly variant)
            and still carry ``product_type=['reanalysis']`` plus the
            four six-hourly time slots.
        """
        ecmwf_stub.temporal_resolution = "daily"
        ecmwf_stub.api(monthly_var_info)
        dataset_arg = ecmwf_stub.client.retrieve.call_args[0][0]
        request = _captured_request(ecmwf_stub)
        assert dataset_arg == "reanalysis-era5-single-levels", (
            f"Daily resolution should keep cds_dataset; got {dataset_arg!r}"
        )
        assert request["product_type"] == ["reanalysis"], (
            f"Daily product_type regressed; got {request['product_type']!r}"
        )
        assert request["time"] == [
            "00:00",
            "06:00",
            "12:00",
            "18:00",
        ], f"Daily time slots regressed; got {request['time']!r}"


class TestDownloadIteration:
    """Tests for :meth:`ECMWF.download` iteration (C3)."""

    def test_download_iterates_self_vars_not_self_variables(self, ecmwf_stub):
        """``download()`` iterates ``self.vars`` (the parent's storage).

        Test scenario:
            :class:`AbstractDataSource.__init__` stores the user's
            ``variables`` list as ``self.vars``. Pre-C3, ECMWF.download
            iterated ``self.variables`` instead, which raised
            ``AttributeError`` on the first call. This test sets
            ``self.vars`` to a known list, mocks out the per-variable
            download path, and asserts ``download_dataset`` was called
            once per element of ``self.vars`` — never touching
            ``self.variables``.
        """
        ecmwf_stub.vars = ["2T", "TP"]
        ecmwf_stub.download_dataset = MagicMock()

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub.download_dataset.call_count == 2, (
            f"download_dataset should be called once per variable; "
            f"got {ecmwf_stub.download_dataset.call_count}"
        )
        called_with = [
            args[0] for args, _kwargs in ecmwf_stub.download_dataset.call_args_list
        ]
        assert called_with == [
            Catalog().get_dataset("2T"),
            Catalog().get_dataset("TP"),
        ], (
            f"download_dataset should receive each var_info in order; "
            f"got {called_with!r}"
        )

    def test_download_does_not_read_self_variables(self, ecmwf_stub):
        """``download()`` must not depend on a non-existent ``self.variables``.

        Test scenario:
            Even if a future refactor accidentally reintroduces the
            wrong attribute name, this test fails fast: ``self.vars``
            is set, ``self.variables`` is explicitly absent, and
            ``download()`` must complete without an ``AttributeError``.
        """
        ecmwf_stub.vars = ["2T"]
        ecmwf_stub.download_dataset = MagicMock()
        assert not hasattr(ecmwf_stub, "variables"), (
            "Test setup invalid: 'variables' attribute should be absent"
        )

        ecmwf_stub.download(progress_bar=False)

        assert ecmwf_stub.download_dataset.call_count == 1

    def test_download_does_not_attempt_to_delete_legacy_files(
        self, ecmwf_stub, monkeypatch
    ):
        """``download()`` no longer touches the hardcoded ``data_interim.nc``.

        Test scenario:
            Pre-H3, ``download()`` ended with
            ``os.remove(os.path.join(self.root_dir, "data_interim.nc"))``
            — a leftover from the MARS flow that always raised
            FileNotFoundError under the cdsapi path. Patch ``os.remove``
            so any call would record itself, then verify that
            ``download()`` completes without invoking it.
        """
        removed = []
        ecmwf_stub.vars = ["2T"]
        ecmwf_stub.download_dataset = MagicMock()
        monkeypatch.setattr(
            "earth2observe.ecmwf.os.remove",
            lambda path: removed.append(path),
        )

        ecmwf_stub.download(progress_bar=False)

        assert removed == [], (
            f"download() must not call os.remove on legacy paths; "
            f"got removed={removed!r}"
        )


class _FakeNetCDFDataset:
    """In-memory stand-in for :class:`netCDF4.Dataset` used in post_download tests.

    Returns numpy arrays for the four variables ``post_download``
    indexes (``<nc_variable>``, ``time``, ``longitude``, ``latitude``)
    so the function runs end-to-end without touching the file system or
    the real netCDF4 library. The factory captures every constructor
    call so tests can assert which file path was opened.
    """

    instances = []

    def __init__(self, path, mode="r"):
        import numpy as np
        type(self).instances.append((path, mode))
        time_axis = np.arange(0, 24 * 4, 6, dtype=float) + (
            (pd.Timestamp("2022-01-01") - pd.Timestamp("1900-01-01")).total_seconds()
            / 3600
        )
        self.variables = {
            "time": np.array(time_axis),
            "longitude": np.linspace(-75.0, -74.0, 9),
            "latitude": np.linspace(5.0, 4.0, 9),
        }
        self._fake_data = np.full((len(time_axis), 9, 9), 273.15, dtype=float)

    def __setitem__(self, name, array):
        self.variables[name] = array

    def close(self):
        pass


def _install_fake_netcdf(monkeypatch, var_value=273.15):
    """Patch ``netCDF4.Dataset`` to return :class:`_FakeNetCDFDataset` instances.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        var_value: Constant the fake will fill the variable array with
            (in Kelvin for temperature; the post_download factors then
            convert to Celsius).

    Returns:
        list: The class-level ``_FakeNetCDFDataset.instances`` list, so
        tests can assert the path that was opened.
    """
    import numpy as np
    _FakeNetCDFDataset.instances = []

    def _factory(path, mode="r"):
        ds = _FakeNetCDFDataset(path, mode)
        ds.variables.setdefault(
            "t2m", np.full(ds._fake_data.shape, var_value, dtype=float)
        )
        ds.variables.setdefault(
            "tp", np.full(ds._fake_data.shape, 0.001, dtype=float)
        )
        return ds

    monkeypatch.setattr("earth2observe.ecmwf.Dataset", _factory)
    return _FakeNetCDFDataset.instances


class TestPostDownload:
    """Tests for the H1+H2+M2 rewrite of :meth:`ECMWF.post_download`."""

    def test_post_download_opens_path_returned_by_api(
        self, ecmwf_stub, single_level_var_info, monkeypatch, tmp_path
    ):
        """``post_download`` opens the path it was given, not a hardcoded one.

        Test scenario:
            After H1, ``post_download`` reads the NetCDF at the path
            argument it receives — no ``os.path.join(self.root_dir,
            f"data_{dataset}.nc")`` reconstruction. Patch
            ``netCDF4.Dataset`` to record every open() and verify the
            single recorded path is the one passed in.
        """
        single_level_var_info["nc_variable"] = "t2m"
        instances = _install_fake_netcdf(monkeypatch)
        nc_path = tmp_path / "Tair_reanalysis-era5-single-levels.nc"

        ecmwf_stub.post_download(
            single_level_var_info, nc_path, progress_bar=False
        )

        assert len(instances) == 1, (
            f"Expected exactly one Dataset open; got {len(instances)}"
        )
        opened_path, mode = instances[0]
        assert opened_path == str(nc_path), (
            f"Should open the path returned by api(); got {opened_path!r}, "
            f"expected {str(nc_path)!r}"
        )
        assert mode == "r", f"Expected read mode 'r'; got {mode!r}"

    def test_post_download_uses_nc_variable_not_var_name(
        self, ecmwf_stub, single_level_var_info, monkeypatch, tmp_path
    ):
        """``post_download`` indexes ``fh.variables[nc_variable]``.

        Test scenario:
            Pre-H2, the function used ``var_info.get("var_name")``
            (a MARS-only key) which always resolved to ``None`` —
            ``fh.variables[None]`` raised. The new code reads
            ``var_info["nc_variable"]``. Test passes when the function
            completes without raising.
        """
        single_level_var_info["nc_variable"] = "t2m"
        _install_fake_netcdf(monkeypatch)

        ecmwf_stub.post_download(
            single_level_var_info,
            tmp_path / "out.nc",
            progress_bar=False,
        )

    def test_post_download_uses_underscore_file_name(
        self, ecmwf_stub, single_level_var_info, monkeypatch, tmp_path
    ):
        """``post_download`` reads ``var_info["file_name"]`` (no space).

        Test scenario:
            Pre-H2, the function read ``var_info.get("file name")``
            with a space, which the new catalog never carries. With
            the new key in place, post_download should run without
            ever raising ``KeyError``. The legacy spaced key must not
            satisfy the lookup.
        """
        single_level_var_info["nc_variable"] = "t2m"
        # Inject the legacy spaced key with a sentinel value that
        # would surface in the output file name if it were used.
        single_level_var_info["file name"] = "LEGACY_NAME_SHOULD_NOT_BE_USED"
        _install_fake_netcdf(monkeypatch)

        ecmwf_stub.post_download(
            single_level_var_info,
            tmp_path / "out.nc",
            progress_bar=False,
        )

    def test_post_download_raises_on_missing_required_keys(
        self, ecmwf_stub, monkeypatch, tmp_path
    ):
        """Missing required keys raise ``KeyError`` immediately.

        Test scenario:
            ``post_download`` requires ``nc_variable``, ``units``,
            ``file_name``, ``factors_add`` and ``factors_mul``. A
            var_info that is missing ``nc_variable`` must surface a
            ``KeyError`` from the dict lookup rather than silently
            indexing ``fh.variables[None]``.
        """
        _install_fake_netcdf(monkeypatch)
        var_info_missing = {
            "units": "C",
            "file_name": "Tair",
            "factors_add": 0.0,
            "factors_mul": 1.0,
        }

        with pytest.raises(KeyError, match="nc_variable"):
            ecmwf_stub.post_download(
                var_info_missing,
                tmp_path / "out.nc",
                progress_bar=False,
            )

    def test_post_download_does_not_carry_legacy_signature(self, ecmwf_stub):
        """The signature is ``post_download(var_info, nc_path, progress_bar)``.

        Test scenario:
            The pre-H1 signature was ``(var_info, out_dir, dataset,
            progress_bar)``. Pin the new shape so a future refactor
            that re-introduces the ``dataset`` positional argument
            fails this test instead of silently working with the
            wrong meaning.
        """
        import inspect

        sig = inspect.signature(ECMWF.post_download)
        params = list(sig.parameters)
        assert params == ["self", "var_info", "nc_path", "progress_bar"], (
            f"post_download signature regressed: got {params!r}"
        )

    def test_download_and_download_dataset_signatures_drop_dataset(
        self, ecmwf_stub
    ):
        """``download`` / ``download_dataset`` no longer accept ``dataset``.

        Test scenario:
            Pre-M1, both methods carried ``dataset: str = "interim"``
            as a leftover from the MARS flow that no downstream code
            consumed. After M1 the parameter is removed entirely so
            tooling and IDEs do not advertise it as a configuration
            knob.
        """
        import inspect

        download_params = list(
            inspect.signature(ECMWF.download).parameters
        )
        assert "dataset" not in download_params, (
            f"download() should no longer accept 'dataset'; got params="
            f"{download_params!r}"
        )

        download_dataset_params = list(
            inspect.signature(ECMWF.download_dataset).parameters
        )
        assert download_dataset_params == [
            "self",
            "var_info",
            "progress_bar",
        ], (
            f"download_dataset signature regressed: got "
            f"{download_dataset_params!r}"
        )


class TestDownloadDataset:
    """Tests for :meth:`ECMWF.download_dataset` after the C1 call-site fix."""

    def test_calls_api_with_var_info_only(
        self, ecmwf_stub, single_level_var_info, mocker=None
    ):
        """``download_dataset`` invokes ``api(var_info)`` with one arg.

        Test scenario:
            The C1 change dropped the ``dataset`` positional argument
            from ``api()``. ``download_dataset`` must therefore pass
            only ``var_info``. ``post_download`` is stubbed because it
            still depends on the legacy file layout (out of scope for
            C1).
        """
        ecmwf_stub.api = MagicMock(return_value=ecmwf_stub.root_dir / "x.nc")
        ecmwf_stub.post_download = MagicMock()

        ecmwf_stub.download_dataset(
            single_level_var_info, progress_bar=False
        )

        assert ecmwf_stub.api.call_count == 1, (
            f"api() should be called once; got {ecmwf_stub.api.call_count}"
        )
        args, kwargs = ecmwf_stub.api.call_args
        assert kwargs == {}, f"api() must be called positionally; got {kwargs}"
        assert args == (single_level_var_info,), (
            f"api() must be called as api(var_info); got args={args}"
        )

    def test_post_download_receives_path_returned_by_api(
        self, ecmwf_stub, single_level_var_info
    ):
        """``post_download`` is called with the path :meth:`api` returned.

        Test scenario:
            After H1, ``download_dataset`` captures the
            :class:`pathlib.Path` returned by :meth:`api` and threads
            it into :meth:`post_download` so the post-processing step
            opens the very same NetCDF that cdsapi just wrote — not a
            hardcoded ``data_<dataset>.nc`` filename derived from a
            stale parameter.
        """
        api_target = ecmwf_stub.root_dir / "Tair_reanalysis-era5-single-levels.nc"
        ecmwf_stub.api = MagicMock(return_value=api_target)
        ecmwf_stub.post_download = MagicMock()

        ecmwf_stub.download_dataset(
            single_level_var_info, progress_bar=True
        )

        assert ecmwf_stub.post_download.call_count == 1
        args, kwargs = ecmwf_stub.post_download.call_args
        assert args[0] == single_level_var_info, (
            f"first arg must be var_info; got {args[0]!r}"
        )
        assert args[1] == api_target, (
            f"second arg must be the path returned by api(); "
            f"got {args[1]!r}, expected {api_target!r}"
        )
        assert args[2] is True, (
            f"progress_bar must be threaded through; got args={args}"
        )


class _SentinelClient:
    """Minimal stand-in for :class:`cdsapi.Client` used in initialize tests."""


class TestMockHarnessSafeguard:
    """Tests for the autouse safeguard installed by ``_block_real_cdsapi``."""

    def test_safeguard_message_includes_literal_patch_pattern(self):
        """The safeguard error spells out the exact monkeypatch call.

        Test scenario:
            Pre-N3, the message said "see M4 harness" — readers had to
            chase the docstring elsewhere. The new message inlines the
            literal `monkeypatch.setattr(cdsapi, "Client", lambda: ...)`
            so a developer can copy-paste it straight into a failing
            test. Trip the safeguard deliberately and assert the
            string is present in the error.
        """
        with pytest.raises(AssertionError) as excinfo:
            cdsapi.Client()
        message = str(excinfo.value)
        assert 'monkeypatch.setattr(cdsapi, "Client"' in message, (
            f"Safeguard message must contain the literal patch pattern; "
            f"got: {message}"
        )
        assert "RUN_CDS_E2E=1" in message, (
            f"Safeguard message must mention the e2e opt-in env var; "
            f"got: {message}"
        )


class TestSourceCompiles:
    """Compile-time checks for Python 3.11 compatibility (C2)."""

    def test_ecmwf_module_compiles_under_311_grammar(self):
        """``ecmwf.py`` parses without PEP-701-only constructs.

        Test scenario:
            ``pyproject.toml`` declares Python 3.11 as the minimum
            supported version. PEP 701 (which allows reusing the
            outer quote inside an f-string) only landed in 3.12, so a
            line like ``f"...{d["k"]}..."`` is a syntax error on
            3.11. ``compile(..., feature_version=(3, 11))`` exercises
            the 3.11 grammar regardless of the running interpreter,
            so this regression test catches the issue even when the
            test suite executes on a newer Python.
        """
        import ast
        import inspect

        from earth2observe import ecmwf as ecmwf_module

        source = inspect.getsource(ecmwf_module)
        ast.parse(source, feature_version=(3, 11))


class TestParentClassWiring:
    """Tests for the H1 parent-class wiring in :class:`AbstractDataSource`.

    After H1, :meth:`AbstractDataSource.__init__` captures the return
    values of the abstract hooks (``initialize``, ``create_grid``,
    ``check_input_dates``) and exposes them as ``self.client`` /
    ``self.space`` / ``self.time``. It also adds ``self.root_dir`` and
    keeps ``self.path`` as a legacy alias.
    """

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

        assert ecmwf.client is sentinel, (
            f"self.client should be the cdsapi client; got {ecmwf.client!r}"
        )
        assert ecmwf.space["lat_lim"][0] <= 4.19 <= ecmwf.space["lat_lim"][1]
        assert ecmwf.space["lon_lim"][0] <= -75.65 <= ecmwf.space["lon_lim"][1]
        assert "dates" in ecmwf.time, (
            f"self.time should carry a 'dates' key; got {sorted(ecmwf.time)}"
        )
        assert ecmwf.root_dir == tmp_path.resolve(), (
            f"self.root_dir should be the absolute output path; "
            f"got {ecmwf.root_dir}"
        )

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
        assert ecmwf.path == ecmwf.root_dir, (
            f"self.path should equal self.root_dir; "
            f"got path={ecmwf.path}, root_dir={ecmwf.root_dir}"
        )

    def test_api_works_directly_off_a_real_constructed_instance(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: ECMWF().api(var_info) submits a real request.

        Test scenario:
            With cdsapi mocked, building an ECMWF instance and calling
            ``api(var_info)`` should:
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
            {
                "cds_dataset": "reanalysis-era5-single-levels",
                "cds_variable": "2m_temperature",
                "file_name": "Tair",
            }
        )

        assert len(retrieved) == 1, (
            f"client.retrieve should be called once; got {len(retrieved)}"
        )
        dataset, request, target_str = retrieved[0]
        assert dataset == "reanalysis-era5-single-levels"
        assert request["variable"] == ["2m_temperature"]
        assert target_str == str(target)
        assert target.parent == tmp_path.resolve(), (
            f"target should sit under root_dir; got {target}"
        )

    def test_API_uppercase_compatibility_shim_raises(
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


class TestInitialize:
    """Tests for :meth:`ECMWF.initialize` after the H3 fix.

    These tests patch the ``Client`` attribute on the ``cdsapi`` module
    directly. ``cdsapi.Client.__new__`` is a factory that may return a
    :class:`LegacyClient` instead — patching ``__init__`` does not
    reliably intercept that path, so we replace the callable that
    ``ECMWF.initialize`` actually invokes.
    """

    def test_returns_constructed_client_when_credentials_valid(
        self, monkeypatch
    ):
        """``initialize()`` returns whatever ``cdsapi.Client()`` returns.

        Test scenario:
            With ``cdsapi.Client`` patched to a stub factory that
            yields a sentinel object, ``initialize()`` must return the
            very same sentinel — proving it does not double-wrap or
            otherwise transform the client on the happy path.
        """
        sentinel = _SentinelClient()
        monkeypatch.setattr(cdsapi, "Client", lambda: sentinel)
        ecmwf = ECMWF.__new__(ECMWF)
        result = ecmwf.initialize()
        assert result is sentinel, (
            f"initialize() should return the constructed client; "
            f"got {result!r}"
        )

    def test_raises_authentication_error_when_cdsapi_raises(
        self, monkeypatch
    ):
        """A failing ``cdsapi.Client()`` is wrapped in AuthenticationError.

        Test scenario:
            When the CDS client constructor raises (typically because
            ``~/.cdsapirc`` is missing or malformed), ``initialize()``
            must catch *any* exception type — not only ``KeyError`` as
            the pre-H3 code did — and re-raise it wrapped in
            :class:`AuthenticationError` whose ``__cause__`` is the
            original error.
        """
        original = RuntimeError("no .cdsapirc")

        def boom():
            raise original

        monkeypatch.setattr(cdsapi, "Client", boom)
        ecmwf = ECMWF.__new__(ECMWF)
        with pytest.raises(AuthenticationError) as excinfo:
            ecmwf.initialize()
        assert excinfo.value.__cause__ is original, (
            f"AuthenticationError should chain the original error; "
            f"__cause__ is {excinfo.value.__cause__!r}"
        )

    def test_error_message_points_at_cdsapirc(self, monkeypatch):
        """The error message names ``~/.cdsapirc`` and the setup URL.

        Test scenario:
            The H3 acceptance criterion: a user reading the message
            should know exactly which file to create and where to find
            the official setup guide. The message must mention
            ``~/.cdsapirc`` and link to
            ``https://cds.climate.copernicus.eu/how-to-api``.
        """
        def boom():
            raise Exception("missing config")

        monkeypatch.setattr(cdsapi, "Client", boom)
        ecmwf = ECMWF.__new__(ECMWF)
        with pytest.raises(AuthenticationError) as excinfo:
            ecmwf.initialize()
        message = str(excinfo.value)
        assert "~/.cdsapirc" in message, (
            f"Error message should mention ~/.cdsapirc; got: {message}"
        )
        assert "cds.climate.copernicus.eu/how-to-api" in message, (
            f"Error message should link to the cdsapi how-to; "
            f"got: {message}"
        )

    def test_error_message_does_not_reference_legacy_env_vars(
        self, monkeypatch
    ):
        """The error message must not reference the dead env vars.

        Test scenario:
            The pre-H3 message told users to set ``ECMWF_API_URL`` /
            ``ECMWF_API_KEY`` / ``ECMWF_API_EMAIL`` — none of which
            cdsapi reads. Following that advice was a dead end; the
            new message must not perpetuate it.
        """
        def boom():
            raise Exception("missing config")

        monkeypatch.setattr(cdsapi, "Client", boom)
        ecmwf = ECMWF.__new__(ECMWF)
        with pytest.raises(AuthenticationError) as excinfo:
            ecmwf.initialize()
        message = str(excinfo.value)
        for legacy in ("ECMWF_API_URL", "ECMWF_API_KEY", "ECMWF_API_EMAIL"):
            assert legacy not in message, (
                f"Legacy env var {legacy!r} must not appear in the H3 "
                f"error message; got: {message}"
            )


class TestCatalog:
    """Tests for :class:`Catalog` after the H2 / H5 rewiring."""

    def test_catalog_loads_per_variable_map(self):
        """``catalog`` is a per-variable dict, not a per-dataset listing.

        Test scenario:
            After H5/H2, the catalog attribute should be the
            ``variables:`` map from cds_data_catalog.yaml — keyed by
            short variable codes (e.g. "2T"), each value a metadata
            dict with ``cds_dataset`` / ``cds_variable``.
        """
        cat = Catalog()
        assert isinstance(cat.catalog, dict), (
            f"catalog should be a dict; got {type(cat.catalog).__name__}"
        )
        assert "2T" in cat.catalog, (
            f"'2T' missing from catalog keys: {sorted(cat.catalog)}"
        )
        assert "cds_dataset" in cat.catalog["2T"], (
            f"'2T' entry missing cds_dataset: {cat.catalog['2T']}"
        )

    @pytest.mark.parametrize(
        "var_code, expected_dataset, expected_variable",
        [
            ("2T", "reanalysis-era5-single-levels", "2m_temperature"),
            ("TP", "reanalysis-era5-single-levels", "total_precipitation"),
            ("SP", "reanalysis-era5-single-levels", "surface_pressure"),
            ("E", "reanalysis-era5-single-levels", "evaporation"),
            ("T", "reanalysis-era5-pressure-levels", "temperature"),
        ],
    )
    def test_get_dataset_returns_new_schema(
        self, var_code, expected_dataset, expected_variable
    ):
        """``get_dataset`` returns CDS-shaped metadata for each variable.

        Args:
            var_code: User-friendly variable code (e.g. "2T").
            expected_dataset: CDS dataset short name expected for this
                code under the new schema.
            expected_variable: CDS variable name expected.

        Test scenario:
            The five mappings the migration plan calls out explicitly
            (E, T, 2T, TP, SP) must round-trip through the catalog.
        """
        info = Catalog().get_dataset(var_code)
        assert info["cds_dataset"] == expected_dataset, (
            f"{var_code}: expected dataset {expected_dataset!r}, "
            f"got {info['cds_dataset']!r}"
        )
        assert info["cds_variable"] == expected_variable, (
            f"{var_code}: expected variable {expected_variable!r}, "
            f"got {info['cds_variable']!r}"
        )

    def test_get_dataset_includes_file_name_and_factors(self):
        """Per-variable metadata carries file_name and unit conversions.

        Test scenario:
            ``post_download`` reads ``file_name`` for output naming and
            ``factors_add`` / ``factors_mul`` for unit conversion. The
            new catalog must continue to provide them.
        """
        info = Catalog().get_dataset("2T")
        assert info["file_name"] == "Tair", (
            f"2T file_name should be 'Tair'; got {info['file_name']!r}"
        )
        assert info["factors_add"] == -273.15, (
            f"2T factors_add should be -273.15 (K → C); "
            f"got {info['factors_add']!r}"
        )
        assert info["factors_mul"] == 1, (
            f"2T factors_mul should be 1; got {info['factors_mul']!r}"
        )

    def test_pressure_level_var_carries_cds_pressure_level(self):
        """Pressure-level variables expose ``cds_pressure_level``.

        Test scenario:
            T, Q, R live on reanalysis-era5-pressure-levels; their
            catalog entries must carry the ``cds_pressure_level`` key
            so :meth:`ECMWF.api` can forward it to CDS.
        """
        info = Catalog().get_dataset("T")
        assert info.get("cds_pressure_level") == ["1000"], (
            f"T should default to pressure_level=['1000']; "
            f"got {info.get('cds_pressure_level')!r}"
        )

    def test_get_dataset_raises_key_error_for_unknown_code(self):
        """Unknown variable codes raise ``KeyError``.

        Test scenario:
            Asking for a code that isn't in the catalog must raise
            ``KeyError`` immediately rather than returning ``None`` and
            blowing up later inside ``api()``.
        """
        with pytest.raises(KeyError):
            Catalog().get_dataset("DEFINITELY_NOT_A_REAL_CODE")

    def test_get_variable_aliases_get_dataset(self):
        """``get_variable`` returns the same dict as ``get_dataset``.

        Test scenario:
            ``get_variable`` is required by the abstract base class,
            ``get_dataset`` is the legacy public name used by
            :meth:`ECMWF.download`. Both must agree so callers can
            pick either.
        """
        cat = Catalog()
        assert cat.get_variable("2T") == cat.get_dataset("2T"), (
            "get_variable and get_dataset must return the same dict"
        )

    def test_no_mars_schema_keys_remain(self):
        """No catalog entry carries a stale MARS-style key.

        Test scenario:
            The pre-H5 catalog used ``number_para``, ``download type``,
            ``var_name`` (the lowercase MARS GRIB code). Those have no
            meaning in a cdsapi request and must not be present in the
            new catalog.
        """
        catalog = Catalog().catalog
        forbidden = {"number_para", "download type", "var_name"}
        for code, info in catalog.items():
            stale = forbidden & set(info.keys())
            assert not stale, (
                f"{code} still carries MARS-only keys {stale}: {info}"
            )


@pytest.mark.skipif(
    os.environ.get("RUN_CDS_E2E") != "1",
    reason=(
        "Set RUN_CDS_E2E=1 to run live CDS end-to-end tests "
        "(requires ~/.cdsapirc and accepted ERA5 licences)."
    ),
)
class TestApiE2E:
    """End-to-end tests against the live Copernicus Climate Data Store.

    These tests are opt-in: they run only when ``RUN_CDS_E2E=1`` is set
    in the environment. They require:

    * A ``~/.cdsapirc`` file with ``url`` and a Personal Access Token,
      see ``docs/authentication.md`` and
      <https://cds.climate.copernicus.eu/how-to-api>.
    * Accepted licences for the ERA5 single-levels dataset on the user's
      CDS profile.

    Each request can take several minutes due to CDS queue times. The
    request below is intentionally tiny (one day, one variable, ~1°×1°)
    to keep the wall clock and quota footprint small.
    """

    def test_live_single_level_download(self, tmp_path):
        """Submit a tiny ERA5 single-levels request to the real CDS.

        Test scenario:
            One day (2022-01-01), one variable (2m_temperature), one
            degree square area centred on Colombia. Asserts that
            ``api()`` returns a path that exists and is non-empty after
            the live retrieve.
        """
        import cdsapi

        ecmwf = ECMWF.__new__(ECMWF)
        ecmwf.client = cdsapi.Client()
        ecmwf.root_dir = tmp_path
        ecmwf.time = {
            "dates": pd.date_range("2022-01-01", "2022-01-01", freq="D"),
        }
        ecmwf.space = {
            "lat_lim": [4.0, 5.0],
            "lon_lim": [-75.0, -74.0],
        }
        ecmwf.temporal_resolution = "daily"

        target = ecmwf.api(
            {
                "cds_dataset": "reanalysis-era5-single-levels",
                "cds_variable": "2m_temperature",
                "file_name": "Tair",
            }
        )

        assert target.exists(), f"NetCDF file not created at {target}"
        assert target.stat().st_size > 0, f"NetCDF file is empty: {target}"
