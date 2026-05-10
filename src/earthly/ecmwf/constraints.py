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

Pass `skip=True` to :class:`RequestValidator` — or, equivalently,
construct :class:`earthly.ecmwf.ECMWF` with `skip_constraints=True` —
to bypass validation entirely. Useful when the constraints endpoint
is missing or known to be inaccurate for a particular dataset.

Examples:
    - Validate a request against ERA5 single-levels constraints:

        ```python
        >>> from earthly.ecmwf.constraints import RequestValidator
        >>> request = {
        ...     "variable": ["2m_temperature"],
        ...     "year": ["2022"],
        ...     "month": ["01"],
        ...     "day": ["01"],
        ...     "time": ["00:00"],
        ...     "product_type": ["reanalysis"],
        ... }
        >>> RequestValidator(  # doctest: +SKIP
        ...     "reanalysis-era5-single-levels", request,
        ... ).check()

        ```
"""

from __future__ import annotations

import datetime
import difflib
import itertools
import json
import urllib.error
import urllib.request
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

__all__ = [
    "Area",
    "CONSTRAINTS_URL_TEMPLATE",
    "Dates",
    "RequestValidator",
    "fetch_constraints",
]

CONSTRAINTS_URL_TEMPLATE = (
    "https://cds.climate.copernicus.eu/api/catalogue/v1/collections/"
    "{dataset}/constraints.json"
)

# Keys the constraints document never enumerates because they are
# universally accepted by the cdsapi front-end (geographic /
# format / output controls). Skipped during validation.
_UNIVERSAL_KEYS: frozenset[str] = frozenset({"area", "data_format", "format", "grid"})

# Keys CDS uses to partition a dataset's storage internally. A request
# whose values for these keys span multiple constraint entries is
# still accepted by CDS (it splits the retrieval), so the combinatorial
# check unions across consistent entries instead of demanding a single
# entry cover the whole request. See `_check_combinatorial`.
_TIME_PARTITION_KEYS: frozenset[str] = frozenset({"year", "month", "day"})

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
            >>> from earthly.ecmwf.constraints import fetch_constraints
            >>> entries = fetch_constraints(  # doctest: +SKIP
            ...     "reanalysis-era5-single-levels",
            ... )
            >>> entries[0].keys()  # doctest: +SKIP
            dict_keys(['variable', 'year', 'month', 'day', 'time', ...])
            >>> sorted(entries[0]["product_type"])  # doctest: +SKIP
            ['ensemble_mean', 'ensemble_members', 'reanalysis']

            ```
    """
    if dataset not in _CACHE:
        url = CONSTRAINTS_URL_TEMPLATE.format(dataset=dataset)
        if not url.startswith("https://"):
            raise ValueError(
                f"refusing to fetch constraints from non-https URL: {url!r}"
            )
        try:
            # Scheme validated above — bandit B310 (file:// / ftp://
            # vectors) does not apply, and we additionally rule out
            # plaintext http to defeat MITM-injected constraint
            # documents that could trick the validator.
            with urllib.request.urlopen(url, timeout=15) as resp:  # nosec B310
                payload = json.loads(resp.read())
        except (urllib.error.URLError, ValueError, OSError):
            # Network failure or non-JSON response — treat as
            # "no constraints" so callers fall back to letting CDS
            # itself reject the request.
            payload = None
        # Cache `None` for "fetched, unusable" so later calls are cheap.
        _CACHE[dataset] = payload if isinstance(payload, list) else None
    return _CACHE[dataset] or []


class Dates(BaseModel):
    """Year/month/day fields of a CDS request, validated for real dates.

    Public entry point is :meth:`Dates.check` — fails fast on
    obvious calendar mistakes (Feb 30, month=13, year=1492, …) before
    the request is sent.

    Lenient by design: values that are not integer-coercible (e.g.
    `year=["all"]`) are coerced to `None` and skipped during checks,
    not rejected. Extra request keys are ignored — the model only
    owns date validation.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    year: list[int | None] = Field(default_factory=list)
    month: list[int | None] = Field(default_factory=list)
    day: list[int | None] = Field(default_factory=list)

    @field_validator("year", "month", "day", mode="before")
    @classmethod
    def _wrap_and_coerce(cls, v: Any) -> list[int | None]:
        items = v if isinstance(v, list) else [v]
        coerced: list[int | None] = []
        for item in items:
            try:
                coerced.append(int(item))
            except (TypeError, ValueError):
                coerced.append(None)
        return coerced

    @model_validator(mode="after")
    def _check_dates(self) -> Dates:
        for label, raw, lo, hi in (
            ("year", self.year, 1850, 2100),
            ("month", self.month, 1, 12),
            ("day", self.day, 1, 31),
        ):
            for n in raw:
                if n is None or lo <= n <= hi:
                    continue
                if label == "year":
                    raise ValueError(f"year={n} outside the plausible 1850-2100 range")
                raise ValueError(f"{label}={n} must be {lo:02d}-{hi:02d}")
        # No cross-product check on (year, month, day): CDS accepts
        # exhaustive enumerations like `day=[01..31]` for a request
        # spanning months with fewer days, silently dropping the
        # non-existent combinations. Enforcing real-date semantics
        # here would falsely reject the request builder's standard
        # daily-resolution payload.
        return self

    @classmethod
    def check(cls, request: dict[str, Any]) -> None:
        """Validate `request`'s date fields; raise `ValueError` on failure.

        Thin wrapper that runs :meth:`model_validate` and translates
        pydantic's :class:`ValidationError` to :class:`ValueError`
        (matching the rest of this module's error contract).
        """
        try:
            cls.model_validate(request)
        except ValidationError as exc:
            raise ValueError(
                exc.errors()[0]["msg"].removeprefix("Value error, ")
            ) from None


class Area(BaseModel):
    """CDS `area` bbox validated against lat/lon bounds.

    Public entry point is :meth:`Area.check` — fails fast on swapped
    indices, out-of-range latitudes, or non-numeric values, before
    the request reaches CDS and burns a queue slot.
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

    @classmethod
    def check(cls, request: dict[str, Any]) -> None:
        """Validate `request['area']` if present; raise `ValueError` on failure.

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
            cls(north=area[0], west=area[1], south=area[2], east=area[3])
        except ValidationError as exc:
            first = exc.errors()[0]
            if "float" in first["type"]:
                raise ValueError(f"area values must be numeric: {area!r}") from None
            raise ValueError(first["msg"].removeprefix("Value error, ")) from None


class RequestValidator:
    """End-to-end pre-flight validator for a CDS retrieve request.

    Bundles every check that pre-flights a request to the Climate
    Data Store, in a fixed cheap-to-expensive order:

    1. Date sanity (`year` / `month` / `day` form a real date) — :class:`Dates`.
    2. Area bbox sanity (`[north, west, south, east]`) — :class:`Area`.
    3. Variable name spell-check (with close-match suggestions).
    4. Required-field check (every key present in every constraint
       entry must be in the request).
    5. Combinatorial cover check (request values must be a subset of
       at least one constraint entry's allowed values).

    Phases 1-2 are local and run unconditionally. Phases 3-5 walk the
    dataset's `constraints.json` (fetched lazily and cached). Each
    instance holds the dataset name, the request dict, and a lazy
    handle to the fetched constraints; the per-phase methods share
    that state instead of taking it as parameters.

    Pass `skip=True` to bypass every phase — useful when CDS's
    published `constraints.json` is known to be stale or wrong for
    the dataset, or when running offline.

    Example:
        ```python
        >>> from earthly.ecmwf.constraints import RequestValidator
        >>> RequestValidator(
        ...     "any-dataset", {"variable": ["x"]}, skip=True
        ... ).check()

        ```
    """

    def __init__(
        self,
        dataset: str,
        request: dict[str, Any],
        skip: bool = False,
    ) -> None:
        self.dataset = dataset
        self.request = request
        self.skip = skip
        self._constraints: list[dict[str, Any]] | None = None

    @property
    def constraints(self) -> list[dict[str, Any]]:
        """Lazily fetched (and cached) constraints document for `dataset`."""
        if self._constraints is None:
            self._constraints = fetch_constraints(self.dataset)
        return self._constraints

    def check(self) -> None:
        """Run every validation phase; raise `ValueError` on first failure.

        Honours the constructor's `skip` flag — when `True`, every
        phase is short-circuited and no validation runs.
        """
        if self.skip:
            return
        # Phase 1-2: cheap local sanity checks. Run before any network
        # call so a typo gets flagged in milliseconds.
        Dates.check(self.request)
        Area.check(self.request)
        if self.constraints:
            # Phase 3-5: cross-checks against the fetched constraints.
            self._check_variable_typos()
            self._check_required_fields()
            self._check_combinatorial()

    def _check_variable_typos(self) -> None:
        """Phase 3: suggest typo fixes when a requested variable is unknown.

        Walks `self.constraints` to collect every catalogued variable,
        then flags any request variable that is not in that set and
        offers up to 3 close matches via :func:`difflib.get_close_matches`.
        """
        raw = self.request.get("variable", [])
        requested = raw if isinstance(raw, list) else [raw]
        catalogued: set[str] = set()
        for entry in self.constraints:
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
                f"Request for {self.dataset!r} names unknown variable(s): "
                + "; ".join(parts)
                + "\nLive constraints: "
                + CONSTRAINTS_URL_TEMPLATE.format(dataset=self.dataset)
            )

    def _check_required_fields(self) -> None:
        """Phase 4: flag request keys that every constraint entry requires.

        Computes the intersection of keys present in every entry; any
        key in that intersection that is not in the request (and is
        not universal) is reported as missing. Catches the common
        "you forgot to set `experiment` for CMIP6" class of error.
        """
        required: set[str] = set(self.constraints[0])
        for entry in self.constraints[1:]:
            required &= set(entry)

        required -= _UNIVERSAL_KEYS
        missing = sorted(required - set(self.request))
        if missing:
            raise ValueError(
                f"Request for {self.dataset!r} is missing required key(s): "
                f"{missing}\n"
                f"Live constraints: "
                + CONSTRAINTS_URL_TEMPLATE.format(dataset=self.dataset)
            )

    def _check_combinatorial(self) -> None:
        """Phase 5: walk constraints for entries covering all enumerated keys.

        For each non-time key the constraints document enumerates
        (variable, product_type, level_type, ...) the request's
        value(s) must be a subset of *some single entry's* allowed
        values. For the time-partition keys (`year`, `month`, `day`),
        the request's values must be a subset of the *union* of the
        allowed values across the entries that satisfy the non-time
        keys — this matches CDS's actual behaviour, which silently
        splits cross-partition requests across its internal storage
        chunks (e.g. `reanalysis-era5-land-monthly-means` partitions
        by `month` Jan-Apr / May-Dec but accepts requests spanning
        both halves).
        """
        constraint_keys: set[str] = set().union(
            *(set(entry) for entry in self.constraints)
        )
        req_norm = self._normalise_request(constraint_keys)
        if not req_norm:
            return
        non_time = {k: v for k, v in req_norm.items() if k not in _TIME_PARTITION_KEYS}
        time_part = {k: v for k, v in req_norm.items() if k in _TIME_PARTITION_KEYS}

        # Only consider entries that fully cover the non-time keys.
        candidates = [
            entry
            for entry in self.constraints
            if all(k in entry and non_time[k] <= set(entry[k]) for k in non_time)
        ]
        time_ok = True
        if candidates and time_part:
            for k, values in time_part.items():
                union_k: set[Any] = set()
                for entry in candidates:
                    if k in entry:
                        union_k.update(entry[k])
                if not values <= union_k:
                    time_ok = False
                    break

        if not candidates or not time_ok:
            bad_keys = self._find_offending_values(req_norm)
            raise ValueError(
                f"Request for {self.dataset!r} does not match any "
                f"constraint entry. Offending values: "
                f"{', '.join(bad_keys) or '(unclear)'}\n"
                f"Submitted request: {self.request}\n"
                f"Live constraints: "
                + CONSTRAINTS_URL_TEMPLATE.format(dataset=self.dataset)
            )

    def _normalise_request(self, constraint_keys: set[str]) -> dict[str, set[Any]]:
        """Return per-key value sets for keys the constraints enumerate.

        Drops universal keys and any key the constraints document does
        not mention. Scalars are wrapped to a single-element set so
        the combinatorial walk can do uniform `<=` subset comparisons.
        """
        norm: dict[str, set[Any]] = {}
        for key, value in self.request.items():
            if key in _UNIVERSAL_KEYS or key not in constraint_keys:
                continue
            norm[key] = set(value) if isinstance(value, list) else {value}
        return norm

    def _find_offending_values(self, req_norm: dict[str, set[Any]]) -> list[str]:
        """Per key, list the values absent from *every* constraint entry.

        Used when the combinatorial walk finds no covering entry, so
        the error message can name exactly which extras to fix.
        """
        bad_keys: list[str] = []
        for key, values in req_norm.items():
            seen: set[Any] = set()
            for entry in self.constraints:
                if key in entry:
                    seen.update(entry[key])
            missing = values - seen
            if missing:
                bad_keys.append(f"{key}={sorted(missing)!r}")
        return bad_keys
