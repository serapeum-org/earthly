"""Pre-flight validation of CDS retrieve requests against `constraints.json`.

Every CDS dataset publishes a constraints document at
`https://cds.climate.copernicus.eu/api/catalogue/v1/collections/<id>/constraints.json`
listing the (variable × extras) combinations the server actually
accepts. Submitting a request with any value outside that document
results in a 400 `Request has not produced a valid combination of
values` after the request has already taken a per-dataset queue
slot — wasting both queue capacity and wall-clock time.

This module hits the constraints endpoint once per dataset (cached
in-process), and rejects mismatched requests at the call site so
the user sees a clear error before :meth:`cdsapi.Client.retrieve`
is invoked.

Set the environment variable `E2O_SKIP_CONSTRAINTS=1` to bypass
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

import calendar
import datetime
import difflib
import itertools
import json
import os
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

__all__ = ["CONSTRAINTS_URL_TEMPLATE", "fetch_constraints", "validate_request"]

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
# Python process. `None` is reserved for "fetch attempted, no
# constraints available" so we don't refetch on every retry.
_CACHE: dict[str, list[dict[str, Any]] | None] = {}


def fetch_constraints(dataset: str) -> list[dict[str, Any]]:
    """Fetch and cache the constraints document for `dataset`.

    Args:
        dataset: CDS dataset short name
            (e.g. `"reanalysis-era5-single-levels"`).

    Returns:
        list[dict[str, Any]]: Each entry is a dict mapping selector
        names (`variable`, `year`, `level_type`, …) to the
        allowed values for that combination. Returns an empty list
        when the endpoint is missing, returns 404, or transport
        fails — callers should treat that as "skip validation".

    Examples:
        - First call hits the network; second call returns the
          cached value (`# doctest: +SKIP` because it requires
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
        # itself reject the request. Caching `None` makes
        # later calls cheap.
        _CACHE[dataset] = None
        return []
    if not isinstance(payload, list):
        _CACHE[dataset] = None
        return []
    _CACHE[dataset] = payload
    return payload


def _as_list(value: Any) -> list[Any]:
    """Normalise a request value to a list (CDS allows either form)."""
    return value if isinstance(value, list) else [value]


def _try_int(value: Any) -> int | None:
    """Coerce `value` to int, or return None if not coercible."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_date_validity(request: dict[str, Any]) -> None:
    """Reject obvious year/month/day mistakes (Feb 30, month=13, …).

    Lenient: skips when a value is not an integer string (some
    datasets use `year=["all"]` or period ranges and the strict
    integer check should not flag those).
    """
    years = _as_list(request.get("year", []))
    months = _as_list(request.get("month", []))
    days = _as_list(request.get("day", []))

    for label, raw in (("year", years), ("month", months), ("day", days)):
        for value in raw:
            n = _try_int(value)
            if n is None:
                continue
            if label == "month" and not (1 <= n <= 12):
                raise ValueError(
                    f"month={value!r} must be 01-12 (got {n})"
                )
            if label == "day" and not (1 <= n <= 31):
                raise ValueError(
                    f"day={value!r} must be 01-31 (got {n})"
                )
            if label == "year" and not (1850 <= n <= 2100):
                raise ValueError(
                    f"year={value!r} outside the plausible 1850-2100 range "
                    f"(got {n})"
                )

    # Cross-field: every (year, month, day) triple must be a real
    # calendar date. Catches Feb 30, Apr 31, etc. — the constraints
    # walk would also reject these but with a less specific message.
    if years and months and days:
        for y, m, d in itertools.product(years, months, days):
            yi, mi, di = _try_int(y), _try_int(m), _try_int(d)
            if yi is None or mi is None or di is None:
                continue
            try:
                datetime.date(yi, mi, di)
            except ValueError as exc:
                raise ValueError(
                    f"year/month/day combination "
                    f"{yi:04d}-{mi:02d}-{di:02d} is not a real date: {exc}"
                ) from None


class Area(BaseModel):
    """CDS `area` bbox validated against lat/lon bounds.

    Used by :func:`_validate_area` to fail fast on swapped indices,
    out-of-range latitudes, or non-numeric values — before the
    request reaches CDS and burns a queue slot.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    north: float
    west: float
    south: float
    east: float

    @model_validator(mode="after")
    def _check_bounds(self) -> Area:
        if not -90.0 <= self.south <= 90.0 or not -90.0 <= self.north <= 90.0:
            raise ValueError(
                f"area latitudes must be in [-90, 90]: north={self.north}, "
                f"south={self.south}"
            )
        if self.south > self.north:
            raise ValueError(
                f"area south ({self.south}) must be <= north ({self.north}); "
                "did you swap the north/south indices?"
            )
        if not -360.0 <= self.west <= 360.0 or not -360.0 <= self.east <= 360.0:
            raise ValueError(
                f"area longitudes must be in [-360, 360]: west={self.west}, "
                f"east={self.east}"
            )
        return self


def _validate_area(request: dict[str, Any]) -> None:
    """Reject malformed `area` bboxes before they reach MARS.

    CDS expects `[north, west, south, east]` with latitudes in
    [-90, 90] and `south <= north`. Longitudes can wrap so the
    check is wider; the goal is to catch user typos like swapping
    north/south or passing the wrong number of values.
    """
    area = request.get("area")
    if area is None:
        return
    if not isinstance(area, (list, tuple)) or len(area) != 4:
        raise ValueError(
            f"area must be a 4-element list [north, west, south, east], "
            f"got {area!r}"
        )
    try:
        Area(north=area[0], west=area[1], south=area[2], east=area[3])
    except ValidationError as exc:
        first = exc.errors()[0]
        if "float" in first["type"]:
            raise ValueError(
                f"area values must be numeric: {area!r}"
            ) from None
        raise ValueError(first["msg"].removeprefix("Value error, ")) from None


def _validate_variable_typos(
    dataset: str,
    request: dict[str, Any],
    constraints: list[dict[str, Any]],
) -> None:
    """Suggest typo fixes when a requested variable isn't catalogued.

    Walks `constraints` to collect every catalogued variable, then
    flags any request variable that isn't in that set and offers up
    to 3 close matches via :func:`difflib.get_close_matches`.
    """
    requested = _as_list(request.get("variable", []))
    catalogued: set[str] = set()
    for entry in constraints:
        catalogued.update(entry.get("variable", []))

    unknowns: list[tuple[str, list[str]]] = []
    if requested and catalogued:
        for variable in requested:
            if variable not in catalogued:
                suggestions = difflib.get_close_matches(
                    variable, catalogued, n=3, cutoff=0.6
                )
                unknowns.append((variable, suggestions))

    if unknowns:
        parts: list[str] = []
        for variable, suggestions in unknowns:
            if suggestions:
                parts.append(f"{variable!r} -> did you mean {suggestions}?")
            else:
                parts.append(f"{variable!r} (no close match in catalogue)")
        raise ValueError(
            f"Request for {dataset!r} names unknown variable(s): "
            + "; ".join(parts)
            + f"\nLive constraints: "
            + CONSTRAINTS_URL_TEMPLATE.format(dataset=dataset)
        )


def _validate_required_fields(
    dataset: str,
    request: dict[str, Any],
    constraints: list[dict[str, Any]],
) -> None:
    """Flag request keys that every constraint entry requires.

    Computes the intersection of keys present in every entry; any
    key in that intersection that is not in the request (and isn't
    universal) is reported as missing. Catches the common
    "you forgot to set `experiment` for CMIP6" class of error.
    """
    if not constraints:
        return
    required: set[str] = set(constraints[0])
    for entry in constraints[1:]:
        required &= set(entry)
    required -= _UNIVERSAL_KEYS
    missing = sorted(required - set(request))
    if missing:
        raise ValueError(
            f"Request for {dataset!r} is missing required key(s): "
            f"{missing}\n"
            f"Live constraints: "
            + CONSTRAINTS_URL_TEMPLATE.format(dataset=dataset)
        )


def validate_request(dataset: str, request: dict[str, Any]) -> None:
    """Validate `request` against the dataset's `constraints.json`.

    Runs in five phases (cheap → expensive); stops at the first
    failure so the user gets the most specific error possible:

    1. Date sanity (`year`/`month`/`day` form a real date).
    2. Area bbox sanity (`[north, west, south, east]` bounds).
    3. Variable name spell-check (with close-match suggestions).
    4. Required-field check (every key present in every constraint
       entry must be in the request).
    5. Full combinatorial check against the constraints document.

    Walks the cached constraints document looking for at least one
    entry whose allowed-value sets cover every key in `request`
    that the document enumerates. Universal keys
    (:data:`_UNIVERSAL_KEYS` — `area` / `data_format` / etc.)
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
        - The check is a no-op when `E2O_SKIP_CONSTRAINTS` is
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
    # Phase 1-2: cheap local sanity checks. Run before any network
    # call so a typo gets flagged in milliseconds.
    _validate_date_validity(request)
    _validate_area(request)
    constraints = fetch_constraints(dataset)
    if not constraints:
        return  # Nothing to validate against
    # Phase 3-4: per-key checks against the cached constraints.
    _validate_variable_typos(dataset, request, constraints)
    _validate_required_fields(dataset, request, constraints)
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
