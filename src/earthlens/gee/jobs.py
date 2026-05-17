"""Track Earth Engine batch tasks the way `earthlens.ecmwf.jobs` tracks CDS jobs.

The synchronous `export_via="url"` path of :class:`earthlens.gee.GEE`
produces nothing trackable — once the GeoTIFF arrives there is no
queue entry to inspect. The asynchronous sinks (`"drive"` / `"gcs"` /
`"asset"`) do, and Earth Engine exposes two stacked APIs over them
(`ee.batch.Task.*` and the lower-level `ee.data.{listOperations,
getOperation, cancelOperation}`). This module normalises both shapes
into a single frozen :class:`TaskInfo` value object and gives the
caller four operations on top:

* :func:`list_recent_tasks` — every batch task on the current project,
  with client-side filters by `state` / age / `task_type` /
  description prefix.
* :func:`get_task_status` — one task by id.
* :func:`cancel_task` — cancel a queued or running task by id.
* :func:`wait_for_task_id` — id-based blocking poll until the task
  reaches a terminal state.
* :func:`resolve_destination` — return the `destination_uris` of a
  completed task as a list (does *not* auto-download Drive / GCS
  payloads — that's `L2` in `planning/gee-jobs-tracking-plan.md`).

These mirror :mod:`earthlens.ecmwf.jobs` so a user juggling both
backends sees the same surface.

Examples:
    - Show every batch export still running:
        ```python
        >>> from earthlens.gee.jobs import list_recent_tasks  # doctest: +SKIP
        >>> for t in list_recent_tasks(state="RUNNING"):  # doctest: +SKIP
        ...     print(t.id, t.description)

        ```
    - Cancel a runaway task by id:
        ```python
        >>> from earthlens.gee.jobs import cancel_task  # doctest: +SKIP
        >>> cancel_task("SNR3SZ6GNH5LUJQJ52VVNSZT")  # doctest: +SKIP

        ```
"""

from __future__ import annotations

import datetime as dt
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

import ee
from loguru import logger
from pydantic import BaseModel, ConfigDict
from tqdm import tqdm

#: Terminal task states — `wait_for_task_id` returns / raises when one is reached.
#:
#: `CANCEL_REQUESTED` is intentionally **not** terminal: it's a
#: transient state (the cancel has been accepted, but the worker
#: hasn't fully torn down yet). Treating it as terminal would
#: short-circuit `wait_for_task_id` before the real `CANCELLED`
#: arrives, leaving callers unable to tell "cancel completed"
#: apart from "cancel still in flight".
TERMINAL_TASK_STATES: frozenset[str] = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED"}
)

#: Every state earthlens may report on a normalised `TaskInfo`. Earth
#: Engine has two overlapping vocabularies: the high-level
#: `ee.batch.Task.State` (`UNSUBMITTED` / `READY` / `RUNNING` /
#: `CANCEL_REQUESTED` / `CANCELLED` / `COMPLETED` / `FAILED`) and the
#: underlying Google Long-Running-Operations names that
#: `ee.data.listOperations()` returns (`PENDING` for queued,
#: `SUCCEEDED` for completed). The adapter folds the LRO names into
#: their `Task.State` equivalents via :data:`_STATE_ALIASES` so
#: downstream code sees the single :data:`_VALID_TASK_STATES`
#: vocabulary.
_VALID_TASK_STATES: frozenset[str] = frozenset(
    {
        "UNSUBMITTED",
        "READY",
        "RUNNING",
        "CANCEL_REQUESTED",
        "CANCELLED",
        "COMPLETED",
        "FAILED",
    }
)

#: LRO -> Task.State aliases applied at adapter time. The high-level
#: `ee.batch.Task.State` and the underlying Google Long-Running-Operations
#: vocabulary overlap but use different terms for the same states.
_STATE_ALIASES: dict[str, str] = {
    "PENDING": "READY",                  # queued, awaiting a worker
    "SUCCEEDED": "COMPLETED",
    "CANCELLING": "CANCEL_REQUESTED",
}

TaskState = Literal[
    "UNSUBMITTED",
    "READY",
    "RUNNING",
    "CANCEL_REQUESTED",
    "CANCELLED",
    "COMPLETED",
    "FAILED",
]


class TaskInfo(BaseModel):
    """Normalised view of a single Earth Engine batch task.

    Built from either an `ee.data.listOperations()` operation dict
    (nested `metadata`) or an `ee.batch.Task.status()` flat dict; both
    map to the same frozen model so downstream code never has to
    care which API it came from.

    Attributes:
        id: The bare task id (e.g. `"SNR3SZ6GNH5LUJQJ52VVNSZT"`) —
            the trailing token of `operation_name`.
        operation_name: The canonical
            `projects/<proj>/operations/<id>` path used by
            `ee.data.getOperation` and `ee.data.cancelOperation`.
        description: Human description set when the task was
            submitted (e.g. the `description=` kwarg of
            `ee.batch.Export.image.toDrive`).
        state: One of :data:`_VALID_TASK_STATES`.
        task_type: `EXPORT_IMAGE` / `EXPORT_TABLE` / `EXPORT_MAP` /
            `EXPORT_VIDEO` / `EXPORT_CLASSIFIER`.
        create_time: Submission time as a naive UTC datetime.
        update_time: Last status-update time as a naive UTC datetime.
        start_time: When the task moved out of `READY` into `RUNNING`,
            or `None` if it hasn't.
        attempt: 1 on first try, increments on EE-side retries.
        priority: Task priority (EE assigns 100 by default).
        destination_uris: Output URLs (Drive / GCS / asset) — only
            populated on `COMPLETED`.
        error_message: EE-side error string — only populated on
            `FAILED`.
        done: `True` on any terminal state.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    operation_name: str
    description: str
    state: TaskState
    task_type: str
    create_time: dt.datetime
    update_time: dt.datetime
    start_time: dt.datetime | None = None
    attempt: int = 1
    priority: int | None = None
    destination_uris: tuple[str, ...] = ()
    error_message: str | None = None
    done: bool = False


# -- low-level helpers ------------------------------------------------------


def _resolve_project(project: str | None) -> str:
    """Return `project` or fall back to the project the SDK was initialised with.

    Reads from `ee.data._get_projects_path()` — a **private** EE SDK
    helper. The leading underscore is the EE devs reserving the right
    to rename or remove it; if they do, we fall back to
    `ee.data._cloud_api_user_project` (also private but the actual
    state behind the helper) and finally raise with a clear remediation
    rather than letting an `AttributeError` bubble out of every call
    site. Callers that want to bypass this resolution can always pass
    `project=` explicitly.
    """
    if project is not None:
        return project

    raw: str | None = None
    # Primary: the documented-but-private accessor used by the EE SDK
    # itself in `ee.data.listOperations` etc.
    getter = getattr(ee.data, "_get_projects_path", None)
    if getter is not None:
        try:
            raw = getter()
        except Exception:  # noqa: BLE001 - any failure → try the fallback
            raw = None

    # Fallback: read the underlying module-level state. As of
    # `earthengine-api` 1.x this is the variable `_get_projects_path`
    # itself returns after wrapping.
    if not raw:
        raw = getattr(ee.data, "_cloud_api_user_project", None)

    if not raw:
        raise RuntimeError(
            "Could not resolve the current EE project. Pass an explicit "
            "`project=` argument, or call `ee.Initialize(project=...)` "
            "before invoking the jobs API. (Internally we rely on "
            "`ee.data._get_projects_path()` / `_cloud_api_user_project`; "
            "if you're on a recent `earthengine-api` release that "
            "renamed those, please open an issue.)"
        )

    if raw.startswith("projects/"):
        return raw[len("projects/"):]
    return raw


def _operation_name(task_id: str, project: str | None = None) -> str:
    """Build the canonical `projects/<proj>/operations/<id>` operation name.

    Accepts either a bare task id or an already-formed operation name;
    the latter is returned unchanged.
    """
    if task_id.startswith("projects/") and "/operations/" in task_id:
        return task_id
    return f"projects/{_resolve_project(project)}/operations/{task_id}"


def _parse_iso(ts: str | None) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp from `listOperations` as a naive UTC datetime."""
    if not ts:
        return None
    # ISO-8601 with `Z` (Zulu). `fromisoformat` handles `+00:00` natively
    # from Python 3.11 onwards but not the bare `Z` suffix.
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(
        dt.timezone.utc
    ).replace(tzinfo=None)


def _parse_ms(ms: int | float | None) -> dt.datetime | None:
    """Parse a ms-since-epoch int from `task.status()` as a naive UTC datetime.

    `task.status()` reports `start_timestamp_ms == 0` when the task
    hasn't started yet — treat that as `None`.
    """
    if not ms:
        return None
    return dt.datetime.fromtimestamp(int(ms) / 1000.0, tz=dt.timezone.utc).replace(
        tzinfo=None
    )


def _op_to_taskinfo(payload: dict[str, Any]) -> TaskInfo:
    """Build a :class:`TaskInfo` from either of the two EE response shapes.

    Two distinguishing markers:

    * The `ee.data.listOperations()` shape carries a nested `metadata`
      dict (Google Long-Running-Operation envelope).
    * The `ee.batch.Task.status()` shape is flat with `state` /
      `task_type` / `creation_timestamp_ms` / etc. at the top level.

    Args:
        payload: One operation dict from either API.

    Returns:
        A :class:`TaskInfo` with normalised fields.

    Raises:
        ValueError: If `payload` is neither shape, or carries a `state`
            outside :data:`_VALID_TASK_STATES`.
    """
    if "metadata" in payload:
        meta = payload.get("metadata") or {}
        op_name = payload.get("name", "")
        task_id = op_name.rsplit("/", 1)[-1] if op_name else ""
        state = str(meta.get("state", ""))
        task_type = str(meta.get("type", ""))
        create_time = _parse_iso(meta.get("createTime"))
        update_time = _parse_iso(meta.get("updateTime")) or create_time
        start_time = _parse_iso(meta.get("startTime"))
        attempt = int(meta.get("attempt", 1) or 1)
        priority = meta.get("priority")
        description = str(meta.get("description", ""))
        done = bool(payload.get("done", False))
        response = payload.get("response") or {}
        destination_uris = tuple(response.get("destination_uris") or ())
        error = payload.get("error") or {}
        error_message = error.get("message") if error else None
    else:
        # `task.status()` flat shape.
        op_name = payload.get("name", "")
        task_id = str(payload.get("id") or (op_name.rsplit("/", 1)[-1] if op_name else ""))
        state = str(payload.get("state", "")).rsplit(".", 1)[-1].upper()
        task_type = str(payload.get("task_type", ""))
        create_time = _parse_ms(payload.get("creation_timestamp_ms"))
        update_time = _parse_ms(payload.get("update_timestamp_ms")) or create_time
        start_time = _parse_ms(payload.get("start_timestamp_ms"))
        attempt = int(payload.get("attempt", 1) or 1)
        priority = payload.get("priority")
        description = str(payload.get("description", ""))
        done = state in TERMINAL_TASK_STATES
        destination_uris = tuple(payload.get("destination_uris") or ())
        error_message = payload.get("error_message")

    state = _STATE_ALIASES.get(state, state)
    if state not in _VALID_TASK_STATES:
        raise ValueError(
            f"unknown EE task state {state!r}; expected one of "
            f"{sorted(_VALID_TASK_STATES)} (or LRO aliases "
            f"{sorted(_STATE_ALIASES)})"
        )
    if create_time is None:
        # Both shapes should always carry a create-time; fall back to
        # `now()` rather than crashing on an EE-side schema surprise.
        create_time = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if update_time is None:
        update_time = create_time

    return TaskInfo(
        id=task_id,
        operation_name=op_name or _operation_name(task_id),
        description=description,
        state=state,  # type: ignore[arg-type]
        task_type=task_type,
        create_time=create_time,
        update_time=update_time,
        start_time=start_time,
        attempt=attempt,
        priority=int(priority) if priority is not None else None,
        destination_uris=destination_uris,
        error_message=error_message,
        done=done,
    )


# -- public API -------------------------------------------------------------


def list_recent_tasks(
    state: str | Iterable[str] | None = None,
    max_age_min: int | None = None,
    task_type: str | None = None,
    description_prefix: str | None = None,
    project: str | None = None,
    limit: int | None = None,
) -> list[TaskInfo]:
    """List the user's batch Earth Engine tasks on the current project.

    Wraps `ee.data.listOperations(project)` and applies every filter
    client-side (EE's `listOperations` endpoint doesn't accept query
    parameters). Results are sorted newest-first by `create_time`.

    Note:
        **Network cost.** Because every filter is client-side, this call
        fetches *all* operations the EE backend still has on file for
        the project — which paginates internally inside the SDK. For a
        hobby project with a dozen tasks the cost is one HTTP round
        trip; for a CI / orchestration project with hundreds of tasks
        it is several. If you're polling on a tight loop (e.g. every
        minute from a worker), pass a small `limit=` (e.g. `50`) plus a
        tight `max_age_min=` to bound the worst case. The `limit` is
        applied *after* the fetch + sort, so it caps memory and printed
        output, not the number of HTTP round trips — for the latter
        you'll want a tight `max_age_min` so the SDK can stop paginating
        once it's seen enough old entries.

    Args:
        state: One state name or an iterable of names — e.g.
            `"RUNNING"` or `{"FAILED", "CANCELLED"}`. `None` (the
            default) returns every state.
        max_age_min: Drop tasks older than this many minutes (clock
            difference uses the EE-reported `createTime`). `None`
            keeps every task EE still has on file.
        task_type: Filter by `Task.Type` (e.g. `"EXPORT_IMAGE"`).
        description_prefix: Substring match against the task
            `description` — works well with earthlens's
            `<asset-slug>_<bands>_<YYYYMMDD>` naming.
        project: Cloud project id to scope to; defaults to whichever
            project the EE SDK was initialised with.
        limit: Hard cap on returned entries (applied after sort);
            `None` for no cap. See the network-cost note above — this
            caps the returned list, not the underlying pagination.

    Returns:
        A list of :class:`TaskInfo` sorted newest first.

    Raises:
        ValueError: If a passed `state` is not in :data:`_VALID_TASK_STATES`.
    """
    wanted_states: frozenset[str] | None
    if state is None:
        wanted_states = None
    elif isinstance(state, str):
        wanted_states = frozenset({state})
    else:
        wanted_states = frozenset(state)
    if wanted_states is not None:
        bad = wanted_states - _VALID_TASK_STATES
        if bad:
            raise ValueError(
                f"unknown task state(s) {sorted(bad)}; expected one of "
                f"{sorted(_VALID_TASK_STATES)}"
            )

    proj = _resolve_project(project)
    raw = ee.data.listOperations(f"projects/{proj}")
    tasks = [_op_to_taskinfo(op) for op in raw]

    if wanted_states is not None:
        tasks = [t for t in tasks if t.state in wanted_states]
    if task_type is not None:
        tasks = [t for t in tasks if t.task_type == task_type]
    if description_prefix is not None:
        tasks = [t for t in tasks if description_prefix in t.description]
    if max_age_min is not None:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        cutoff = now - dt.timedelta(minutes=max_age_min)
        tasks = [t for t in tasks if t.create_time >= cutoff]

    tasks.sort(key=lambda t: t.create_time, reverse=True)
    if limit is not None:
        tasks = tasks[:limit]
    return tasks


def get_task_status(task_id: str, project: str | None = None) -> TaskInfo:
    """Fetch one task by id and return its normalised :class:`TaskInfo`.

    Args:
        task_id: The bare task id (e.g. `"SNR3SZ6GNH5LUJQJ52VVNSZT"`)
            or a full `projects/.../operations/<id>` operation name.
        project: Cloud project id to scope to when `task_id` is a bare
            id. Defaults to the SDK's current project.

    Returns:
        A :class:`TaskInfo`.

    Raises:
        Exceptions from the underlying `ee.data.getOperation` call
        propagate verbatim — most commonly a `googleapiclient.errors.HttpError`
        with a 404 when the id is unknown to EE.
    """
    op_name = _operation_name(task_id, project)
    raw = ee.data.getOperation(op_name)
    return _op_to_taskinfo(raw)


def cancel_task(task_id: str, project: str | None = None) -> None:
    """Cancel a queued or running task by id.

    A no-op (logs at INFO + returns) when the task is already in a
    terminal state. Google Cloud's Long-Running-Operations API
    returns `FAILED_PRECONDITION` (HTTP 400) when asked to cancel an
    already-finished operation; the wrapper catches that path so a
    cleanup loop over `list_recent_tasks` can call `cancel_task` on
    every match without special-casing the terminal ones.

    Args:
        task_id: The bare task id or full operation name (see
            :func:`get_task_status`).
        project: Cloud project id to scope to when `task_id` is bare.

    Raises:
        Exceptions from the underlying `ee.data.cancelOperation`
        propagate verbatim **except** the already-terminal
        `FAILED_PRECONDITION` HttpError, which is downgraded to an
        INFO log.
    """
    op_name = _operation_name(task_id, project)
    try:
        ee.data.cancelOperation(op_name)
    except Exception as exc:  # noqa: BLE001 - inspect the HTTP status below
        # `googleapiclient.errors.HttpError` carries `.resp.status`; on
        # an already-terminal op we get a 400. Anything else is the
        # user's problem.
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 400 or "FAILED_PRECONDITION" in str(exc):
            logger.info(
                f"cancel_task({task_id!r}): already terminal "
                f"({type(exc).__name__}: {exc}); treating as no-op."
            )
            return
        raise


def wait_for_task_id(
    task_id: str,
    *,
    poll_seconds: float = 15.0,
    progress_bar: bool = True,
    project: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> TaskInfo:
    """Block until a task reaches a terminal state, then return its `TaskInfo`.

    Unlike :func:`earthlens.gee._helpers.wait_for_task` (which needs a
    live `ee.batch.Task` handle), this only needs the id, so it works
    for tasks submitted from a previous process / session.

    Args:
        task_id: The bare task id or full operation name.
        poll_seconds: Seconds between status polls. Defaults to `15.0`
            (same as the in-process `wait_for_task`).
        progress_bar: Show a `tqdm` spinner with the current state.
            Defaults to `True`.
        project: Cloud project id to scope to when `task_id` is bare.
        sleep: Sleep implementation (injectable so tests run instantly).

    Returns:
        The final :class:`TaskInfo` (state `"COMPLETED"`).

    Raises:
        RuntimeError: If the task ends `FAILED` / `CANCELLED` /
            `CANCEL_REQUESTED`; the message includes
            `TaskInfo.error_message` when present.
    """
    spinner = tqdm(desc=f"EE task {task_id[:12]}", unit="poll", disable=not progress_bar)
    info: TaskInfo
    try:
        while True:
            info = get_task_status(task_id, project)
            spinner.set_postfix_str(info.state)
            spinner.update(1)
            if info.state in TERMINAL_TASK_STATES:
                break
            sleep(poll_seconds)
    finally:
        spinner.close()
    if info.state != "COMPLETED":
        detail = info.error_message or ""
        raise RuntimeError(
            f"Earth Engine task {info.id} ended {info.state}"
            + (f": {detail}" if detail else "")
        )
    return info


#: Recognised destination-URI shapes. Each entry is a
#: `(predicate, downloader)` pair; `resolve_destination(download_to=...)`
#: walks the list in order and dispatches the first match.
_DRIVE_FILE_URL_RE = re.compile(
    r"^https?://drive\.google\.com/(?:open\?id=|file/d/|uc\?(?:[^#]*&)?id=)"
    r"(?P<file_id>[A-Za-z0-9_-]+)"
)
_DRIVE_FOLDER_URL_RE = re.compile(
    r"^https?://drive\.google\.com/(?:drive/)?(?:#?folders/|drive/folders/)"
    r"(?P<folder_id>[A-Za-z0-9_-]+)"
)
_GCS_HTTPS_RE = re.compile(
    r"^https?://(?:storage\.googleapis\.com|storage\.cloud\.google\.com)/"
    r"(?P<bucket>[^/]+)/(?P<key>.+?)(?:[?#].*)?$"
)
_GCS_GS_RE = re.compile(r"^gs://(?P<bucket>[^/]+)/(?P<key>.+)$")
_EE_ASSET_RE = re.compile(r"^projects/[^/]+/assets/.+")


def _pull_gcs(bucket: str, key: str, dest_dir: Path) -> Path:
    """Download `gs://<bucket>/<key>` to `dest_dir / <basename>` and return the path.

    Threads the EE persistent credentials through to the GCS client
    so the download uses the same identity that authored the export
    — not whatever Application Default Credentials happen to be on
    the box. Requires the EE service account (or whichever identity
    initialised `ee`) to have the `devstorage.full_control` scope;
    :class:`earthlens.gee.auth.EarthEngineAuth` requests it by
    default.
    """
    from google.cloud import storage as _gcs

    client = _gcs.Client(credentials=ee.data.get_persistent_credentials())
    blob = client.bucket(bucket).blob(unquote(key))
    target = dest_dir / Path(unquote(key)).name
    blob.download_to_filename(str(target))
    return target


def _pull_drive_file(file_id: str, dest_dir: Path) -> Path:
    """Download a Drive file by id to `dest_dir / <drive-name>` and return the path.

    Uses the Drive v3 API with the existing earthengine-api / EE-auth
    credentials. Requires the service account (or whichever identity
    initialised `ee`) to have either ownership of the file or a Drive
    share that includes it.
    """
    from googleapiclient.discovery import build  # type: ignore[import-untyped]
    from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-untyped]

    creds = ee.data.get_persistent_credentials()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    meta = drive.files().get(fileId=file_id, fields="name").execute()
    target = dest_dir / meta["name"]
    request = drive.files().get_media(fileId=file_id)
    with target.open("wb") as out:
        downloader = MediaIoBaseDownload(out, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return target


def resolve_destination(
    task_info: TaskInfo,
    *,
    download_to: Path | str | None = None,
) -> list[str | Path]:
    """Return — and optionally download — a completed task's destinations.

    For Drive / GCS / asset exports, EE populates
    `Operation.response.destination_uris` once the task reaches
    `COMPLETED`. This helper surfaces them as a plain list and, when
    `download_to=` is given, dispatches each URI to the appropriate
    transport:

    * `https://drive.google.com/file/d/<id>/...` (single-file Drive
      URL) → Drive v3 `files().get_media(fileId=<id>)`. Requires the
      identity initialised on `ee` to have access to the file (either
      ownership or a share). Folder-form URLs
      (`https://drive.google.com/#folders/<id>`) are surfaced
      verbatim with a warning — list-the-folder + download-children
      is intentionally out of scope (the typical service-account
      pattern shares per-file, not per-folder).
    * `gs://<bucket>/<key>` / `https://storage.googleapis.com/<bucket>/<key>`
      → google-cloud-storage `Blob.download_to_filename`.
    * `projects/<proj>/assets/<...>` → returned verbatim (no-op; the
      asset already lives on EE and is consumable via
      `ee.Image(asset_id)` / `ee.FeatureCollection(asset_id)`).

    Any URI that matches none of the above is surfaced as-is and
    logged at WARNING so the caller can decide what to do with it.

    Args:
        task_info: A terminal-state :class:`TaskInfo` (typically from
            :func:`get_task_status` after :func:`wait_for_task_id`).
        download_to: When given, a directory path; downloaded files
            land under it (created on demand). When `None` (the
            default), the helper only returns the URIs verbatim — no
            downloads.

    Returns:
        A list whose entries are either local
        :class:`pathlib.Path`s (for downloaded files) or URI strings
        (for the no-op asset path / unrecognised URIs / when
        `download_to is None`). Ordering matches
        `task_info.destination_uris`.

    Raises:
        Exceptions from the underlying GCS / Drive client calls
        propagate verbatim. Missing optional deps surface as
        `ImportError`.
    """
    if task_info.state != "COMPLETED":
        logger.warning(
            f"resolve_destination: task {task_info.id} is "
            f"{task_info.state}, not COMPLETED — destination_uris is empty."
        )
        return list(task_info.destination_uris)

    if download_to is None:
        return list(task_info.destination_uris)

    dest_dir = Path(download_to)
    dest_dir.mkdir(parents=True, exist_ok=True)

    out: list[str | Path] = []
    for uri in task_info.destination_uris:
        m = _DRIVE_FILE_URL_RE.match(uri)
        if m:
            out.append(_pull_drive_file(m.group("file_id"), dest_dir))
            continue
        if _DRIVE_FOLDER_URL_RE.match(uri):
            logger.warning(
                f"resolve_destination: Drive folder URL surfaced verbatim "
                f"({uri}); list-and-download of folder children is out of "
                "scope — share individual files with the SA instead, or "
                "pull from the Drive UI."
            )
            out.append(uri)
            continue
        m = _GCS_HTTPS_RE.match(uri) or _GCS_GS_RE.match(uri)
        if m:
            out.append(_pull_gcs(m.group("bucket"), m.group("key"), dest_dir))
            continue
        if _EE_ASSET_RE.match(uri):
            logger.info(
                f"resolve_destination: EE asset {uri} stays on EE "
                "(no local file produced); use ee.Image(asset_id) to read."
            )
            out.append(uri)
            continue
        logger.warning(
            f"resolve_destination: unrecognised destination URI shape "
            f"{uri!r}; surfaced verbatim without download."
        )
        out.append(uri)
    return out


# -- CLI --------------------------------------------------------------------


def _maybe_initialize_ee() -> None:
    """Authenticate `ee` from env vars when not already initialised.

    Probes the current EE state with `ee.data._get_projects_path()`
    (which raises `EEException` if `ee.Initialize` hasn't run). When
    EE is uninitialised, prefers `GEE_SERVICE_ACCOUNT` +
    `GEE_SERVICE_KEY`, else falls back to `GEE_PROJECT` for the
    interactive `ee.Initialize(project=...)` path.
    """
    import os

    try:
        ee.data._get_projects_path()
        return
    except ee.EEException:
        pass
    sa = os.environ.get("GEE_SERVICE_ACCOUNT")
    key = os.environ.get("GEE_SERVICE_KEY")
    if sa and key:
        from earthlens.gee.auth import EarthEngineAuth

        EarthEngineAuth.initialize(sa, key)
        return
    project = os.environ.get("GEE_PROJECT")
    if project:
        ee.Initialize(project=project)
        return
    raise SystemExit(
        "Earth Engine is not initialised. Set GEE_SERVICE_ACCOUNT + "
        "GEE_SERVICE_KEY (preferred) or GEE_PROJECT, or call "
        "`ee.Initialize(...)` in a Python session before invoking this CLI."
    )


def _print_task_oneline(t: TaskInfo) -> None:
    """Render a single :class:`TaskInfo` as one terminal-friendly line.

    Uses ASCII `-` for the unstarted-task placeholder so the line
    renders cleanly on Windows `cmd.exe` (cp437 / cp1252), which
    otherwise turns `—` (U+2014) into `?`.
    """
    started = t.start_time.strftime("%Y-%m-%d %H:%M:%S") if t.start_time else "-"
    print(
        f"{t.id:26} {t.state:18} {t.task_type:14} "
        f"created={t.create_time:%Y-%m-%d %H:%M:%S} started={started:<19} "
        f"{t.description}"
    )


def _cmd_list(args) -> int:
    tasks = list_recent_tasks(
        state=args.state,
        max_age_min=args.max_age_min,
        task_type=args.task_type,
        description_prefix=args.description_prefix,
        project=args.project,
        limit=args.limit,
    )
    if not tasks:
        print("(no tasks match)")
        return 0
    for t in tasks:
        _print_task_oneline(t)
    return 0


def _cmd_status(args) -> int:
    t = get_task_status(args.task_id, project=args.project)
    print(t.model_dump_json(indent=2))
    return 0


def _cmd_cancel(args) -> int:
    cancel_task(args.task_id, project=args.project)
    print(f"cancel requested for {args.task_id}")
    return 0


def _cmd_wait(args) -> int:
    """Block until a task terminates; print result; exit 1 on FAILED / CANCELLED.

    `wait_for_task_id` raises `RuntimeError` when the task ends in any
    non-COMPLETED state (FAILED / CANCELLED). The CLI catches that and
    prints a one-line message to stderr — letting it propagate would
    surface a Python traceback to the user, which isn't what a polished
    CLI should do for a known failure mode.
    """
    import sys

    try:
        t = wait_for_task_id(
            args.task_id,
            poll_seconds=args.poll_seconds,
            progress_bar=not args.no_progress_bar,
            project=args.project,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"task {t.id} COMPLETED")
    for uri in t.destination_uris:
        print(f"  -> {uri}")
    return 0


def _build_argparser():
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m earthlens.gee.jobs",
        description=(
            "Track Earth Engine batch tasks (Drive / GCS / asset sinks). "
            "Reads GEE_SERVICE_ACCOUNT + GEE_SERVICE_KEY from the "
            "environment when ee is not already initialised."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    list_p = sub.add_parser("list", help="list recent tasks (one per line)")
    list_p.add_argument(
        "--state", default=None, nargs="+", metavar="STATE",
        help=(
            'filter by one or more states (e.g. "--state RUNNING READY" '
            'or "--state FAILED"). Mirrors the iterable form of '
            "list_recent_tasks(state=...)."
        ),
    )
    list_p.add_argument("--max-age-min", type=int, default=None,
                       help="drop tasks older than this many minutes")
    list_p.add_argument("--task-type", default=None,
                       help='filter by Task.Type (e.g. "EXPORT_IMAGE")')
    list_p.add_argument("--description-prefix", default=None,
                       help="substring match against task description")
    list_p.add_argument("--limit", type=int, default=None)
    list_p.add_argument("--project", default=None)
    list_p.set_defaults(func=_cmd_list)

    status_p = sub.add_parser("status", help="dump one task as pydantic JSON")
    status_p.add_argument("task_id")
    status_p.add_argument("--project", default=None)
    status_p.set_defaults(func=_cmd_status)

    cancel_p = sub.add_parser("cancel", help="cancel a queued / running task")
    cancel_p.add_argument("task_id")
    cancel_p.add_argument("--project", default=None)
    cancel_p.set_defaults(func=_cmd_cancel)

    wait_p = sub.add_parser("wait", help="block until a task reaches a terminal state")
    wait_p.add_argument("task_id")
    wait_p.add_argument("--poll-seconds", type=float, default=15.0)
    wait_p.add_argument("--no-progress-bar", action="store_true")
    wait_p.add_argument("--project", default=None)
    wait_p.set_defaults(func=_cmd_wait)

    return p


def main(argv: list[str] | None = None) -> int:
    """Entry point for `python -m earthlens.gee.jobs`."""
    parser = _build_argparser()
    args = parser.parse_args(argv)
    _maybe_initialize_ee()
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in M5 tests
    raise SystemExit(main())
