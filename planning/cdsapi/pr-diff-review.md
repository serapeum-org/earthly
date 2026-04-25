# PR Diff Review — `refactor` vs `main`

Range reviewed: `c44d655..HEAD` (13 commits, the cdsapi migration). Files
read: `src/earth2observe/{abstractdatasource.py, ecmwf.py, cds_data_catalog.yaml,
earth2observe.py, chirps.py, s3.py}`, `tests/test_ecmwf.py`,
`pyproject.toml`, `environment.yml`, `docs/examples/{authentication,catalog,
data-sources}.md`. Lockfile and the unrelated `HISTORY.rst` deletion were
spot-checked only.

# Summary

- The api() rewrite (C1), Catalog rewiring (H2/H5), parent-class wiring
  (H1), monthly branch (M5), and offline mock harness (M4) are sound in
  isolation. Tests are well-targeted and cover request shape, schema,
  and error paths thoroughly (43 passing, 1 e2e skipped).
- **The end-to-end claim is overstated.** `Earth2Observe(data_source="ecmwf",
  ...).download()` cannot work today because (a) the facade does not
  register ECMWF at all, (b) `ECMWF.download()` references `self.variables`
  which does not exist (parent stores `self.vars`), and (c) line 225
  contains an f-string with nested double-quotes that is a `SyntaxError`
  on Python 3.11 — the lowest version `pyproject.toml` claims to support.
- `post_download()` was not updated as part of this migration: it still
  reads from a `data_<dataset>.nc` filename that `api()` no longer
  produces and looks up MARS-schema keys (`var_name`, `file name` with a
  space, `types`) that the new catalog does not carry. So even after the
  three Critical issues are fixed, the post-processing step will fail.
- CHIRPS and S3 are unaffected by the parent-class changes; the opt-in
  `if isinstance(...)` capture in `AbstractDataSource.__init__` and the
  `self.path = self.root_dir` alias preserve their behaviour. Verified by
  importing both and checking attribute usage.

# Findings

## Critical

### `C1` — ECMWF is not registered in the `Earth2Observe` facade

**File:** `src/earth2observe/earth2observe.py:1-12`

`Earth2Observe.DataSources = {"chirps": CHIRPS, "amazon-s3": S3}`. ECMWF is
neither imported nor mapped, so `Earth2Observe(data_source="ecmwf", ...)`
raises `ValueError: ecmwf not supported` immediately in `__init__`
(line 49). This contradicts the migration plan's stated constraint that
the facade interface remains stable, and the new docs (`docs/examples/
data-sources.md`, the api() / download_dataset() docstring examples)
all use the facade — every one of those examples fails today.

**Suggested fix:** Add the import and registration:

```python
from earth2observe.ecmwf import ECMWF
...
DataSources = {"chirps": CHIRPS, "amazon-s3": S3, "ecmwf": ECMWF}
```

Then add at least one integration test in `tests/test_ecmwf.py` (or a
new `tests/test_earth2observe.py`) that exercises
`Earth2Observe(data_source="ecmwf", ...)` with the cdsapi mock to
prevent regression.

### `C2` — f-string nested quotes break Python 3.11 import

**File:** `src/earth2observe/ecmwf.py:225`

```python
f"Download ECMWF {var} data for period {self.time["start_date"]} till {self.time["end_date"]}"
```

The nested double-quotes inside an f-string require PEP 701, which only
landed in Python **3.12**. `pyproject.toml` declares
`requires-python = ">= 3.11, <4"` and the pixi config ships a `py311`
environment in the test matrix. Verified directly:

```
$ pixi run -e py311 python -c "import earth2observe.ecmwf"
SyntaxError: f-string: unmatched '['
```

CI on 3.11 will fail at collection time. The package cannot be installed
on 3.11 environments at all.

**Suggested fix:** swap the inner quotes — for example
`{self.time['start_date']}` — or split into intermediate locals:

```python
start = self.time["start_date"]
end = self.time["end_date"]
logger.info(f"Download ECMWF {var} data for period {start} till {end}")
```

### `C3` — `ECMWF.download()` iterates a non-existent attribute

**File:** `src/earth2observe/ecmwf.py:222`

```python
for var in self.variables:
```

`AbstractDataSource.__init__` stores the user-supplied list as
`self.vars`, not `self.variables`. Verified across the package — only
ECMWF uses the wrong name; S3 (`s3.py:133`) correctly uses `self.vars`.
On the first call to `download()`, this raises `AttributeError` before
any catalog or cdsapi work.

This is independent of `C2`: even on Python 3.12 where the file imports
cleanly, calling `download()` blows up immediately.

**Suggested fix:** either rename to `self.vars` here, or rename
`AbstractDataSource.__init__`'s `self.vars = variables` to
`self.variables = variables` and fix the S3 reference too. The latter is
clearer and matches the constructor argument name.

## High

### `H1` — `post_download()` reads from the old MARS-style filename

**File:** `src/earth2observe/ecmwf.py:499`

```python
NC_filename = os.path.join(self.root_dir, f"data_{dataset}.nc")
```

`api()` (post-C1) writes the NetCDF to
`<root_dir>/<file_name>_<cds_dataset>.nc` — for example
`Tair_reanalysis-era5-single-levels.nc`. `post_download` then tries to
open `data_<dataset>.nc` (e.g. `data_interim.nc` from the legacy default
of `download_dataset(dataset="interim")`), which does not exist.

**Suggested fix:** thread the target path returned by `api()` through
`download_dataset()` into `post_download()`. `api()` already returns the
path; the caller just discards it:

```python
target = self.api(var_info)
self.post_download(var_info, target, progress_bar)
```

### `H2` — `post_download()` looks up MARS-schema keys absent from the new catalog

**File:** `src/earth2observe/ecmwf.py:515-518, 583, 586`

```python
parameter_var = var_info.get("var_name")        # always None
Var_unit       = var_info.get("units")           # OK
factors_add    = var_info.get("factors_add")     # OK
factors_mul    = var_info.get("factors_mul")     # OK
...
if var_info.get("types") == "flux":              # always False
var_output_name = var_info.get("file name")      # always None — note the SPACE
```

The post-H5 catalog uses `file_name` (underscore, not space) and does not
ship `var_name` or `types` at all. `parameter_var` therefore goes to
`fh.variables[None]`, which raises. `var_output_name` ends up as `None`
in the output filename. `types == "flux"` never fires, so flux unit
conversion silently disappears.

**Suggested fix:**

* Replace `"var_name"` with `var_info["cds_variable"]` (and accept that
  the NetCDF variable name CDS returns may differ from the request
  variable — check against an actual ERA5 NetCDF before relying on it).
* Replace `"file name"` with `"file_name"`.
* Either reintroduce a `types` field on flux variables in the catalog,
  or hard-code the flux multiplication for the variables known to be
  fluxes (E, RO, SRO, SSRO, TP).

### `H3` — `download()` unconditionally deletes a hardcoded `data_interim.nc`

**File:** `src/earth2observe/ecmwf.py:228-230`

```python
del_ecmwf_dataset = os.path.join(self.root_dir, "data_interim.nc")
os.remove(del_ecmwf_dataset)
```

This runs after the variable loop completes successfully and tries to
delete a file the new flow never creates. On Linux it raises
`FileNotFoundError`; on Windows it raises `WindowsError`. Either way,
the entire download is reported as failed even when the per-variable
calls succeeded.

The deletion is also outside the loop, so deleting one shared file
doesn't make sense in the new per-variable layout regardless.

**Suggested fix:** delete these two lines. If cleanup of intermediate
NetCDFs is desired, do it per-variable inside `download_dataset()` after
`post_download()` succeeds, using the path `api()` returned.

## Medium

### `M1` — `download()`'s `dataset` parameter is dead

**File:** `src/earth2observe/ecmwf.py:206-228`

`download(dataset="interim", ...)` threads `dataset` into
`download_dataset()` which threads it into `post_download()` for the
filename. With C1+H5 the actual CDS dataset is per-variable
(`var_info["cds_dataset"]`); the parameter is misleading. Worse, its
default `"interim"` perpetuates the very dataset name the migration was
meant to remove.

**Suggested fix:** drop the parameter from `download` /
`download_dataset` / `post_download`. Update the `Earth2Observe` facade
call sites accordingly.

### `M2` — `post_download()` docstring is still numpy-style with MARS-schema example

**File:** `src/earth2observe/ecmwf.py:485-498`

The docstring shows `var_info` as

```python
>>> {
>>>     'descriptions': 'Evaporation [m of water]',
>>>     ...
>>>     'download type': 2,
>>>     'number_para': 182,
>>>     'var_name': 'e',
>>>     ...
>>> }
```

None of those keys exist in the new catalog. The doctest blocks (each
line starts with `>>>`) also break `python -m doctest src/earth2observe/
ecmwf.py` at module level (verified during C1 generate-docstring run).

**Suggested fix:** convert to Google style and update the example to the
new catalog shape, in the same pass that fixes H1 / H2.

### `M3` — `_ConcreteECMWF` test workaround is now redundant

**File:** `tests/test_ecmwf.py:42-66`

After H1 added the `API` (uppercase) stub directly to `ECMWF`, the
`_ConcreteECMWF` test subclass is no longer needed —
`ECMWF.__new__(ECMWF)` works fine. The fixture's docstring still claims
"every backend implements `api` (lowercase) instead", which is no longer
true (ECMWF now has both).

**Suggested fix:** delete `_ConcreteECMWF` and replace
`_ConcreteECMWF.__new__(_ConcreteECMWF)` with `ECMWF.__new__(ECMWF)` in
the fixtures.

### `M4` — `download()` docstring still describes the legacy `dataset` parameter

**File:** `src/earth2observe/ecmwf.py:209-220`

The docstring says `dataset:[str] Default is "interim"`. Even if the
parameter stays for backwards compatibility (M1 fix not applied), the
description should at least note that ERA-Interim is gone and the value
is ignored for CDS routing.

**Suggested fix:** rewrite in Google style alongside the M1 cleanup.

## Low

### `L1` — `HISTORY.rst` deletion is unrelated to the cdsapi migration

**File:** `HISTORY.rst`

The diff includes `HISTORY.rst` being deleted (40 lines). That change
was already staged on the branch when the migration started; it is not
mentioned in `planning/cdsapi/migration-plan.md` and not authored by any
of the 13 cdsapi commits.

**Suggested fix:** if HISTORY.rst removal is intentional, land it in a
separate PR with its own justification (changelog moved to
`docs/change-log.md`?). Otherwise, restore the file. Either way, it
shouldn't appear in the cdsapi PR.

### `L2` — `chirps.py` / `s3.py` docstrings remain numpy-style

**File:** `src/earth2observe/chirps.py`, `src/earth2observe/s3.py`

The new ECMWF docstrings are Google-style with runnable doctests; the
sibling backends still use the older numpy convention. Internal
inconsistency, low impact, but worth a follow-up for tooling alignment
(e.g. `pydocstyle --convention=google` would now flag the other
backends).

**Suggested fix:** out of scope for this PR; track as a separate issue.

### `L3` — `Earth2Observe` does not import ECMWF

**File:** `src/earth2observe/earth2observe.py:1-3`

A pre-existing omission, but the migration arguably should have caught
it given that the plan explicitly names the facade as the user-visible
entry point.

**Suggested fix:** add the import together with the registration in
`C1`.

### `L4` — `pixi.lock` adds ~2,700 line deltas to the diff

**File:** `pixi.lock`

Mechanical regeneration after dropping `ecmwf-api-client`, but it
overwhelms the substantive code review. Future PRs touching dependencies
might benefit from a separate "lockfile only" commit (already done here)
plus a PR description that calls the lockfile out as auto-generated.

**Suggested fix:** this PR already separates the lockfile change to its
own commit (`cc9fb06`) — that's good. Just call it out in the PR
description so reviewers know to skip the lockfile churn.

## Nit

### `N1` — Mixed docstring conventions inside `ecmwf.py`

**File:** `src/earth2observe/ecmwf.py`

`api`, `download_dataset`, `initialize`, `Catalog` (and its methods)
have new Google-style docstrings; `check_input_dates`, `create_grid`,
`download`, `post_download` are still numpy-style. Reading top to bottom
the style flips back and forth.

**Suggested fix:** finish the conversion in a follow-up; out of scope
here.

### `N2` — YAML keys in `cds_data_catalog.yaml` are unquoted scalars starting with digits

**File:** `src/earth2observe/cds_data_catalog.yaml:46, 70, 96, 112, 128, 138, 198`

Keys like `2T:`, `2D:`, `10U:`, `10V:`, `10SI:` are unquoted YAML
scalars. PyYAML resolves them to strings today, but quoting them as
`"2T":` would be defensive against future YAML 1.2 parsers and
clearer to a human reader.

**Suggested fix:** quote the digit-leading short codes.

### `N3` — Mock-harness safeguard error message could include the literal pattern

**File:** `tests/test_ecmwf.py:73-78`

```python
"Patch cdsapi.Client at the module level (see M4 harness) "
"or move the test into TestApiE2E with RUN_CDS_E2E=1."
```

The "M4 harness" reference forces the reader to chase a doc link.
Inlining the literal pattern would help:

```python
'Use monkeypatch.setattr(cdsapi, "Client", lambda: ...) '
'in your test, or move the test into TestApiE2E with RUN_CDS_E2E=1.'
```

# Tests

**Added:** `tests/test_ecmwf.py` — 43 unit tests across `TestApi`
(17), `TestApiMonthly` (5), `TestDownloadDataset` (2), `TestInitialize`
(4), `TestCatalog` (7), `TestParentClassWiring` (4) plus an autouse
safeguard fixture and one skipped e2e test (`TestApiE2E`).

**Coverage of the migration scope:**

* `api()` request shape, monthly branch, pressure-level forwarding,
  KeyError paths — comprehensive.
* `Catalog` schema, all five plan-mandated mappings (E/T/2T/TP/SP),
  no-MARS-key invariant, KeyError on unknown codes — comprehensive.
* `initialize()` happy path + three error-message invariants — solid.
* Parent-class wiring exercised end-to-end with a fake `cdsapi.Client`
  (TestParentClassWiring.test_api_works_directly_off_a_real_constructed_instance).

**Gaps that match the findings above:**

* No test exercises `Earth2Observe(data_source="ecmwf", ...)` —
  would have caught `C1`.
* No test runs under Python 3.11 — would have caught `C2`.
* No test calls `download()` end-to-end — would have caught `C3` and
  the post_download chain (`H1`, `H2`, `H3`).
* `post_download` is uncovered.
* `AbstractDataSource` itself has no direct tests; coverage is via
  ECMWF only. CHIRPS / S3 import-time smoke is verified manually but
  has no automated check.

**Recommended additions** (in order of payoff):

1. CI matrix coverage on Python 3.11 — let the existing 19+ tests run
   on the lowest supported version. This catches `C2` and any future
   PEP-701-only syntax.
2. An integration test through `Earth2Observe`:
   `Earth2Observe(data_source="ecmwf", ...).download()` with the
   cdsapi mock and `post_download` either stubbed or exercised. This
   catches `C1`, `C3`, and `H1`–`H3` together.
3. A direct test on `AbstractDataSource.__init__` using a minimal
   concrete subclass (independent of ECMWF), verifying the
   `if isinstance(...)` opt-in capture for each return value.
4. After `H1`/`H2` fixes land, smoke-test `post_download` with a
   minimal hand-crafted NetCDF to lock in the schema expectations.

# Questions and Assumptions

* **Was the facade wiring the migration's responsibility?** The plan's
  *Constraints to respect* section says the facade interface is stable,
  which I read as "it should keep working". The fact that it never
  worked for ECMWF on `main` either suggests the facade gap is older
  than this branch — but the migration doesn't fix it, and the new docs
  examples now visibly fail. Treating this as in-scope for the PR
  because the docs assume it works.
* **Is `post_download` deferred work?** The migration plan does not
  carry a tracker entry for fixing the legacy file naming or MARS-schema
  lookups in `post_download`. If that's intentional and tracked
  elsewhere, mark it as such; otherwise add `H1`/`H2`/`H3` to the plan.
* **Assumed:** `AbstractDataSource.__init__` ordering is correct — the
  `self.client` capture happens before `self.create_grid` which happens
  before `self.check_input_dates`. CHIRPS overrides set their own
  attributes inside `check_input_dates`; nothing depends on the order
  the parent stores them. Verified by reading both subclasses.
* **Assumed:** `cdsapi.Client` is a factory that returns either
  `Client` itself or `LegacyClient` based on token format. Confirmed
  in C1's investigation. The mock harness patches the factory at the
  module level for that reason.

# Residual Risks

After the Critical / High items are addressed:

* The new CDS NetCDF schema may differ from the old MARS NetCDF schema
  that `post_download` was written against — variable axis names,
  longitude wrapping (0–360 vs −180–180), time-axis epoch. None of
  these are validated by tests; first real e2e run is likely to expose
  more issues.
* `pixi` matrix runs Python 3.11/3.12/3.13/3.14 but coverage is
  effectively only the dev (3.14) env from local runs. Confirm the GH
  Actions workflow actually executes the per-version matrix and that
  the 3.11 SyntaxError surfaces there before merging.
* The `~/.cdsapirc` setup is documented but the package cannot
  enumerate accepted licences for the user; first live run will likely
  return 403 *"Required licences not accepted"* until the user clicks
  through each ERA5 dataset page. The docs warn about this; it is not
  preventable from code.
* `L1` in the migration plan (watch `ecmwf-datastores-client`) remains
  Open by design. No action required this PR; revisit when CDS
  announces a `cdsapi` deprecation.

# Issue Tracker

| ID   | Severity | State  | Description                                                                                   | File(s)                                                                        |
|------|----------|--------|-----------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| `C1` | Critical | Solved | ECMWF not registered in `Earth2Observe.DataSources`; facade rejects `data_source="ecmwf"`     | `src/earth2observe/earth2observe.py`                                           |
| `C2` | Critical | Solved | f-string nested quotes break Python 3.11 import (`SyntaxError: f-string: unmatched '['`)      | `src/earth2observe/ecmwf.py:225`                                               |
| `C3` | Critical | Solved | `ECMWF.download()` iterates `self.variables` but parent stores `self.vars` — `AttributeError` | `src/earth2observe/ecmwf.py:222`, `src/earth2observe/abstractdatasource.py:62` |
| `H1` | High     | Solved | `post_download()` reads `data_<dataset>.nc` but `api()` writes `<file_name>_<cds_dataset>.nc` | `src/earth2observe/ecmwf.py:499`                                               |
| `H2` | High     | Solved | `post_download()` reads obsolete MARS-schema keys (`var_name`, `file name`, `types`)          | `src/earth2observe/ecmwf.py:515-518, 583, 586`                                 |
| `H3` | High     | Solved | `download()` unconditionally deletes a hardcoded `data_interim.nc` after the loop             | `src/earth2observe/ecmwf.py:228-230`                                           |
| `M1` | Medium   | Solved | `download()`'s `dataset="interim"` parameter is dead post-migration                           | `src/earth2observe/ecmwf.py:206-228`                                           |
| `M2` | Medium   | Solved | `post_download()` docstring still numpy-style with MARS-schema example                        | `src/earth2observe/ecmwf.py:485-498`                                           |
| `M3` | Medium   | Solved | Test fixture `_ConcreteECMWF` is redundant after H1's `API` stub                              | `tests/test_ecmwf.py:42-66`                                                    |
| `M4` | Medium   | Solved | `download()` docstring still references the legacy `dataset` parameter                        | `src/earth2observe/ecmwf.py:209-220`                                           |
| `L1` | Low      | Closed | `HISTORY.rst` deletion is unrelated to the cdsapi migration                                   | `HISTORY.rst`                                                                  |
| `L2` | Low      | Open   | `chirps.py` / `s3.py` docstrings remain numpy-style — internal inconsistency                  | `src/earth2observe/chirps.py`, `src/earth2observe/s3.py`                       |
| `L3` | Low      | Solved | `Earth2Observe` module does not import ECMWF                                                  | `src/earth2observe/earth2observe.py:1-3`                                       |
| `L4` | Low      | Solved | `pixi.lock` regeneration adds ~2,700 line deltas to the diff                                  | `pixi.lock`                                                                    |
| `N1` | Nit      | Open   | Mixed docstring conventions inside `ecmwf.py` (Google vs numpy)                               | `src/earth2observe/ecmwf.py`                                                   |
| `N2` | Nit      | Solved | YAML keys in catalog use unquoted digit-leading scalars                                       | `src/earth2observe/cds_data_catalog.yaml`                                      |
| `N3` | Nit      | Solved | Mock-harness safeguard message could include the literal patch pattern                        | `tests/test_ecmwf.py:73-78`                                                    |
