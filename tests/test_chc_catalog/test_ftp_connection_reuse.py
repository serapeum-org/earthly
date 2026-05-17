"""Lock-in for L5: the sequential CHIRPS batch shares one FTP login across dates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from earthlens.chc import CHIRPS
from earthlens.chc import backend as chc_backend

pytestmark = [pytest.mark.chc]


def _build_chirps(tmp_path: Path) -> CHIRPS:
    """Build a CHIRPS backend pinned to a 5-day window for sharing/reconnect tests."""
    return CHIRPS(
        variables=["precipitation"],
        temporal_resolution="daily",
        start="2020-01-01",
        end="2020-01-05",
        lat_lim=[0.0, 1.0],
        lon_lim=[0.0, 1.0],
        path=tmp_path,
    )


class _ApiSpy:
    """Capture the `ftp` kwarg `_api` receives across every per-date call."""

    def __init__(self):
        self.ftp_args: list = []

    def __call__(self, ds_key, dataset, var, date, ftp=None):  # noqa: D401
        self.ftp_args.append(ftp)
        return None


class TestFtpConnectionReuse:
    """`_download_dataset`'s sequential branch reuses one FTP session across dates (L5)."""

    def test_sequential_batch_passes_same_ftp_to_every_date(
        self, tmp_path: Path, monkeypatch
    ):
        """A 5-date sequential batch sees the same shared FTP instance on each `_api` call."""
        chirps = _build_chirps(tmp_path)
        spy = _ApiSpy()
        monkeypatch.setattr(chirps, "_api", spy)
        # Replace `_open_ftp` so we never touch the real FTP server.
        fake_ftp = MagicMock(name="shared_ftp_session")
        monkeypatch.setattr(chc_backend, "_open_ftp", lambda: fake_ftp)
        monkeypatch.setattr(chc_backend, "_close_ftp_quietly", lambda f: None)
        ds = chirps.catalog.datasets["global-daily"]
        var = ds.variables["precipitation"]
        chirps._download_dataset(
            "global-daily", ds, var, progress_bar=False, cores=None
        )
        assert len(spy.ftp_args) == 5
        # Every per-date call sees the same shared instance, not None.
        assert all(arg is fake_ftp for arg in spy.ftp_args), spy.ftp_args

    def test_parallel_batch_does_not_pass_a_shared_ftp(
        self, tmp_path: Path, monkeypatch
    ):
        """The parallel branch routes through `_api_or_capture`, which calls `_api` WITHOUT `ftp=`."""
        chirps = _build_chirps(tmp_path)
        spy = _ApiSpy()
        monkeypatch.setattr(chirps, "_api", spy)
        # No need to mock `_open_ftp`: the parallel branch never opens
        # a shared session.
        ds = chirps.catalog.datasets["global-daily"]
        var = ds.variables["precipitation"]
        # Parallel branch with one worker so joblib serialisation is real.
        chirps._download_dataset(
            "global-daily", ds, var, progress_bar=False, cores=1
        )
        assert len(spy.ftp_args) == 5
        # Every per-date call sees ftp=None -- workers can't share the
        # unpicklable FTP socket so the parallel branch keeps the old
        # per-file login behaviour.
        assert all(arg is None for arg in spy.ftp_args), spy.ftp_args

    def test_per_date_failure_triggers_ftp_reopen(self, tmp_path: Path, monkeypatch):
        """A per-date exception in the sequential branch causes the FTP session to be reopened."""
        chirps = _build_chirps(tmp_path)
        # Sequence: dates[0] OK, dates[1] raises, dates[2..4] OK.
        # Expect _open_ftp called 2x (original + one reopen after the failure).
        open_calls: list[int] = []

        def _open_counted():
            open_calls.append(len(open_calls))
            return MagicMock(name=f"session_{len(open_calls)}")

        monkeypatch.setattr(chc_backend, "_open_ftp", _open_counted)
        monkeypatch.setattr(chc_backend, "_close_ftp_quietly", lambda f: None)

        calls: list[pd.Timestamp] = []

        def _api(ds_key, dataset, var, date, ftp=None):
            calls.append(date)
            if len(calls) == 2:
                raise RuntimeError("synthetic FTP failure on date #2")
            return None

        monkeypatch.setattr(chirps, "_api", _api)
        ds = chirps.catalog.datasets["global-daily"]
        var = ds.variables["precipitation"]
        chirps._download_dataset(
            "global-daily", ds, var, progress_bar=False, cores=None
        )
        # All 5 dates were attempted (M1 contract).
        assert len(calls) == 5
        # The connection was reopened exactly once (after the failed date).
        # That's 1 initial open + 1 reopen = 2 total `_open_ftp` calls.
        assert len(open_calls) == 2, open_calls

    def test_fetch_ftp_with_provided_session_skips_login(self, monkeypatch, tmp_path: Path):
        """Calling `_fetch_ftp(..., ftp=session)` does NOT open a new FTP connection."""
        session = MagicMock(name="caller_session")
        # If anyone calls `FTP(...)` (the bare constructor) the test fails.
        monkeypatch.setattr(
            chc_backend, "_open_ftp", lambda: pytest.fail("must not open a new FTP")
        )
        # Make `retrbinary` write a placeholder byte so the open(...) actually runs.
        def _fake_retr(cmd, callback):
            callback(b"x")
        session.retrbinary.side_effect = _fake_retr
        local_path = tmp_path / "out.bin"
        CHIRPS._fetch_ftp(
            "some/remote/dir/",
            "file.bin",
            local_path,
            ftp=session,
        )
        session.cwd.assert_called_once_with("some/remote/dir/")
        session.retrbinary.assert_called_once()
        # No login attempt on the caller's session (caller already logged in).
        session.login.assert_not_called()
