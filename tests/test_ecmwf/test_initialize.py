"""Unit tests for :meth:`ECMWF._initialize` and the credentials heuristic.

Covers H3 (the rewritten error message), C2 (only wrap credential-
shaped errors as `AuthenticationError`), and the contract that
otherwise-valid errors propagate untouched.
"""

from __future__ import annotations

import cdsapi
import pytest

from earthlens.ecmwf import ECMWF, AuthenticationError
from tests.test_ecmwf._fakes import _SentinelClient

pytestmark = [pytest.mark.unit]


class TestInitialize:
    """Tests for :meth:`ECMWF._initialize` (H3, C2)."""

    def test_returns_constructed_client_when_credentials_valid(self, monkeypatch):
        """`initialize()` returns whatever `cdsapi.Client()` returns.

        Test scenario:
            With `cdsapi.Client` patched to a stub factory that
            yields a sentinel object, `initialize()` must return the
            very same sentinel — proving it does not double-wrap or
            otherwise transform the client on the happy path.
        """
        sentinel = _SentinelClient()
        monkeypatch.setattr(cdsapi, "Client", lambda: sentinel)
        ecmwf = ECMWF.__new__(ECMWF)
        result = ecmwf._initialize()
        assert result is sentinel

    def test_raises_authentication_error_when_cdsapi_raises(self, monkeypatch):
        """A failing `cdsapi.Client()` is wrapped in AuthenticationError.

        Test scenario:
            When the CDS client constructor raises with a message that
            looks like a credentials problem (cdsapirc / configuration
            keywords), `initialize()` must wrap it in
            :class:`AuthenticationError` whose `__cause__` is the
            original error.
        """
        original = RuntimeError("missing/incomplete configuration file")

        def boom():
            raise original

        monkeypatch.setattr(cdsapi, "Client", boom)
        ecmwf = ECMWF.__new__(ECMWF)
        with pytest.raises(AuthenticationError) as excinfo:
            ecmwf._initialize()
        assert excinfo.value.__cause__ is original

    def test_non_credentials_exception_propagates_untouched(self, monkeypatch):
        """Network / library errors are not branded as auth failures.

        Test scenario:
            C2 narrowed the wrap: when the failure looks unrelated to
            credentials (e.g. an SSL handshake failure during
            `Client()` construction), :meth:`initialize` must
            re-raise the original exception so the user is not told
            to re-create their (working) ~/.cdsapirc.
        """
        original = ConnectionError("TLS handshake failed")
        monkeypatch.setenv("CDSAPI_URL", "https://example.invalid/api")
        monkeypatch.setenv("CDSAPI_KEY", "00000000-0000-0000-0000-000000000000")

        def boom():
            raise original

        monkeypatch.setattr(cdsapi, "Client", boom)
        ecmwf = ECMWF.__new__(ECMWF)
        with pytest.raises(ConnectionError) as excinfo:
            ecmwf._initialize()
        assert excinfo.value is original

    def test_error_message_points_at_cdsapirc(self, monkeypatch):
        """The error message names `~/.cdsapirc` and the setup URL.

        Test scenario:
            The H3 acceptance criterion: a user reading the message
            should know exactly which file to create and where to find
            the official setup guide.
        """

        def boom():
            raise Exception("missing/incomplete configuration file")

        monkeypatch.setattr(cdsapi, "Client", boom)
        ecmwf = ECMWF.__new__(ECMWF)
        with pytest.raises(AuthenticationError) as excinfo:
            ecmwf._initialize()
        message = str(excinfo.value)
        assert "~/.cdsapirc" in message
        assert "cds.climate.copernicus.eu/how-to-api" in message

    def test_error_message_does_not_reference_legacy_env_vars(self, monkeypatch):
        """The error message must not reference the dead env vars.

        Test scenario:
            The pre-H3 message told users to set `ECMWF_API_URL` /
            `ECMWF_API_KEY` / `ECMWF_API_EMAIL` — none of which
            cdsapi reads. Following that advice was a dead end.
        """

        def boom():
            raise Exception("missing/incomplete configuration file")

        monkeypatch.setattr(cdsapi, "Client", boom)
        ecmwf = ECMWF.__new__(ECMWF)
        with pytest.raises(AuthenticationError) as excinfo:
            ecmwf._initialize()
        message = str(excinfo.value)
        for legacy in ("ECMWF_API_URL", "ECMWF_API_KEY", "ECMWF_API_EMAIL"):
            assert legacy not in message
