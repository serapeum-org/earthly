"""Tests for `earthlens.gee.auth` (service-account authentication).

Full coverage is added in task H8 via `/generate-full-test-suite`; this
module is the placeholder so the import path exists.
"""

from __future__ import annotations

from earthlens.gee.auth import AuthenticationError, EarthEngineAuth


def test_auth_symbols_importable() -> None:
    """The public auth symbols import without touching Earth Engine."""
    assert issubclass(AuthenticationError, Exception)
    assert hasattr(EarthEngineAuth, "initialize")
    assert hasattr(EarthEngineAuth, "encode_service_account")
    assert hasattr(EarthEngineAuth, "decode_service_account")
