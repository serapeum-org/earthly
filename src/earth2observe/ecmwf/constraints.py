"""Pre-flight validation of CDS retrieve requests against `constraints.json`.

Every CDS dataset publishes a constraints document at
``https://cds.climate.copernicus.eu/api/catalogue/v1/collections/<id>/constraints.json``
listing the (variable × extras) combinations the server actually
accepts. Submitting a request with any value outside that document
results in a 400 ``Request has not produced a valid combination of
values`` after the request has already taken a per-dataset queue
slot — wasting both queue capacity and wall-clock time.

This module hits the constraints endpoint once per dataset (cached
in-process), and rejects mismatched requests at the call site so
the user sees a clear error before :meth:`cdsapi.Client.retrieve`
is invoked.

Set the environment variable ``E2O_SKIP_CONSTRAINTS=1`` to bypass
validation — useful when the constraints endpoint is missing or
known to be inaccurate for a particular dataset.

Examples:
    - Validate a request against ERA5 single-levels constraints:

        ```python
        >>> from earth2observe.ecmwf.constraints import validate_request
        >>> request = {
        ...     "variable": ["2m_temperature"],
        ...     "year": ["2022"],
        ...     "month": ["01"],
        ...     "day": ["01"],
        ...     "time": ["00:00"],
        ...     "product_type": ["reanalysis"],
        ... }
        >>> validate_request(  # doctest: +SKIP
        ...     "reanalysis-era5-single-levels", request,
        ... )

        ```
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

CONSTRAINTS_URL_TEMPLATE = (
    "https://cds.climate.copernicus.eu/api/catalogue/v1/collections/"
    "{dataset}/constraints.json"
)

# Keys the constraints document never enumerates because they are
# universally accepted by the cdsapi front-end (geographic /
# format / output controls). Skipped during validation.
_UNIVERSAL_KEYS: frozenset[str] = frozenset(
    {"area", "data_format", "format", "grid"}
)

# Module-level cache so each dataset is only fetched once per
# Python process. ``None`` is reserved for "fetch attempted, no
# constraints available" so we don't refetch on every retry.
_CACHE: dict[str, list[dict[str, Any]] | None] = {}


def fetch_constraints(dataset: str) -> list[dict[str, Any]]:
    """Fetch and cache the constraints document for ``dataset``.

    Args:
        dataset: CDS dataset short name
            (e.g. ``"reanalysis-era5-single-levels"``).

    Returns:
        list[dict[str, Any]]: Each entry is a dict mapping selector
        names (``variable``, ``year``, ``level_type``, …) to the
        allowed values for that combination. Returns an empty list
        when the endpoint is missing, returns 404, or transport
        fails — callers should treat that as "skip validation".

    Examples:
        - First call hits the network; second call returns the
          cached value (``# doctest: +SKIP`` because it requires
          network access):

            ```python
            >>> from earth2observe.ecmwf.constraints import fetch_constraints
            >>> entries = fetch_constraints(  # doctest: +SKIP
            ...     "reanalysis-era5-single-levels",
            ... )
            >>> isinstance(entries, list)  # doctest: +SKIP
            True

            ```
    """
    cached = _CACHE.get(dataset, "MISS")
    if cached != "MISS":
        return cached or []
    url = CONSTRAINTS_URL_TEMPLATE.format(dataset=dataset)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, OSError):
        # Network failure or non-JSON response — treat as
        # "no constraints" so callers fall back to letting CDS
        # itself reject the request. Caching ``None`` makes
        # later calls cheap.
        _CACHE[dataset] = None
        return []
    if not isinstance(payload, list):
        _CACHE[dataset] = None
        return []
    _CACHE[dataset] = payload
    return payload


def validate_request(dataset: str, request: dict[str, Any]) -> None:
    """Validate ``request`` against the dataset's `constraints.json`.

    Walks the cached constraints document looking for at least one
    entry whose allowed-value sets cover every key in ``request``
    that the document enumerates. Universal keys
    (:data:`_UNIVERSAL_KEYS` — ``area`` / ``data_format`` / etc.)
    are ignored; keys the constraints document does not mention are
    also ignored (they may be optional for that dataset).

    Args:
        dataset: CDS dataset short name.
        request: The request dict that will be passed to
            :meth:`cdsapi.Client.retrieve`.

    Raises:
        ValueError: If no constraints entry covers the request.
            The message names the dataset and the keys whose values
            were not present in any constraint entry, plus a URL
            pointing at the live constraints document.

    Examples:
        - The check is a no-op when ``E2O_SKIP_CONSTRAINTS`` is
          set to a truthy value:

            ```python
            >>> import os
            >>> from earth2observe.ecmwf.constraints import validate_request
            >>> os.environ["E2O_SKIP_CONSTRAINTS"] = "1"
            >>> validate_request("any-dataset", {"variable": ["x"]})
            >>> os.environ.pop("E2O_SKIP_CONSTRAINTS", None) is not None
            True

            ```
    """
    if os.environ.get("E2O_SKIP_CONSTRAINTS"):
        return
    constraints = fetch_constraints(dataset)
    if not constraints:
        return  # Nothing to validate against
    constraint_keys: set[str] = set().union(
        *(set(entry) for entry in constraints)
    )
    # Normalise the request to per-key sets, dropping keys the
    # constraints document does not enumerate.
    req_norm: dict[str, set[Any]] = {}
    for key, value in request.items():
        if key in _UNIVERSAL_KEYS or key not in constraint_keys:
            continue
        if isinstance(value, list):
            req_norm[key] = set(value)
        else:
            req_norm[key] = {value}
    if not req_norm:
        return
    for entry in constraints:
        if all(
            key in entry and req_norm[key] <= set(entry[key])
            for key in req_norm
        ):
            return
    # No entry matched — surface the keys whose values were not in
    # *any* entry, so the user can spot which extras to fix.
    bad_keys: list[str] = []
    for key, values in req_norm.items():
        seen: set[Any] = set()
        for entry in constraints:
            if key in entry:
                seen.update(entry[key])
        missing = values - seen
        if missing:
            bad_keys.append(f"{key}={sorted(missing)!r}")
    raise ValueError(
        f"Request for {dataset!r} does not match any constraint "
        f"entry. Offending values: {', '.join(bad_keys) or '(unclear)'}\n"
        f"Submitted request: {request}\n"
        f"Live constraints: "
        + CONSTRAINTS_URL_TEMPLATE.format(dataset=dataset)
    )
