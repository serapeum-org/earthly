"""Unit tests for :meth:`ECMWF.api`.

Covers the daily request shape (TestApi) and the monthly branch
(TestApiMonthly). The full set of assertions on request keys, area
ordering, date deduplication, pressure-level forwarding, dataset
naming, and CDS-side error translation lives here.

All tests in this module are pure unit tests — they never touch the
network. The autouse ``_block_real_cdsapi`` safeguard in
``conftest.py`` makes that an enforced contract.
"""

from __future__ import annotations


import pandas as pd
import pytest

from earth2observe.ecmwf import Variable

from tests.test_ecmwf._fakes import captured_request

pytestmark = [pytest.mark.unit]


class TestApi:
    """Tests for :meth:`ECMWF.api` — the C1-rewritten request builder."""

    def test_returns_path_under_root_dir(self, ecmwf_stub, single_level_var_info):
        """api() returns <root_dir>/<cds_variable>_<cds_dataset>.nc."""
        target = ecmwf_stub.api(single_level_var_info)
        expected = (
            ecmwf_stub.root_dir
            / "2m_temperature_reanalysis-era5-single-levels.nc"
        )
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
        assert args[0] == single_level_var_info.cds_dataset, (
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
        request = captured_request(ecmwf_stub)
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
            Daily ERA5 requests use ``product_type=['reanalysis']``.
        """
        ecmwf_stub.api(single_level_var_info)
        assert captured_request(ecmwf_stub)["product_type"] == ["reanalysis"]

    def test_variable_taken_from_var_info(
        self, ecmwf_stub, single_level_var_info
    ):
        """``variable`` mirrors ``var_info.cds_variable``.

        Test scenario:
            For ``cds_variable='2m_temperature'`` the request must have
            ``variable=['2m_temperature']``.
        """
        ecmwf_stub.api(single_level_var_info)
        assert captured_request(ecmwf_stub)["variable"] == ["2m_temperature"]

    def test_dates_are_zero_padded_and_sorted(
        self, ecmwf_stub, single_level_var_info
    ):
        """``year``/``month``/``day`` are zero-padded, deduplicated, sorted.

        Test scenario:
            For dates ``2022-01-01`` to ``2022-01-03`` (daily), the
            request must carry ``year=['2022']``, ``month=['01']``, and
            ``day=['01','02','03']``.
        """
        ecmwf_stub.api(single_level_var_info)
        request = captured_request(ecmwf_stub)
        assert request["year"] == ["2022"]
        assert request["month"] == ["01"]
        assert request["day"] == ["01", "02", "03"]

    def test_dates_handle_multi_year_range(
        self, ecmwf_stub, single_level_var_info
    ):
        """Multi-year ranges deduplicate across year/month/day boundaries.

        Test scenario:
            For dates ``2021-12-30`` to ``2022-01-02`` (daily), the
            request must contain ``year=['2021','2022']``,
            ``month=['01','12']`` and ``day=['01','02','30','31']``.
        """
        ecmwf_stub.time = ecmwf_stub.time.model_copy(
            update={
                "dates": pd.date_range("2021-12-30", "2022-01-02", freq="D"),
            }
        )
        ecmwf_stub.api(single_level_var_info)
        request = captured_request(ecmwf_stub)
        assert request["year"] == ["2021", "2022"]
        assert request["month"] == ["01", "12"]
        assert request["day"] == ["01", "02", "30", "31"]

    def test_time_defaults_to_six_hourly_slots(
        self, ecmwf_stub, single_level_var_info
    ):
        """``time`` defaults to ``['00:00','06:00','12:00','18:00']``.

        Test scenario:
            Daily resolution requests cover four six-hourly snapshots
            so downstream post-processing can aggregate to a daily
            value.
        """
        ecmwf_stub.api(single_level_var_info)
        assert captured_request(ecmwf_stub)["time"] == [
            "00:00",
            "06:00",
            "12:00",
            "18:00",
        ]

    def test_data_format_is_netcdf(self, ecmwf_stub, single_level_var_info):
        """``data_format`` is ``'netcdf'``."""
        ecmwf_stub.api(single_level_var_info)
        assert captured_request(ecmwf_stub)["data_format"] == "netcdf"

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
        assert captured_request(ecmwf_stub)["area"] == [
            4.64,
            -75.65,
            4.19,
            -74.73,
        ]

    def test_no_pressure_level_for_single_level_var(
        self, ecmwf_stub, single_level_var_info
    ):
        """``pressure_level`` is omitted for single-level datasets.

        Test scenario:
            ``var_info`` without ``cds_pressure_level`` must not
            produce a ``pressure_level`` key — sending one to a
            single-level dataset is rejected by CDS.
        """
        ecmwf_stub.api(single_level_var_info)
        assert "pressure_level" not in captured_request(ecmwf_stub)

    def test_pressure_level_forwarded_when_present(
        self, ecmwf_stub, pressure_level_var_info
    ):
        """``pressure_level`` is forwarded from ``var_info``.

        Test scenario:
            ``var_info.cds_pressure_level=['1000']`` must surface as
            ``request['pressure_level']=['1000']``.
        """
        ecmwf_stub.api(pressure_level_var_info)
        assert captured_request(ecmwf_stub)["pressure_level"] == ["1000"]

    def test_single_date_produces_singleton_arrays(
        self, ecmwf_stub, single_level_var_info
    ):
        """A one-day range produces length-1 ``year``/``month``/``day``.

        Test scenario:
            For dates ``[2022-06-15]`` the request fields collapse to
            ``year=['2022']``, ``month=['06']``, ``day=['15']``.
        """
        ecmwf_stub.time = ecmwf_stub.time.model_copy(
            update={
                "dates": pd.date_range("2022-06-15", "2022-06-15", freq="D"),
            }
        )
        ecmwf_stub.api(single_level_var_info)
        request = captured_request(ecmwf_stub)
        assert request["year"] == ["2022"]
        assert request["month"] == ["06"]
        assert request["day"] == ["15"]

    def test_target_filename_pattern_for_pressure_level(
        self, ecmwf_stub, pressure_level_var_info
    ):
        """Target filename follows <cds_variable>_<cds_dataset>.nc."""
        target = ecmwf_stub.api(pressure_level_var_info)
        assert target.name == "temperature_reanalysis-era5-pressure-levels.nc"

    def test_variable_spec_requires_cds_dataset(self):
        """Variable cannot be built without cds_dataset."""
        catalog_entry = {
            "cds_variable": "2m_temperature",
            "nc_variable": "t2m",
            "units": "C",
            "factors_add": -273.15,
            "factors_mul": 1,
        }
        with pytest.raises(ValueError, match="cds_dataset"):
            Variable.from_dict("2T", catalog_entry)

    def test_variable_spec_requires_cds_variable(self):
        """Variable cannot be built without cds_variable."""
        catalog_entry = {
            "cds_dataset": "reanalysis-era5-single-levels",
            "nc_variable": "t2m",
            "units": "C",
            "factors_add": -273.15,
            "factors_mul": 1,
        }
        with pytest.raises(ValueError, match="cds_variable"):
            Variable.from_dict("2T", catalog_entry)

    def test_licence_not_accepted_is_translated(
        self, ecmwf_stub, single_level_var_info
    ):
        """A 403 'Required licences not accepted' is rewritten with a URL.

        Test scenario:
            cdsapi raises a generic exception whose message contains
            'licence' / 'license' for licence-acceptance failures.
            ``api()`` must translate that into a ``PermissionError``
            naming the dataset's CDS page.
        """
        original = RuntimeError(
            "the request you have submitted is not valid. "
            "Required licences not accepted; please accept the "
            "terms of use on the dataset page."
        )

        def boom(*_args, **_kwargs):
            raise original

        ecmwf_stub.client.retrieve.side_effect = boom
        with pytest.raises(PermissionError) as excinfo:
            ecmwf_stub.api(single_level_var_info)
        message = str(excinfo.value)
        assert "reanalysis-era5-single-levels" in message
        assert "https://cds.climate.copernicus.eu/datasets/" in message
        assert excinfo.value.__cause__ is original

    def test_non_licence_retrieve_errors_propagate_untouched(
        self, ecmwf_stub, single_level_var_info
    ):
        """Non-licence retrieve errors propagate as-is.

        Test scenario:
            A 5xx CDS server error or a transient connection drop
            during ``retrieve()`` must surface unmodified — the
            licence translation only applies when the error message
            actually mentions a licence.
        """
        original = RuntimeError("HTTP 503 Service Unavailable")

        def boom(*_args, **_kwargs):
            raise original

        ecmwf_stub.client.retrieve.side_effect = boom
        with pytest.raises(RuntimeError) as excinfo:
            ecmwf_stub.api(single_level_var_info)
        assert excinfo.value is original


class TestApiMonthly:
    """Tests for :meth:`ECMWF.api` on the monthly path (M5)."""

    @pytest.fixture
    def monthly_var_info(self):
        """Catalog entry with both daily and monthly CDS datasets."""
        return Variable(
            cds_dataset="reanalysis-era5-single-levels",
            cds_dataset_monthly="reanalysis-era5-single-levels-monthly-means",
            cds_variable="2m_temperature",
            nc_variable="t2m",
            units="C",
            factors_add=-273.15,
            factors_mul=1,
        )

    def test_monthly_routes_to_monthly_dataset(
        self, ecmwf_stub, monthly_var_info
    ):
        """Monthly resolution targets ``cds_dataset_monthly``."""
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        dataset_arg = ecmwf_stub.client.retrieve.call_args[0][0]
        assert dataset_arg == "reanalysis-era5-single-levels-monthly-means"

    def test_monthly_falls_back_to_daily_dataset_when_monthly_missing(
        self, ecmwf_stub, monthly_var_info
    ):
        """When ``cds_dataset_monthly`` is absent, fall back to ``cds_dataset``."""
        monthly_var_info = monthly_var_info.model_copy(
            update={"cds_dataset_monthly": None}
        )
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        dataset_arg = ecmwf_stub.client.retrieve.call_args[0][0]
        assert dataset_arg == "reanalysis-era5-single-levels"

    def test_monthly_product_type_is_monthly_averaged_reanalysis(
        self, ecmwf_stub, monthly_var_info
    ):
        """Monthly requests carry ``product_type=monthly_averaged_reanalysis``."""
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        request = captured_request(ecmwf_stub)
        assert request["product_type"] == ["monthly_averaged_reanalysis"]

    def test_monthly_request_uses_single_time_slot(
        self, ecmwf_stub, monthly_var_info
    ):
        """Monthly requests pin time to one 00:00 slot and drop day."""
        ecmwf_stub.temporal_resolution = "monthly"
        ecmwf_stub.api(monthly_var_info)
        request = captured_request(ecmwf_stub)
        assert request["time"] == ["00:00"]
        assert "day" not in request

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
        request = captured_request(ecmwf_stub)
        assert dataset_arg == "reanalysis-era5-single-levels"
        assert request["product_type"] == ["reanalysis"]
        assert request["time"] == ["00:00", "06:00", "12:00", "18:00"]
