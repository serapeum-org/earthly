"""Tests for `earthlens.gee.auth` — service-account authentication.

`ee.ServiceAccountCredentials` and `ee.Initialize` are stubbed via ``monkeypatch``
so no network or real credentials are touched; the real `ee.EEException` class is
kept so the backend's `except ee.EEException` branches are exercised faithfully.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock

import ee
import pytest

from earthlens.gee import auth as auth_module
from earthlens.gee.auth import AuthenticationError, EarthEngineAuth, _load_key_dict


def _key_text(**extra) -> str:
    """Return a JSON service-account key string with the given extra fields."""
    return json.dumps({"type": "service_account", "client_email": "sa@x.iam", **extra})


@pytest.fixture(scope="function")
def key_file(tmp_path):
    """Write a minimal service-account key file (with a project_id) and return its path."""
    path = tmp_path / "key.json"
    path.write_text(_key_text(project_id="demo-project"))
    return str(path)


@pytest.fixture(scope="function")
def stub_ee(monkeypatch):
    """Stub `ee.ServiceAccountCredentials` and `ee.Initialize`; keep `ee.EEException` real.

    Returns:
        tuple: ``(credentials_stub, initialize_stub)`` — both ``MagicMock``s.
    """
    creds = MagicMock(name="ServiceAccountCredentials")
    init = MagicMock(name="Initialize")
    monkeypatch.setattr(auth_module.ee, "ServiceAccountCredentials", creds)
    monkeypatch.setattr(auth_module.ee, "Initialize", init)
    return creds, init


class TestLoadKeyDict:
    """Tests for the module-private `_load_key_dict` helper."""

    def test_reads_path(self, key_file):
        """A path to a real key file is parsed to a dict."""
        result = _load_key_dict(key_file)
        assert result["project_id"] == "demo-project", f"unexpected: {result}"

    def test_reads_raw_json(self):
        """Raw JSON content is parsed to a dict."""
        result = _load_key_dict(_key_text(project_id="p"))
        assert result["project_id"] == "p"

    def test_garbage_returns_none(self):
        """A non-path, non-JSON string yields `None`."""
        assert _load_key_dict("not json and not a file") is None

    def test_non_object_json_returns_none(self):
        """Valid JSON that doesn't start with `{` is treated as a (missing) path → None."""
        assert _load_key_dict("[1, 2]") is None


class TestAuthenticationError:
    """Tests for the :class:`AuthenticationError` exception type."""

    def test_is_exception_subclass(self):
        """`AuthenticationError` is a plain `Exception` subclass."""
        assert issubclass(AuthenticationError, Exception)
        assert str(AuthenticationError("boom")) == "boom"


class TestEarthEngineAuthEncodeDecode:
    """Tests for ``encode_service_account`` / ``decode_service_account``."""

    def test_round_trip(self, key_file):
        """Encoding then decoding a key file yields the original mapping."""
        blob = EarthEngineAuth.encode_service_account(key_file)
        assert isinstance(blob, bytes)
        decoded = EarthEngineAuth.decode_service_account(blob)
        assert decoded == {"type": "service_account", "client_email": "sa@x.iam",
                           "project_id": "demo-project"}

    def test_decode_independent_of_encode(self):
        """`decode_service_account` works on any base64'd JSON object."""
        blob = base64.b64encode(json.dumps({"a": 1}).encode())
        assert EarthEngineAuth.decode_service_account(blob) == {"a": 1}


class TestEarthEngineAuthInitialize:
    """Tests for ``EarthEngineAuth.initialize`` and the constructor."""

    def test_no_project_raises(self):
        """A key with no `project_id` and no `project` arg fails fast."""
        with pytest.raises(AuthenticationError, match="no Earth Engine Cloud project"):
            EarthEngineAuth.initialize("sa@x.iam", _key_text())

    def test_project_from_key_file(self, key_file, stub_ee):
        """The project is read from the key file's `project_id`."""
        creds, init = stub_ee
        project = EarthEngineAuth.initialize("sa@x.iam", key_file)
        assert project == "demo-project"
        init.assert_called_once()
        assert init.call_args.kwargs["project"] == "demo-project"
        creds.assert_called_once_with("sa@x.iam", key_file)

    def test_explicit_project_overrides_key_file(self, key_file, stub_ee):
        """An explicit `project` argument wins over the key file's `project_id`."""
        _, init = stub_ee
        project = EarthEngineAuth.initialize("sa@x.iam", key_file, project="override")
        assert project == "override"
        assert init.call_args.kwargs["project"] == "override"

    def test_credentials_fallback_to_key_data(self, monkeypatch):
        """A `ValueError` from the path form falls back to the `key_data=` form."""
        monkeypatch.setattr(auth_module.ee, "Initialize", MagicMock())
        creds = MagicMock(side_effect=[ValueError("not a file"), MagicMock()])
        monkeypatch.setattr(auth_module.ee, "ServiceAccountCredentials", creds)
        project = EarthEngineAuth.initialize("sa@x.iam", _key_text(project_id="p"))
        assert project == "p"
        assert creds.call_count == 2
        assert "key_data" in creds.call_args.kwargs

    def test_both_credential_attempts_fail_raises(self, monkeypatch):
        """If both credential constructions fail, an `AuthenticationError` is raised."""
        creds = MagicMock(side_effect=[ValueError("nope"), RuntimeError("still nope")])
        monkeypatch.setattr(auth_module.ee, "ServiceAccountCredentials", creds)
        with pytest.raises(AuthenticationError, match="could not build service-account credentials"):
            EarthEngineAuth.initialize("sa@x.iam", _key_text(project_id="p"))

    def test_not_registered_project_raises_friendly(self, monkeypatch):
        """An "EE not registered" error becomes a registration-pointing AuthenticationError."""
        monkeypatch.setattr(auth_module.ee, "ServiceAccountCredentials", MagicMock())
        monkeypatch.setattr(
            auth_module.ee, "Initialize",
            MagicMock(side_effect=ee.EEException("Project p is not registered to use Earth Engine")),
        )
        with pytest.raises(AuthenticationError, match="not registered to use Earth Engine"):
            EarthEngineAuth.initialize("sa@x.iam", _key_text(project_id="p"))

    def test_permission_error_raises_friendly(self, monkeypatch):
        """A serviceUsage permission error becomes an IAM-role-pointing AuthenticationError."""
        monkeypatch.setattr(auth_module.ee, "ServiceAccountCredentials", MagicMock())
        monkeypatch.setattr(
            auth_module.ee, "Initialize",
            MagicMock(side_effect=ee.EEException(
                "Caller does not have required permission ... serviceUsageConsumer")),
        )
        with pytest.raises(AuthenticationError, match="serviceUsageConsumer|earthengine.viewer"):
            EarthEngineAuth.initialize("sa@x.iam", _key_text(project_id="p"))

    def test_other_ee_exception_wrapped(self, monkeypatch):
        """Any other `ee.EEException` is wrapped as an initialisation failure."""
        monkeypatch.setattr(auth_module.ee, "ServiceAccountCredentials", MagicMock())
        monkeypatch.setattr(auth_module.ee, "Initialize", MagicMock(side_effect=ee.EEException("weird")))
        with pytest.raises(AuthenticationError, match="initialisation failed"):
            EarthEngineAuth.initialize("sa@x.iam", _key_text(project_id="p"))

    def test_generic_exception_wrapped(self, monkeypatch):
        """A non-`EEException` from `ee.Initialize` is also wrapped."""
        monkeypatch.setattr(auth_module.ee, "ServiceAccountCredentials", MagicMock())
        monkeypatch.setattr(auth_module.ee, "Initialize", MagicMock(side_effect=OSError("disk")))
        with pytest.raises(AuthenticationError, match="initialisation failed"):
            EarthEngineAuth.initialize("sa@x.iam", _key_text(project_id="p"))

    def test_constructor_sets_attributes(self, key_file, stub_ee):
        """The constructor stores the account and the resolved project."""
        auth = EarthEngineAuth("sa@x.iam", key_file)
        assert auth.service_account == "sa@x.iam"
        assert auth.project == "demo-project"


def test_module_exposes_expected_symbols():
    """The auth module exposes the documented public symbols."""
    assert hasattr(auth_module, "EarthEngineAuth")
    assert hasattr(auth_module, "AuthenticationError")
