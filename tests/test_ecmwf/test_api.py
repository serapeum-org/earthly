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
            "units": "K",
        }
        with pytest.raises(ValueError, match="cds_dataset"):
            Variable.from_dict("2m-temperature", catalog_entry)

    def test_variable_spec_requires_cds_variable(self):
        """Variable cannot be built without cds_variable."""
        catalog_entry = {
            "cds_dataset": "reanalysis-era5-single-levels",
            "nc_variable": "t2m",
            "units": "K",
        }
        with pytest.raises(ValueError, match="cds_variable"):
            Variable.from_dict("2m-temperature", catalog_entry)

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

    def test_extras_forwarded_into_request(self, ecmwf_stub):
        """Variable.extras keys reach ``client.retrieve`` verbatim."""
        spec = Variable(
            cds_dataset="projections-cmip6",
            cds_variable="near_surface_air_temperature",
            nc_variable="tas",
            units="K",
            extras={
                "experiment": "ssp585",
                "model": "ec_earth3",
                "temporal_resolution": "monthly",
            },
        )
        ecmwf_stub.api(spec)
        request = captured_request(ecmwf_stub)
        assert request["experiment"] == "ssp585"
        assert request["model"] == "ec_earth3"
        assert request["temporal_resolution"] == "monthly"

    def test_extras_override_template_defaults(self, ecmwf_stub):
        """A row-level extras key wins over the template default."""
        spec = Variable(
            cds_dataset="reanalysis-era5-single-levels",
            cds_variable="2m_temperature",
            nc_variable="t2m",
            units="K",
            extras={"product_type": ["ensemble_mean"]},
        )
        ecmwf_stub.api(spec)
        assert captured_request(ecmwf_stub)["product_type"] == ["ensemble_mean"]

    def test_invalid_request_caught_before_retrieve(
        self, ecmwf_stub, single_level_var_info, monkeypatch
    ):
        """``api()`` calls ``validate_request`` before ``client.retrieve``.

        Stubs the constraints fetch to return an entry that does NOT
        cover the assembled request, then asserts the resulting
        ``ValueError`` surfaces before any retrieve call.
        """
        from earth2observe.ecmwf import constraints as constraints_module

        monkeypatch.delenv("E2O_SKIP_CONSTRAINTS", raising=False)
        constraints_module._CACHE.clear()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                import json as _j

                return _j.dumps(
                    [
                        {
                            "variable": ["different_variable"],
                            "year": ["1900"],
                        }
                    ]
                ).encode("utf-8")

        monkeypatch.setattr(
            constraints_module.urllib.request,
            "urlopen",
            lambda *_a, **_kw: _Resp(),
        )

        # M17's variable-typo check fires before the full constraints
        # walk, so the error names the unknown variable rather than
        # the constraints-mismatch message.
        with pytest.raises(ValueError, match="unknown variable"):
            ecmwf_stub.api(single_level_var_info)
        assert ecmwf_stub.client.retrieve.call_count == 0

    def test_extras_with_none_value_drops_key_from_request(self, ecmwf_stub):
        """Setting ``extras: {area: None}`` removes ``area`` from the request.

        Per-row opt-out (M27) for datasets that reject the default
        bbox without needing a new ``request_kind``.
        """
        spec = Variable(
            cds_dataset="reanalysis-era5-single-levels",
            cds_variable="2m_temperature",
            nc_variable="t2m",
            units="K",
            extras={"area": None},
        )
        ecmwf_stub.api(spec)
        request = captured_request(ecmwf_stub)
        assert "area" not in request

    def test_extras_with_none_value_drops_arbitrary_key(self, ecmwf_stub):
        """The drop-on-None semantics works for any extras key."""
        spec = Variable(
            cds_dataset="reanalysis-era5-single-levels",
            cds_variable="2m_temperature",
            nc_variable="t2m",
            units="K",
            extras={"product_type": None},
        )
        ecmwf_stub.api(spec)
        request = captured_request(ecmwf_stub)
        assert "product_type" not in request

    def test_oceanic_monthly_strips_day_time_area_product_type(self, ecmwf_stub):
        """``request_kind=oceanic_monthly`` drops ERA5 template defaults.

        ORAS5 rejects ``day`` / ``time`` / ``area`` and uses a
        non-ERA5 ``product_type`` value. The strip happens after the
        extras merge so the row's ``vertical_resolution`` and
        ``product_type`` (consolidated) survive.
        """
        ecmwf_stub.temporal_resolution = "daily"
        spec = Variable(
            cds_dataset="reanalysis-oras5",
            cds_variable="sea_ice_thickness",
            nc_variable="iicethic",
            units="m",
            request_kind="oceanic_monthly",
            extras={
                "product_type": ["consolidated"],
                "vertical_resolution": "single_level",
            },
        )
        ecmwf_stub.api(spec)
        request = captured_request(ecmwf_stub)
        assert "day" not in request
        assert "time" not in request
        assert "area" not in request
        assert request["product_type"] == ["consolidated"]
        assert request["vertical_resolution"] == "single_level"
        assert request["variable"] == ["sea_ice_thickness"]

    def test_carra_means_strips_time(self, ecmwf_stub):
        """``request_kind=carra_means`` drops ``time`` (aggregate window)."""
        spec = Variable(
            cds_dataset="reanalysis-carra-means",
            cds_variable="2m_temperature",
            nc_variable="t2m",
            units="K",
            request_kind="carra_means",
            extras={
                "product_type": ["analysis_based"],
                "domain": "east_domain",
                "time_aggregation": "daily",
            },
        )
        ecmwf_stub.api(spec)
        request = captured_request(ecmwf_stub)
        assert "time" not in request
        assert request["product_type"] == ["analysis_based"]
        assert request["time_aggregation"] == "daily"

    def test_form_request_kind_is_unchanged(
        self, ecmwf_stub, single_level_var_info
    ):
        """The default ``form`` request_kind keeps every template key."""
        ecmwf_stub.api(single_level_var_info)
        request = captured_request(ecmwf_stub)
        for key in ("day", "time", "area", "product_type"):
            assert key in request

    def test_extras_can_re_introduce_a_stripped_key(self, ecmwf_stub):
        """Setting a stripped key in extras keeps it in the request."""
        spec = Variable(
            cds_dataset="reanalysis-oras5",
            cds_variable="sea_surface_temperature",
            nc_variable="sosstsst",
            units="C",
            request_kind="oceanic_monthly",
            extras={
                "area": [60, -10, 50, 5],
                "product_type": ["operational"],
            },
        )
        ecmwf_stub.api(spec)
        request = captured_request(ecmwf_stub)
        assert request["area"] == [60, -10, 50, 5]

    def test_empty_extras_leave_request_unchanged(
        self, ecmwf_stub, single_level_var_info
    ):
        """An empty extras dict does not introduce extra request keys."""
        ecmwf_stub.api(single_level_var_info)
        request = captured_request(ecmwf_stub)
        baseline = {
            "variable",
            "year",
            "month",
            "day",
            "time",
            "data_format",
            "area",
            "product_type",
        }
        assert set(request) == baseline

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
            units="K",
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
