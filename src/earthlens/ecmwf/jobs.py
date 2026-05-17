"""CDS retrieval-jobs HTTP helpers (N3).

Thin wrappers over the CDS `/retrieve/v1/jobs` REST endpoints — listing
the user's recent jobs, downloading a successful job's result asset,
and the `~/.cdsapirc` reader they share. These are catalog-independent
(they don't read `cds_data_catalog.yaml`); promoted out of
`earthlens.ecmwf.catalog` so the catalog module stays focused on its
schema and the HTTP plumbing has a clear separation-of-concerns home.

`Catalog.list_recent_jobs` / `Catalog.download_job` delegate to the
free functions here for back-compat.
"""

from __future__ import annotations

import datetime
import urllib.request
from pathlib import Path
from typing import Any

import requests


def read_cdsapirc() -> dict[str, str]:
    """Parse `~/.cdsapirc` into a `{url, key}` dict.

    Used by :func:`list_recent_jobs` and :func:`download_job` to
    authenticate the bare HTTP calls without spinning up a full
    :class:`cdsapi.Client`.
    """
    cfg: dict[str, str] = {}
    for line in (Path.home() / ".cdsapirc").read_text().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            cfg[key.strip()] = value.strip()
    return cfg


def list_recent_jobs(
    status: str | None = None,
    max_age_min: int = 60,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the user's recent CDS retrieval jobs.

    Wraps `GET /retrieve/v1/jobs` with the same Personal Access Token
    cdsapi uses (read from `~/.cdsapirc`). Useful for resuming
    downloads after a script crash, or inspecting which probes have
    completed without rerunning them.

    Args:
        status: Optional filter — one of `"accepted"`, `"running"`,
            `"successful"`, `"failed"`, `"rejected"`. `None` returns
            every status.
        max_age_min: Drop entries older than this many minutes (CDS
            retains job records for a few weeks). Defaults to `60`.
        limit: Hard cap on returned entries, sent as the `limit` query
            param. Defaults to `50`.

    Returns:
        Each entry has at least `jobID` / `processID` (= dataset name)
        / `status` / `created`. See the CDS OGC API processes spec for
        the full schema.
    """
    cfg = read_cdsapirc()
    url = cfg["url"].rstrip("/") + "/retrieve/v1/jobs"
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    resp = requests.get(
        url,
        headers={"PRIVATE-TOKEN": cfg["key"]},
        params=params,
        timeout=30,
    )
    resp.raise_for_status()
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    out: list[dict[str, Any]] = []
    for job in resp.json().get("jobs", []):
        created = job.get("created", "")
        if not created:
            continue
        ago = (
            now - datetime.datetime.fromisoformat(created.replace("Z", ""))
        ).total_seconds() / 60
        if ago <= max_age_min:
            out.append(job)
    return out


def download_job(
    job_id: str,
    target: Path | str,
    chunk_size: int = 1 << 20,
) -> Path:
    """Download the result asset of a successful CDS job.

    Looks up `job_id` via `GET /retrieve/v1/jobs/<id>/results`, follows
    the asset's `href`, and streams the body into `target`. Idempotent —
    if `target` already exists with a non-zero size the download is
    skipped.

    Args:
        job_id: CDS job identifier (e.g. as returned by
            :func:`list_recent_jobs`).
        target: Destination path. Parents are created.
        chunk_size: Streaming chunk size in bytes. Defaults to 1 MiB.

    Returns:
        `target`, after the download completes.

    Raises:
        requests.HTTPError: If the job does not exist or its result
            has expired.
        ValueError: If the job's results record contains no downloadable
            asset href, or the href scheme isn't http(s).
    """
    cfg = read_cdsapirc()
    target_path = Path(target)
    if target_path.exists() and target_path.stat().st_size > 0:
        return target_path
    rurl = cfg["url"].rstrip("/") + f"/retrieve/v1/jobs/{job_id}/results"
    resp = requests.get(rurl, headers={"PRIVATE-TOKEN": cfg["key"]}, timeout=30)
    resp.raise_for_status()
    href = resp.json().get("asset", {}).get("value", {}).get("href")
    if not href:
        raise ValueError(
            f"job {job_id!r} has no downloadable asset href in its "
            "results record"
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # `href` comes from CDS server JSON; reject anything that is not
    # http(s) so a malicious / corrupted response can't coerce us into
    # reading a local file via `file://`.
    if not href.startswith(("https://", "http://")):
        raise ValueError(f"refusing to download from non-http(s) href: {href!r}")
    with (
        # Scheme validated above — bandit B310 does not apply.
        urllib.request.urlopen(href, timeout=60) as src,  # nosec B310
        open(target_path, "wb") as out,
    ):
        while chunk := src.read(chunk_size):
            out.write(chunk)
    return target_path
