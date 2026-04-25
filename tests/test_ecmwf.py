"""Tests for ``earth2observe.ecmwf`` focused on the C1 changes.

The C1 task replaced ``ECMWF.api()`` with a real :mod:`cdsapi` request
builder that calls ``self.client.retrieve(dataset, request, target)``.
These tests validate the request shape, the target path, and the call
site change in :meth:`ECMWF.download_dataset`.

Notes:
    * ``AbstractDataSource.__init__`` does not yet expose ``self.client``,
      ``self.time``, ``self.space`` or ``self.root_dir`` (that is the
      ``H1`` task in ``planning/cdsapi/migration-plan.md``). The unit
      tests therefore construct an ``ECMWF`` instance with
      :meth:`object.__new__` and set the attributes ``api()`` consumes by
      hand. This isolates the C1 fix from the rest of the migration.
    * The unit tests mock :class:`cdsapi.Client` so the suite stays
      offline and deterministic.
    * The end-to-end suite at the bottom of this file is opt-in via
      ``RUN_CDS_E2E=1`` and exercises the live Copernicus Climate Data
      Store. It requires a working ``~/.cdsapirc`` and accepted licences
      for ERA5 single-levels.
"""

import os
from unittest.mock import MagicMock

import pandas as pd
import pytest

from earth2observe.ecmwf import ECMWF


class _ConcreteECMWF(ECMWF):
    """Test-only concrete subclass of ``ECMWF``.

    ``AbstractDataSource`` declares an abstract ``API`` method (uppercase)
    that no concrete data-source class in the package actually implements
    today — every backend implements ``api`` (lowercase) instead. That
    naming mismatch makes ``ECMWF`` formally abstract and prevents even
    ``ECMWF.__new__(ECMWF)`` from succeeding. This subclass plugs the gap
    so the unit tests can construct an instance without dragging in the
    rest of the migration.
    """

    def API(self):  # noqa: N802 — name dictated by the abstract base class
        """Stub for the abstract ``API`` method; intentionally unused."""
        raise NotImplementedError(
            "Tests exercise the lowercase api(); API() is only present so "
            "the abstract base class can be instantiated."
        )


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

    Bypasses the parent ``__init__`` (which does not yet populate
    ``self.client``/``self.time``/``self.space``/``self.root_dir`` —
    see ``H1`` in the migration plan) and manually wires the attributes
    that the C1-rewritten ``api()`` reads. ``self.client`` is a
    :class:`unittest.mock.MagicMock`, so calls to ``client.retrieve`` are
    captured for assertion without any network round-trip.

    Args:
        tmp_path: Per-test temp directory provided by pytest, used as
            ``self.root_dir`` so target paths land on the test fs.

    Returns:
        ECMWF: An ``ECMWF`` instance ready for ``api()`` invocation.
    """
    ecmwf = _ConcreteECMWF.__new__(_ConcreteECMWF)
    ecmwf.client = MagicMock()
    ecmwf.root_dir = tmp_path
    ecmwf.time = {
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
        ecmwf_stub.path = ecmwf_stub.root_dir

        ecmwf_stub.download_dataset(
            single_level_var_info, dataset="interim", progress_bar=False
        )

        assert ecmwf_stub.api.call_count == 1, (
            f"api() should be called once; got {ecmwf_stub.api.call_count}"
        )
        args, kwargs = ecmwf_stub.api.call_args
        assert kwargs == {}, f"api() must be called positionally; got {kwargs}"
        assert args == (single_level_var_info,), (
            f"api() must be called as api(var_info); got args={args}"
        )

    def test_post_download_still_receives_dataset_name(
        self, ecmwf_stub, single_level_var_info
    ):
        """``post_download`` keeps its ``dataset`` argument.

        Test scenario:
            The legacy ``post_download`` flow is out of C1's scope and
            still receives the ``dataset`` argument from
            ``download_dataset`` so further migration tasks can
            iteratively replace it.
        """
        ecmwf_stub.api = MagicMock(return_value=ecmwf_stub.root_dir / "x.nc")
        ecmwf_stub.post_download = MagicMock()
        ecmwf_stub.path = ecmwf_stub.root_dir

        ecmwf_stub.download_dataset(
            single_level_var_info, dataset="my-ds", progress_bar=True
        )

        assert ecmwf_stub.post_download.call_count == 1
        args, kwargs = ecmwf_stub.post_download.call_args
        assert args[0] == single_level_var_info, (
            f"first arg must be var_info; got {args[0]!r}"
        )
        assert args[2] == "my-ds", (
            f"dataset name must be threaded through; got args={args}"
        )
        assert args[3] is True, (
            f"progress_bar must be threaded through; got args={args}"
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

        ecmwf = _ConcreteECMWF.__new__(_ConcreteECMWF)
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
