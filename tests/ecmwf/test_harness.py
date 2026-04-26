"""Meta-tests for the ECMWF test harness itself.

Pins two cross-cutting invariants of the test infrastructure:

* The autouse ``_block_real_cdsapi`` safeguard fires with a clear,
  copy-pasteable message when a test forgets to mock ``cdsapi.Client``.
* The production ``ecmwf.py`` parses under the Python 3.11 grammar
  (no PEP-701 nested f-string quotes) regardless of the running
  interpreter version.
"""

from __future__ import annotations

import cdsapi
import pytest

pytestmark = [pytest.mark.unit]


class TestMockHarnessSafeguard:
    """Tests for the autouse safeguard installed by ``_block_real_cdsapi``."""

    def test_safeguard_message_includes_literal_patch_pattern(self):
        """The safeguard error spells out the exact monkeypatch call.

        Test scenario:
            Pre-N3, the message said "see M4 harness" — readers had
            to chase the docstring elsewhere. The new message inlines
            the literal `monkeypatch.setattr(cdsapi, "Client", lambda: ...)`
            so a developer can copy-paste it straight into a failing
            test. Trip the safeguard deliberately and assert the
            string is present in the error.
        """
        with pytest.raises(AssertionError) as excinfo:
            cdsapi.Client()
        message = str(excinfo.value)
        assert 'monkeypatch.setattr(cdsapi, "Client"' in message
        assert "RUN_CDS_E2E=1" in message


class TestSourceCompiles:
    """Compile-time checks for Python 3.11 compatibility (C2)."""

    def test_ecmwf_module_compiles_under_311_grammar(self):
        """``ecmwf.py`` parses without PEP-701-only constructs.

        Test scenario:
            ``pyproject.toml`` declares Python 3.11 as the minimum
            supported version. PEP 701 (which allows reusing the
            outer quote inside an f-string) only landed in 3.12, so
            a line like ``f"...{d["k"]}..."`` is a syntax error on
            3.11. ``ast.parse(..., feature_version=(3, 11))``
            exercises the 3.11 grammar regardless of the running
            interpreter, so this regression test catches the issue
            even when the test suite executes on a newer Python.
        """
        import ast
        import inspect

        from earth2observe import ecmwf as ecmwf_module

        source = inspect.getsource(ecmwf_module)
        ast.parse(source, feature_version=(3, 11))
