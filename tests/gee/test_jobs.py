"""Tests for `earthlens.gee.jobs` (Phase A: H1, H2, H3)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from earthlens.gee import jobs as jobs_module
from earthlens.gee.jobs import (
    TERMINAL_TASK_STATES,
    TaskInfo,
    _op_to_taskinfo,
    _operation_name,
    _resolve_project,
    cancel_task,
    get_task_status,
    list_recent_tasks,
    resolve_destination,
    wait_for_task_id,
)


# -- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_project(monkeypatch):
    """Pretend `ee.Initialize` happened with `earth-engine-415620`."""
    monkeypatch.setattr(
        jobs_module.ee.data, "_get_projects_path",
        lambda: "projects/earth-engine-415620",
        raising=False,
    )


def _op(
    state: str = "RUNNING",
    *,
    task_id: str = "ID0001",
    task_type: str = "EXPORT_IMAGE",
    description: str = "demo",
    create_iso: str = "2026-05-17T16:36:48.000Z",
    update_iso: str = "2026-05-17T16:36:53.000Z",
    start_iso: str | None = "2026-05-17T16:36:53.000Z",
    attempt: int = 1,
    priority: int | None = 100,
    done: bool = False,
    destination_uris: list[str] | None = None,
    error_message: str | None = None,
) -> dict:
    """Build a fake `ee.data.listOperations()` operation dict."""
    payload = {
        "name": f"projects/earth-engine-415620/operations/{task_id}",
        "metadata": {
            "@type": "type.googleapis.com/google.earthengine.v1alpha.OperationMetadata",
            "state": state,
            "description": description,
            "priority": priority,
            "createTime": create_iso,
            "updateTime": update_iso,
            "startTime": start_iso,
            "type": task_type,
            "attempt": attempt,
        },
    }
    if done:
        payload["done"] = True
        if destination_uris is not None:
            payload["response"] = {"destination_uris": destination_uris}
        if error_message is not None:
            payload["error"] = {"code": 13, "message": error_message}
    return payload


def _flat_status(
    state: str = "READY",
    *,
    task_id: str = "ID0001",
    task_type: str = "EXPORT_IMAGE",
    creation_ms: int = 1779035808064,
    update_ms: int = 1779035808064,
    start_ms: int = 0,
) -> dict:
    """Build a fake `ee.batch.Task.status()` flat dict."""
    return {
        "state": state,
        "description": "demo-flat",
        "priority": 100,
        "creation_timestamp_ms": creation_ms,
        "update_timestamp_ms": update_ms,
        "start_timestamp_ms": start_ms,
        "task_type": task_type,
        "id": task_id,
        "name": f"projects/earth-engine-415620/operations/{task_id}",
    }


# -- _resolve_project / _operation_name -------------------------------------


class TestResolveProject:
    """Tests for the `_resolve_project` / `_operation_name` helpers."""

    def test_explicit_project_passes_through(self):
        assert _resolve_project("my-other-project") == "my-other-project"

    def test_none_reads_initialised_project(self):
        assert _resolve_project(None) == "earth-engine-415620"

    def test_operation_name_from_bare_id(self):
        assert _operation_name("ID42") == "projects/earth-engine-415620/operations/ID42"

    def test_operation_name_passes_full_name_through(self):
        full = "projects/foo/operations/BAR"
        assert _operation_name(full) == full


# -- _op_to_taskinfo --------------------------------------------------------


class TestOpAdapter:
    """Tests for the dual-shape `_op_to_taskinfo` adapter."""

    def test_list_operations_shape_running(self):
        info = _op_to_taskinfo(_op(state="RUNNING"))
        assert info.id == "ID0001"
        assert info.state == "RUNNING"
        assert info.task_type == "EXPORT_IMAGE"
        assert info.create_time == dt.datetime(2026, 5, 17, 16, 36, 48)
        assert info.start_time == dt.datetime(2026, 5, 17, 16, 36, 53)
        assert info.done is False
        assert info.destination_uris == ()
        assert info.error_message is None

    def test_list_operations_shape_completed_carries_destination_uris(self):
        info = _op_to_taskinfo(_op(
            state="COMPLETED", done=True,
            destination_uris=["drive://my_folder/scene_0001.tif"],
        ))
        assert info.state == "COMPLETED"
        assert info.done is True
        assert info.destination_uris == ("drive://my_folder/scene_0001.tif",)
        assert info.error_message is None

    def test_list_operations_shape_failed_carries_error_message(self):
        info = _op_to_taskinfo(_op(
            state="FAILED", done=True, error_message="quota exceeded",
        ))
        assert info.state == "FAILED"
        assert info.error_message == "quota exceeded"
        assert info.destination_uris == ()

    def test_flat_status_shape_with_unstarted_task(self):
        info = _op_to_taskinfo(_flat_status(state="READY", start_ms=0))
        assert info.state == "READY"
        assert info.start_time is None  # `start_timestamp_ms == 0` → None

    def test_flat_status_state_repr_is_normalised(self):
        info = _op_to_taskinfo(_flat_status(state="State.COMPLETED"))
        assert info.state == "COMPLETED"

    def test_unknown_state_rejected(self):
        with pytest.raises(ValueError, match="unknown EE task state 'GARGLE'"):
            _op_to_taskinfo(_op(state="GARGLE"))

    @pytest.mark.parametrize("lro, normalised", [
        ("PENDING", "READY"),
        ("SUCCEEDED", "COMPLETED"),
        ("CANCELLING", "CANCEL_REQUESTED"),
    ])
    def test_lro_state_aliases_are_normalised(self, lro, normalised):
        """The Operations LRO vocabulary folds into the `Task.State` vocabulary."""
        info = _op_to_taskinfo(_op(state=lro))
        assert info.state == normalised

    def test_missing_create_and_update_time_falls_back_to_now(self):
        """Both timestamps missing → create_time defaults to now(); update follows."""
        op = _op()
        op["metadata"]["createTime"] = None
        op["metadata"]["updateTime"] = None
        info = _op_to_taskinfo(op)
        # Both empty → update_time defaults to create_time (which itself
        # falls back to a current naive-UTC `now()`).
        assert info.create_time == info.update_time
        # And the fallback is close to wall-clock now (within a couple seconds).
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        assert abs((now - info.create_time).total_seconds()) < 5


# -- list_recent_tasks ------------------------------------------------------


class TestListRecentTasks:
    """Tests for `list_recent_tasks` and its filters."""

    @pytest.fixture
    def fake_list(self, monkeypatch):
        """Stub `ee.data.listOperations` with a configurable batch."""
        captured: dict = {"calls": []}

        def _make(ops):
            def _stub(project=None):
                captured["calls"].append(project)
                return ops
            monkeypatch.setattr(jobs_module.ee.data, "listOperations", _stub)
            return captured
        return _make

    def test_returns_all_when_no_filter(self, fake_list):
        fake_list([_op(state="RUNNING"), _op(state="COMPLETED", task_id="ID0002")])
        tasks = list_recent_tasks()
        assert {t.id for t in tasks} == {"ID0001", "ID0002"}

    def test_sorted_newest_first(self, fake_list):
        fake_list([
            _op(task_id="A", create_iso="2026-01-01T00:00:00Z"),
            _op(task_id="B", create_iso="2026-05-17T00:00:00Z"),
            _op(task_id="C", create_iso="2026-03-15T00:00:00Z"),
        ])
        ids = [t.id for t in list_recent_tasks()]
        assert ids == ["B", "C", "A"]

    def test_state_filter_string(self, fake_list):
        fake_list([
            _op(state="RUNNING", task_id="R1"),
            _op(state="COMPLETED", task_id="C1"),
        ])
        ids = [t.id for t in list_recent_tasks(state="RUNNING")]
        assert ids == ["R1"]

    def test_state_filter_set(self, fake_list):
        fake_list([
            _op(state="RUNNING", task_id="R1"),
            _op(state="FAILED", task_id="F1", done=True, error_message="oops"),
            _op(state="COMPLETED", task_id="C1", done=True),
        ])
        ids = {t.id for t in list_recent_tasks(state={"FAILED", "COMPLETED"})}
        assert ids == {"F1", "C1"}

    def test_task_type_filter(self, fake_list):
        fake_list([
            _op(task_type="EXPORT_IMAGE", task_id="I1"),
            _op(task_type="EXPORT_TABLE", task_id="T1"),
        ])
        ids = [t.id for t in list_recent_tasks(task_type="EXPORT_TABLE")]
        assert ids == ["T1"]

    def test_description_prefix_filter(self, fake_list):
        fake_list([
            _op(description="USGS_SRTMGL1_003_elevation_20000211", task_id="A"),
            _op(description="COPERNICUS_S2_SR_HARMONIZED_B4_20240601", task_id="B"),
        ])
        ids = [t.id for t in list_recent_tasks(description_prefix="USGS_")]
        assert ids == ["A"]

    def test_max_age_min_filter(self, fake_list, monkeypatch):
        now = dt.datetime(2026, 5, 17, 18, 0, 0)

        class _FixedDatetime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return cls(2026, 5, 17, 18, 0, 0, tzinfo=tz)
                return now

        monkeypatch.setattr(jobs_module.dt, "datetime", _FixedDatetime)
        fake_list([
            _op(task_id="OLD", create_iso="2026-05-17T15:30:00Z"),
            _op(task_id="FRESH", create_iso="2026-05-17T17:55:00Z"),
        ])
        # 1 hour window → only FRESH (created 5 min before "now").
        ids = [t.id for t in list_recent_tasks(max_age_min=60)]
        assert ids == ["FRESH"]

    def test_limit_clips_after_sort(self, fake_list):
        fake_list([
            _op(task_id="A", create_iso="2026-01-01T00:00:00Z"),
            _op(task_id="B", create_iso="2026-05-17T00:00:00Z"),
            _op(task_id="C", create_iso="2026-03-15T00:00:00Z"),
        ])
        ids = [t.id for t in list_recent_tasks(limit=2)]
        assert ids == ["B", "C"]

    def test_unknown_state_rejected(self, fake_list):
        fake_list([])
        with pytest.raises(ValueError, match=r"unknown task state\(s\) \['GARGLE'\]"):
            list_recent_tasks(state="GARGLE")

    def test_uses_resolved_project_when_none(self, fake_list):
        captured = fake_list([])
        list_recent_tasks()
        assert captured["calls"] == ["projects/earth-engine-415620"]

    def test_uses_explicit_project_when_given(self, fake_list):
        captured = fake_list([])
        list_recent_tasks(project="other-project")
        assert captured["calls"] == ["projects/other-project"]


# -- get_task_status / cancel_task ------------------------------------------


class TestGetTaskStatus:
    """Tests for `get_task_status`."""

    def test_calls_get_operation_with_canonical_name(self, monkeypatch):
        captured = {}

        def _stub(op_name):
            captured["op_name"] = op_name
            return _op(state="COMPLETED", done=True, destination_uris=["drive://x/y.tif"])

        monkeypatch.setattr(jobs_module.ee.data, "getOperation", _stub)
        info = get_task_status("ID0001")
        assert captured["op_name"] == "projects/earth-engine-415620/operations/ID0001"
        assert info.state == "COMPLETED"
        assert info.destination_uris == ("drive://x/y.tif",)

    def test_accepts_full_operation_name(self, monkeypatch):
        captured = {}

        def _stub(op_name):
            captured["op_name"] = op_name
            return _op(state="RUNNING")

        monkeypatch.setattr(jobs_module.ee.data, "getOperation", _stub)
        get_task_status("projects/foo/operations/BAR")
        assert captured["op_name"] == "projects/foo/operations/BAR"


class TestCancelTask:
    """Tests for `cancel_task`."""

    def test_calls_cancel_operation_with_canonical_name(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            jobs_module.ee.data, "cancelOperation",
            lambda n: captured.setdefault("op_name", n),
        )
        cancel_task("ID0042", project="my-project")
        assert captured["op_name"] == "projects/my-project/operations/ID0042"


# -- wait_for_task_id -------------------------------------------------------


class TestWaitForTaskId:
    """Tests for `wait_for_task_id`."""

    def test_returns_when_already_completed(self, monkeypatch):
        monkeypatch.setattr(
            jobs_module.ee.data, "getOperation",
            lambda n: _op(state="COMPLETED", done=True, destination_uris=["drive://x.tif"]),
        )
        out = wait_for_task_id("ID0001", progress_bar=False, sleep=lambda s: None)
        assert out.state == "COMPLETED"

    def test_polls_then_completes(self, monkeypatch):
        states = iter(["READY", "RUNNING", "COMPLETED"])
        slept: list[float] = []
        monkeypatch.setattr(
            jobs_module.ee.data, "getOperation",
            lambda n: _op(state=next(states), done=False),
        )
        # Polling stops on COMPLETED; the third call's `done=False` doesn't
        # matter — the state-based check is what terminates the loop.
        wait_for_task_id("ID0001", poll_seconds=2, progress_bar=False, sleep=slept.append)
        assert slept == [2, 2]  # two sleeps between three polls

    def test_failed_task_raises_with_error_message(self, monkeypatch):
        monkeypatch.setattr(
            jobs_module.ee.data, "getOperation",
            lambda n: _op(state="FAILED", done=True, error_message="quota exceeded"),
        )
        with pytest.raises(RuntimeError, match="ended FAILED: quota exceeded"):
            wait_for_task_id("ID0001", progress_bar=False, sleep=lambda s: None)

    def test_cancelled_task_raises(self, monkeypatch):
        monkeypatch.setattr(
            jobs_module.ee.data, "getOperation",
            lambda n: _op(state="CANCELLED", done=True),
        )
        with pytest.raises(RuntimeError, match="ended CANCELLED"):
            wait_for_task_id("ID0001", progress_bar=False, sleep=lambda s: None)


# -- resolve_destination ----------------------------------------------------


class TestResolveDestination:
    """Tests for `resolve_destination`."""

    def _completed_info(self, uris: list[str]) -> TaskInfo:
        return _op_to_taskinfo(_op(
            state="COMPLETED", done=True, destination_uris=uris,
        ))

    def test_returns_destination_uris_for_completed_task(self):
        info = self._completed_info(["drive://my_folder/scene.tif"])
        assert resolve_destination(info) == ["drive://my_folder/scene.tif"]

    def test_returns_empty_for_non_completed_task(self):
        info = _op_to_taskinfo(_op(state="RUNNING"))
        assert resolve_destination(info) == []

    @pytest.mark.parametrize("uri", [
        "drive://my_folder/scene.tif",
        "gs://my-bucket/scene.tif",
        "projects/my-project/assets/my-folder/scene",
    ])
    def test_returns_each_sink_destination_verbatim(self, uri):
        info = self._completed_info([uri])
        assert resolve_destination(info) == [uri]

    def test_download_to_recognises_gs_uri_and_calls_pull_gcs(
        self, monkeypatch, tmp_path,
    ):
        """A `gs://bucket/key` URI dispatches to `_pull_gcs(bucket, key, dest_dir)`."""
        captured: dict = {}

        def _stub(bucket, key, dest_dir):
            captured.update({"bucket": bucket, "key": key, "dest_dir": dest_dir})
            return dest_dir / Path(key).name

        monkeypatch.setattr(jobs_module, "_pull_gcs", _stub)
        info = self._completed_info(["gs://my-bucket/subdir/scene.tif"])
        out = resolve_destination(info, download_to=tmp_path)
        assert captured == {
            "bucket": "my-bucket",
            "key": "subdir/scene.tif",
            "dest_dir": tmp_path,
        }
        assert out == [tmp_path / "scene.tif"]

    def test_download_to_recognises_https_storage_url(self, monkeypatch, tmp_path):
        """`https://storage.googleapis.com/<bucket>/<key>` also routes to GCS."""
        captured: dict = {}

        def _stub(bucket, key, dest_dir):
            captured.update({"bucket": bucket, "key": key})
            return dest_dir / "scene.tif"

        monkeypatch.setattr(jobs_module, "_pull_gcs", _stub)
        info = self._completed_info(
            ["https://storage.googleapis.com/my-bucket/subdir/scene.tif"]
        )
        resolve_destination(info, download_to=tmp_path)
        assert captured == {"bucket": "my-bucket", "key": "subdir/scene.tif"}

    def test_download_to_recognises_drive_file_url(self, monkeypatch, tmp_path):
        """`https://drive.google.com/file/d/<id>/view` dispatches to `_pull_drive_file`."""
        captured: dict = {}

        def _stub(file_id, dest_dir):
            captured["file_id"] = file_id
            return dest_dir / "scene.tif"

        monkeypatch.setattr(jobs_module, "_pull_drive_file", _stub)
        info = self._completed_info([
            "https://drive.google.com/file/d/1abcDEF_xYz/view?usp=drive_web"
        ])
        resolve_destination(info, download_to=tmp_path)
        assert captured == {"file_id": "1abcDEF_xYz"}

    def test_download_to_passes_drive_folder_url_through_with_warning(
        self, tmp_path,
    ):
        """Drive folder URLs are surfaced verbatim (no list+download)."""
        info = self._completed_info([
            "https://drive.google.com/#folders/1zzzFolderId"
        ])
        out = resolve_destination(info, download_to=tmp_path)
        assert out == ["https://drive.google.com/#folders/1zzzFolderId"]

    def test_download_to_passes_ee_asset_path_through(self, tmp_path):
        """An EE-asset destination stays on EE — surfaced verbatim."""
        info = self._completed_info(["projects/p/assets/folder/scene"])
        out = resolve_destination(info, download_to=tmp_path)
        assert out == ["projects/p/assets/folder/scene"]

    def test_download_to_creates_target_dir(self, monkeypatch, tmp_path):
        """`download_to` is created if missing."""
        monkeypatch.setattr(jobs_module, "_pull_gcs", lambda b, k, d: d / "x.tif")
        info = self._completed_info(["gs://b/x.tif"])
        target = tmp_path / "newly-made"
        resolve_destination(info, download_to=target)
        assert target.is_dir()

    def test_unrecognised_uri_surfaced_with_warning(self, tmp_path):
        """An unrecognised URI shape is surfaced verbatim without download."""
        info = self._completed_info(["s3://other-cloud/scene.tif"])
        out = resolve_destination(info, download_to=tmp_path)
        assert out == ["s3://other-cloud/scene.tif"]


# -- terminal-state constant ------------------------------------------------


class TestTerminalStates:
    """Anchor test for the exported terminal-state set."""

    def test_terminal_states_are_the_documented_four(self):
        assert TERMINAL_TASK_STATES == frozenset(
            {"COMPLETED", "FAILED", "CANCELLED", "CANCEL_REQUESTED"}
        )


# -- M2: Catalog convenience methods ----------------------------------------


class TestCatalogShortcuts:
    """Tests for `Catalog.list_recent_tasks` / `Catalog.get_task_status` (M2)."""

    def test_catalog_list_recent_tasks_delegates(self, monkeypatch):
        """`Catalog.list_recent_tasks(**kw)` forwards verbatim to the jobs module."""
        from earthlens.gee import Catalog
        captured: dict = {}

        def _stub(**kw):
            captured.update(kw)
            return ["sentinel"]

        monkeypatch.setattr(jobs_module, "list_recent_tasks", _stub)
        cat = Catalog.model_construct(
            available_datasets=[], datasets={}, providers={},
        )
        result = cat.list_recent_tasks(state="RUNNING", max_age_min=120)
        assert result == ["sentinel"]
        assert captured == {"state": "RUNNING", "max_age_min": 120}

    def test_catalog_get_task_status_delegates(self, monkeypatch):
        """`Catalog.get_task_status(id, **kw)` forwards verbatim."""
        from earthlens.gee import Catalog
        captured: dict = {}

        def _stub(task_id, **kw):
            captured["task_id"] = task_id
            captured.update(kw)
            return "sentinel"

        monkeypatch.setattr(jobs_module, "get_task_status", _stub)
        cat = Catalog.model_construct(
            available_datasets=[], datasets={}, providers={},
        )
        result = cat.get_task_status("ID0001", project="my-project")
        assert result == "sentinel"
        assert captured == {"task_id": "ID0001", "project": "my-project"}

    def test_audit_recent_tasks_groups_by_state(self, monkeypatch):
        """`Catalog.audit_recent_tasks()` groups list_recent_tasks output by state (L3)."""
        from earthlens.gee import Catalog

        def _stub(max_age_min=None, **kw):
            assert max_age_min == 7 * 24 * 60
            return [
                _op_to_taskinfo(_op(state="RUNNING", task_id="R1")),
                _op_to_taskinfo(_op(state="COMPLETED", task_id="C1", done=True)),
                _op_to_taskinfo(_op(state="FAILED", task_id="F1", done=True,
                                    error_message="oops")),
                _op_to_taskinfo(_op(state="COMPLETED", task_id="C2", done=True)),
            ]

        monkeypatch.setattr(jobs_module, "list_recent_tasks", _stub)
        cat = Catalog.model_construct(available_datasets=[], datasets={}, providers={})
        report = cat.audit_recent_tasks()
        assert set(report) == {"RUNNING", "COMPLETED", "FAILED"}
        assert [t.id for t in report["COMPLETED"]] == ["C1", "C2"]
        assert report["FAILED"][0].error_message == "oops"

    def test_audit_recent_tasks_rejects_state_kwarg(self, monkeypatch):
        """`state=` would silently narrow the report — reject it explicitly."""
        from earthlens.gee import Catalog
        monkeypatch.setattr(jobs_module, "list_recent_tasks", lambda **kw: [])
        cat = Catalog.model_construct(available_datasets=[], datasets={}, providers={})
        with pytest.raises(ValueError, match="don't pass `state=`"):
            cat.audit_recent_tasks(state="RUNNING")

    def test_audit_recent_tasks_empty_window_returns_empty_dict(self, monkeypatch):
        """No matching tasks → empty dict."""
        from earthlens.gee import Catalog
        monkeypatch.setattr(jobs_module, "list_recent_tasks", lambda **kw: [])
        cat = Catalog.model_construct(available_datasets=[], datasets={}, providers={})
        assert cat.audit_recent_tasks() == {}


# -- M4: CLI parsing --------------------------------------------------------


class TestCli:
    """Tests for the `python -m earthlens.gee.jobs` argparse surface."""

    def test_list_command_invokes_list_recent_tasks(self, monkeypatch, capsys):
        captured: dict = {"calls": 0}

        def _stub_list(**kw):
            captured["calls"] += 1
            captured.update(kw)
            return [_op_to_taskinfo(_op(state="RUNNING", task_id="LCMD1"))]

        monkeypatch.setattr(jobs_module, "list_recent_tasks", _stub_list)
        monkeypatch.setattr(jobs_module, "_maybe_initialize_ee", lambda: None)
        rc = jobs_module.main([
            "list", "--state", "RUNNING", "--max-age-min", "30",
        ])
        out = capsys.readouterr().out
        assert rc == 0
        assert "LCMD1" in out
        assert captured["state"] == "RUNNING"
        assert captured["max_age_min"] == 30

    def test_list_command_prints_no_match_marker_when_empty(self, monkeypatch, capsys):
        monkeypatch.setattr(jobs_module, "list_recent_tasks", lambda **kw: [])
        monkeypatch.setattr(jobs_module, "_maybe_initialize_ee", lambda: None)
        rc = jobs_module.main(["list", "--state", "RUNNING"])
        assert rc == 0
        assert "(no tasks match)" in capsys.readouterr().out

    def test_status_command_dumps_pydantic_json(self, monkeypatch, capsys):
        info = _op_to_taskinfo(_op(state="COMPLETED", done=True,
                                   destination_uris=["drive://x.tif"]))
        monkeypatch.setattr(jobs_module, "get_task_status", lambda *a, **k: info)
        monkeypatch.setattr(jobs_module, "_maybe_initialize_ee", lambda: None)
        rc = jobs_module.main(["status", "ID0001"])
        out = capsys.readouterr().out
        assert rc == 0
        assert '"state": "COMPLETED"' in out
        assert "drive://x.tif" in out

    def test_cancel_command_calls_cancel_task(self, monkeypatch, capsys):
        captured: dict = {}
        monkeypatch.setattr(
            jobs_module, "cancel_task",
            lambda task_id, **kw: captured.update({"task_id": task_id, **kw}),
        )
        monkeypatch.setattr(jobs_module, "_maybe_initialize_ee", lambda: None)
        rc = jobs_module.main(["cancel", "ID0001"])
        assert rc == 0
        assert captured == {"task_id": "ID0001", "project": None}
        assert "cancel requested for ID0001" in capsys.readouterr().out

    def test_wait_command_invokes_wait_for_task_id(self, monkeypatch, capsys):
        info = _op_to_taskinfo(_op(state="COMPLETED", done=True,
                                   destination_uris=["drive://x.tif"]))
        monkeypatch.setattr(jobs_module, "wait_for_task_id", lambda *a, **k: info)
        monkeypatch.setattr(jobs_module, "_maybe_initialize_ee", lambda: None)
        rc = jobs_module.main(["wait", "ID0001", "--no-progress-bar"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "COMPLETED" in out
        assert "drive://x.tif" in out
