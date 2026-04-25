# Migrating earth2observe ECMWF backend from `ecmwf-api-client` to `cdsapi`

> Status: planning. Captured from a session analysis on 2026-04-25 against
> `src/earth2observe/ecmwf.py`, `ecmwf2_old.py`, `ecmwf_data_catalog.yaml`,
> and `cds_data_catalog.yaml` on branch `build/use-pixi`.

## TL;DR

The old **ECMWF Web API** (`ecmwf-api-client` → `ECMWFDataServer`) had its
public-datasets service decommissioned on **1 June 2023**. Datasets like
`interim`, `era40`, `era20c`, `cera_sat`, `tigge`, `s2s` listed in the existing
`ecmwf_data_catalog.yaml` are no longer reachable via that API. The replacement
is **`cdsapi >= 0.7.7`** talking to the Copernicus Climate Data Store at
`https://cds.climate.copernicus.eu/api`. ERA-Interim itself was retired in 2019;
its replacement is ERA5.

## Background & Context

This section exists so future readers (humans or future LLM sessions) can pick
up the plan without rebuilding the surrounding context from scratch.

### What `earth2observe` is

A Python package that wraps several remote-sensing data providers behind a
single unified API:

| Backend | Class | Source | Auth |
|---------|-------|--------|------|
| CHIRPS  | `CHIRPS` (`src/earth2observe/chirps.py`) | UCSB FTP | none |
| Amazon S3 / ERA5 | `S3` (`src/earth2observe/s3.py`) | AWS public bucket `era5-pds` | unsigned |
| ECMWF / CDS | `ECMWF` (`src/earth2observe/ecmwf.py`) — **the subject of this plan** | Copernicus CDS via cdsapi | `~/.cdsapirc` PAT |
| Google Earth Engine | `gee/` subpackage | GEE | service-account key |

The `Earth2Observe` facade in `src/earth2observe/earth2observe.py` maps a
string key (e.g. `"ecmwf"`) to one of those backends. Each backend extends
`AbstractDataSource` (`src/earth2observe/abstractdatasource.py`).

### Why this migration exists — the timeline

| Date | Event |
|------|-------|
| 2019 | ECMWF retires ERA-Interim. ERA5 is the production successor. |
| 2023-06-01 | ECMWF Public Datasets service decommissioned. The old `ecmwf-api-client`, the `~/.ecmwfapirc` file, and the `https://api.ecmwf.int/v1` endpoint stop returning data for `interim`, `era20c`, `era40`, `cera_sat`, etc. |
| 2024-09-26 | Legacy CDS Toolbox discontinued. Not migrated to the new CDS. |
| ~2024 | New CDS infrastructure ("Common Data Store Engine") launched. `cdsapi >= 0.7.x` required. The CDS-Beta endpoint was later promoted to the default at `https://cds.climate.copernicus.eu/api`. |
| 2026-04-25 | This plan written. `cdsapi >= 0.7.7` is the recommended client; `ecmwf-datastores-client` is published but the CDS team explicitly say users do not need to migrate yet (tracked as `L1`). |

The reason this plan exists in one sentence: the `ECMWF` backend was originally
written against the *first* row of that table — ECMWF Public Datasets via
`ecmwf-api-client`. Everything between rows 2 and 4 invalidated those
assumptions.

### Key terminology

- **MARS** — ECMWF's old internal data archive. The `ecmwf-api-client` package
  spoke a MARS-flavoured request format (`param=130.128`, `grid=0.125/0.125`,
  `area=N/W/S/E`, `levtype=sfc`, `stream=oper`). Most of the dead code in
  `ecmwf.py` and `ecmwf2_old.py` is MARS-shaped.
- **CDS** — Climate Data Store. The Copernicus-run replacement service. Hosts
  ERA5, ERA5-Land, derived products. Accessed via `cdsapi`.
- **ADS** — Atmosphere Data Store. Sister service to CDS, same API, different
  datasets (atmospheric composition, air quality). Not used by this package
  yet.
- **ECDS** — ECMWF Data Store. Newer umbrella, will eventually be served by
  `ecmwf-datastores-client`. Out of scope; tracked as `L1`.
- **PAT** — Personal Access Token. Replaces the old `key` + `email` + `url`
  triple for cdsapi auth. Lives in `~/.cdsapirc`.
- **ERA5** vs **ERA-Interim** — Both are reanalysis products from ECMWF.
  ERA-Interim is retired; ERA5 is the successor and is what every CDS dataset
  in this plan refers to.

### State of the code at the time of analysis

The migration is not zero — it's partial:

- `src/earth2observe/ecmwf.py` *imports* `cdsapi` (line 9) and constructs
  `cdsapi.Client()` in `initialize()` (line 108). Everything else in the file
  is still MARS-shaped and broken (see `C1`, `C2`, `H1`–`H3`).
- `src/earth2observe/ecmwf2_old.py` is dead code — duplicates the old
  MARS-style request building and imports `from ecmwfapi import ECMWFDataServer`
  (`H4`).
- `src/earth2observe/ecmwf_data_catalog.yaml` is the old MARS catalog (`H6`).
- `src/earth2observe/cds_data_catalog.yaml` exists but is shaped wrong for the
  lookup pattern the code wants — it lists CDS datasets as keys with arrays of
  variable strings, instead of variable-codes as keys with metadata (`H5`).
- `pyproject.toml` lists neither `cdsapi` nor `ecmwf-api-client` (`H7`); the
  ECMWF backend cannot be installed cleanly today.

### How this plan relates to surrounding work

This plan was written on branch `build/use-pixi`, which had already:

- Migrated packaging from `setup.py`/`pip` to `pixi`
- Migrated docs from Sphinx/RST/ReadTheDocs to MkDocs/GitHub Pages
- Overhauled GitHub Actions workflows (added `tests.yml`, `wheel-test.yml`,
  `github-pages-mkdocs.yml`; rewrote release workflows)
- Added Python 3.13 / 3.14 support

The cdsapi migration is intentionally a **separate stream of work** and not
folded into the pixi PR (#24). It changes runtime behaviour (network calls,
auth model, dataset names) and deserves its own PR with its own review.

### Constraints to respect

- **Python ≥ 3.11** (per `pyproject.toml` `requires-python`). Don't introduce
  syntax that needs newer.
- **`Earth2Observe` facade interface stable.** Users calling
  `Earth2Observe(data_source="ecmwf", ...)` should keep working — argument
  names, defaults, and ordering are public API.
- **`AbstractDataSource` interface stable.** The CHIRPS, S3, and GEE backends
  rely on the same parent class; do not change its signatures while fixing
  ECMWF.
- **Tests must run offline in CI.** Mock cdsapi (`M4`); never hit the live
  service from CI.
- **No new runtime deps without justification.** `cdsapi` is in; resist adding
  `xarray` / `cfgrib` / etc. just to make NetCDF post-processing prettier —
  the existing `netCDF4` + `numpy` is the established pattern.

### Files involved at a glance

| File | Role | Touched by |
|------|------|------------|
| `src/earth2observe/ecmwf.py` | Main ECMWF backend | `C1`, `C2`, `H1`, `H2`, `H3`, `L4` |
| `src/earth2observe/ecmwf2_old.py` | Dead code | `H4` (delete) |
| `src/earth2observe/ecmwf_data_catalog.yaml` | Old MARS catalog | `H6` (delete) |
| `src/earth2observe/cds_data_catalog.yaml` | New catalog (in progress) | `H5` (restructure) |
| `src/earth2observe/abstractdatasource.py` | Parent class — read-only here | — |
| `pyproject.toml` | Dependency declaration | `H7` |
| `pixi.lock` | Auto-regenerated | regenerate after `H7` |
| `docs/authentication.md` | User-facing auth recipe | `M1`, `L2`, `L3` |
| `docs/catalog.md` | Catalog API examples | `M2` |
| `docs/data-sources.md` | Per-backend usage | `M3` |
| `tests/test_ecmwf.py` | Backend tests | `M4` |

## Current state of the migration in this repo

The migration was started but is **half-done and broken**. Concrete defects in
`src/earth2observe/ecmwf.py`:

| ID | Line(s) | Problem |
|----|---------|---------|
| —    | 9 | imports `cdsapi` — good |
| `H3` | 102–115 | `initialize()` calls `cdsapi.Client()` — good — but the `KeyError` rationale and error message still mention old `ECMWF_API_URL/KEY/EMAIL` env vars; cdsapi reads `~/.cdsapirc` (Personal Access Token) instead |
| `H2` | 162, 169 | `download()` calls `Catalog().get_dataset(var)` — returns entries from the **old** `ecmwf_data_catalog.yaml` with MARS-style fields (`number_para`, `download type`) that have no place in a cdsapi request |
| `C1` | 211–248 | `api()` builds `param=130.128`, MARS-style `grid`, `area_str` — none of this is sent anywhere. The function never calls `client.retrieve(...)` |
| `C2` | 250–261 | `send_request(server)` calls `server.retrieve()` with **no arguments** — cdsapi's signature is `retrieve(dataset_name, request_dict, target_path)` |
| `H1` | 248 | references `self.server`; `AbstractDataSource` exposes the client as `self.client` (or `self._client`) |
| `H1` | 167, 242–243, 293 | uses `self.path` / `self.dates` — `AbstractDataSource` provides `self.root_dir` and `self.time["dates"]` |
| `L4` | 410 | `AuthenticationError` is referenced on line 110 *before* its definition; works at runtime only because it's only triggered inside a function |

`src/earth2observe/ecmwf2_old.py` still does `from ecmwfapi import ECMWFDataServer`
— that import will fail as soon as `ecmwf-api-client` is dropped from the
dependencies (which already happened in the latest `pyproject.toml`). (`H4`)

## What "replace ECMWF with cdsapi" actually means

It is not a one-line swap. The two APIs differ in **dataset names, request
shape, auth, and licensing**.

### 1. Authentication — rewrite `initialize()` and the docs page

| Old (ECMWF Web API) | New (cdsapi → CDS) |
|---|---|
| `~/.ecmwfapirc` with `url`, `key`, `email` | `~/.cdsapirc` with `url: https://cds.climate.copernicus.eu/api` and `key: <PAT>` |
| Account on `apps.ecmwf.int` | Account on `cds.climate.copernicus.eu` (Copernicus SSO) |
| Per-dataset license accept on the website | Same — but new licenses on each new CDS dataset must be accepted in the profile UI |

**Action** (`H3`, `M1`): drop the `ECMWF_API_URL/KEY/EMAIL` references in
`ecmwf.py:105–113` and in `docs/authentication.md`. Replace with cdsapi PAT
instructions and a link to <https://cds.climate.copernicus.eu/how-to-api>.

### 2. Dataset name mapping

The whole `ecmwf_data_catalog.yaml` is obsolete (`H6`). The `cds_data_catalog.yaml`
that was started has the right *direction* but is missing (`H5`):

- per-variable metadata that the post-processing step needs
  (units, file-name prefix, `factors_add` / `factors_mul`)
- the link from a user-friendly variable code (`E`, `T`, `2T`) to a CDS
  variable name (`evaporation`, `temperature`, `2m_temperature`) **and** dataset

| Old code | Old dataset | New dataset | New `variable` name |
|---|---|---|---|
| `E` (Evaporation) | `interim` | `reanalysis-era5-single-levels` | `evaporation` |
| `T` (Temperature) | `interim` (pressure level) | `reanalysis-era5-pressure-levels` | `temperature` |
| `2T` (2m Temp) | `interim` | `reanalysis-era5-single-levels` | `2m_temperature` |
| `TP` (Total precip) | `interim` | `reanalysis-era5-single-levels` | `total_precipitation` |
| `SP` (Surf pressure) | `interim` | `reanalysis-era5-single-levels` | `surface_pressure` |
| `era40`, `era20c`, `cera20c`, `cera_sat` | retired or non-public via this API | — | drop from catalog |
| `tigge`, `s2s` | moved out of public datasets in 2023 | — | drop from catalog |

For `download()` to know which CDS dataset to hit per variable, the catalog
entries need a `cds_dataset:` field, e.g.:

```yaml
2T:
  cds_dataset: reanalysis-era5-single-levels
  cds_variable: 2m_temperature
  units: C
  file_name: Tair
  factors_add: -273.15
  factors_mul: 1

T:
  cds_dataset: reanalysis-era5-pressure-levels
  cds_variable: temperature
  cds_pressure_level: ["1000"]   # extra knob for pressure-level datasets
  units: C
  file_name: Tair2m
  factors_add: -273.15
  factors_mul: 1
```

### 3. Request shape — rewrite `api()` and `send_request()`

The new `client.retrieve()` takes a dict, not URL-encoded strings. Rough
sketch of the replacement:

```python
def api(self, var_info: dict) -> Path:
    dataset = var_info["cds_dataset"]
    request = {
        "product_type": ["reanalysis"],
        "variable": [var_info["cds_variable"]],
        "year":  sorted({d.year  for d in self.time["dates"]}),
        "month": sorted({f"{d.month:02d}" for d in self.time["dates"]}),
        "day":   sorted({f"{d.day:02d}"   for d in self.time["dates"]}),
        "time":  ["00:00", "06:00", "12:00", "18:00"],   # daily / six-hourly
        "data_format": "netcdf",
        "area": [                                         # N, W, S, E
            self.space["lat_lim"][1], self.space["lon_lim"][0],
            self.space["lat_lim"][0], self.space["lon_lim"][1],
        ],
    }
    if "cds_pressure_level" in var_info:
        request["pressure_level"] = var_info["cds_pressure_level"]

    target = self.root_dir / f"{var_info['file_name']}_{dataset}.nc"
    self.client.retrieve(dataset, request, str(target))
    return target
```

For monthly data, swap to the `*-monthly-means` dataset and use
`product_type: "monthly_averaged_reanalysis"` instead of `time:` — the
monthly datasets don't take a `time` key the same way. (`M5`)

### 4. Pin `cdsapi` and drop the dead dep (`H7`)

The latest `pyproject.toml` removed both `cdsapi` and `ecmwf-api-client`.
The runtime deps need:

```toml
dependencies = [
    ...
    "cdsapi >=0.7.7",
    # remove: ecmwf-api-client
]
```

### 5. Other changes that fall out

| ID   | File | Action |
|------|------|--------|
| `H4` | `src/earth2observe/ecmwf2_old.py` | **Delete** — imports `ecmwfapi` which is no longer a dep |
| `H6` | `src/earth2observe/ecmwf_data_catalog.yaml` | **Delete** — replaced by the redesigned `cds_data_catalog.yaml` |
| `H5` | `src/earth2observe/cds_data_catalog.yaml` | **Restructure** — give each variable code a single entry with `cds_dataset` / `cds_variable` / unit-conversion metadata, instead of a per-dataset list |
| `M1` | `docs/authentication.md` | Replace the `.ecmwfapirc` instructions and the `ECMWF_API_URL/KEY/EMAIL` env vars with `.cdsapirc` PAT instructions |
| `M2` | `docs/catalog.md` | Update the ECMWF section to describe CDS dataset short names |
| `M3` | `docs/data-sources.md` | Note that `interim` is gone; ERA5 is the default |
| `M4` | `tests/test_ecmwf.py` | Mock `cdsapi.Client.retrieve`; remove anything that exercised `ecmwfapi.ECMWFDataServer` |

### 6. Things worth knowing but not blockers

- (`L1`) **`ecmwf-datastores-client`** is the *next* official client (CDS team
  are recommending it for advanced users), but they explicitly say *"users are
  not requested to migrate at this time"* — sticking with `cdsapi >= 0.7.7` is
  fine for now.
- (`L2`) **Queue times** on CDS are minutes-to-hours, not seconds. Tests must
  not rely on a synchronous round-trip — `client.retrieve(...)` blocks until
  the request completes server-side. Skip them in CI or mock the call.
- (`L3`) **Licenses** must be accepted once per dataset on
  <https://cds.climate.copernicus.eu/profile>, otherwise `retrieve()` returns a
  permission error.

## Suggested order of work

1. Pin `cdsapi >= 0.7.7` in `pyproject.toml` and `[tool.pixi.dependencies]`;
   delete `ecmwf2_old.py`. (`H7`, `H4`)
2. Redesign `cds_data_catalog.yaml` (one entry per variable code with
   `cds_dataset` + `cds_variable` + unit fields). Delete
   `ecmwf_data_catalog.yaml`. (`H5`, `H6`)
3. Rewrite `ecmwf.py`: fix `initialize()` error message, replace `api()` to
   build a real request dict, replace `send_request()` with
   `self.client.retrieve(dataset, request, target)`, fix `self.path` /
   `self.dates` references, fix `Catalog.get_dataset` to return the new schema.
   (`C1`, `C2`, `H1`, `H2`, `H3`, `M5`, `L4`)
4. Update `docs/authentication.md`, `docs/catalog.md`, `docs/data-sources.md`.
   (`M1`, `M2`, `M3`)
5. Mock the cdsapi client in `tests/test_ecmwf.py`. (`M4`)

## Sources

Every URL below was fetched on **2026-04-25** while writing this plan. The
notes describe what each source contributed and which task IDs depend on it.

### Web

- **[CDSAPI setup — Copernicus Climate Data Store](https://cds.climate.copernicus.eu/how-to-api)**
  — authoritative how-to for the cdsapi client. Confirmed:
  - Recommended minimum version is `cdsapi >= 0.7.7`.
  - Endpoint URL is `https://cds.climate.copernicus.eu/api`.
  - Auth is a Personal Access Token in `~/.cdsapirc`.
  - Basic call shape: `client.retrieve(dataset, request, target)`.
  - The CDS team explicitly say users do not need to migrate to
    `ecmwf-datastores-client` *"at this time"*.
  Used directly by `H3` (error message), `M1` (auth doc rewrite), `H7`
  (version pin), `L1` (monitoring).

- **[`ecmwf/cdsapi` on GitHub](https://github.com/ecmwf/cdsapi)**
  — source repository for the cdsapi package. Confirmed:
  - Latest release `0.7.7` (2025-09-30).
  - 15 total releases — old versions exist but should not be pinned.
  - Canonical retrieve signature is positional:
    `(dataset_name, request_dict, output_filepath)`.
  - Example dataset name `reanalysis-era5-pressure-levels` matches the names
    in the rewritten `cds_data_catalog.yaml` (`H5`).
  Used by `C1` and `C2` (correct call signature) and `H5` (dataset names).

- **[CDS and ADS migrating to new infrastructure (Copernicus Knowledge Base)](https://confluence.ecmwf.int/x/uINmFw)**
  — ECMWF / Copernicus migration tracker. Source for:
  - Legacy CDS Toolbox discontinued **2024-09-26**.
  - New CDS infrastructure ("Common Data Store Engine") replacing the old one.
  - cdsapi syntax notes: *"some keys or parameter names may have also changed
    for some new CDS datasets"*.
  Used by the timeline table in *Background & Context* and as the watch URL
  for `L1`.

- **[ECMWF APIs FAQ — ECMWF Forum](https://forum.ecmwf.int/t/ecmwf-apis-faq-api-data-documentation/6880)**
  — forum FAQ that aggregates the API-deprecation timeline. Source for the
  timeline table, in particular:
  - ECMWF Public Datasets service decommissioned **2023-06-01**.
  - Multi-model datasets (`s2s`, `tigge`) migrated separately later that year.
  - Confirms `interim`, `era20c`, `cera_sat`, etc. are no longer reachable
    via the old API. Used by `H6` (delete the old catalog).

- **[ECMWF Web API home](https://confluence.ecmwf.int/display/WEBAPI)**
  — reference for the *old* API surface. Used to verify that the MARS-style
  fields in `ecmwf_data_catalog.yaml` (`number_para`, `download type`,
  `levtype`, `param`, `grid`, `area`, `stream`, `type`) belong to the
  deprecated path and have no analogue in a cdsapi request dict — which is
  what motivates `H6`.

### Local source code (examined directly, not fetched)

- `src/earth2observe/ecmwf.py` — every line range in the defects table at the
  top of this document is from a fresh read of this file. Lines 9, 102–115,
  162, 167, 169, 211–248, 250–261, 248, 242–243, 293, 410.
- `src/earth2observe/ecmwf2_old.py` — confirmed
  `from ecmwfapi import ECMWFDataServer` is still present at line 13.
- `src/earth2observe/abstractdatasource.py` — confirmed parent class exposes
  `self.client` (property over `self._client`), `self.root_dir`, and
  `self.time` (a dict with `start_date`, `end_date`, `time_freq`, `dates`).
  Authoritative for the renames in `H1`.
- `src/earth2observe/cds_data_catalog.yaml` — confirmed current shape is
  *dataset → list of variables* rather than the *variable → metadata* shape
  the new code path needs. Authoritative for `H5`.
- `src/earth2observe/ecmwf_data_catalog.yaml` — confirmed entries still carry
  MARS-only fields (`number_para`, `download type`). Authoritative for `H6`.
- `pyproject.toml` (lines 29–41) — confirmed neither `cdsapi` nor
  `ecmwf-api-client` is in `[project.dependencies]`. Authoritative for `H7`.

## Task Details

Each task below is identified by the `<Letter><Number>` ID used in the prose
above and tracked at the bottom of the file. Read this section if you want the
full picture of a single task without scrolling between the defects table, the
prose, and the tracker.

### `C1` — `api()` never calls `client.retrieve()`

**What.** The `api()` method (`ecmwf.py:211–248`) builds MARS-style strings —
`param=130.128`, `grid=0.125/0.125`, `area=N/W/S/E` — and then calls
`self.send_request(self.server)` (which itself does nothing useful, see `C2`).
At no point does it touch `cdsapi`. The downloaded file is never produced.

**Why it matters.** This is the entire ECMWF download path. Until `api()`
fires a real `client.retrieve(dataset, request, target)`, no ERA5 data ever
reaches disk and every test exercising this backend fails with a missing-file
error.

**Where.** `src/earth2observe/ecmwf.py:211–248`.

**Fix.** Replace the body of `api()` with the dict-based pattern shown in
section *3. Request shape* of this document. Concretely: receive `var_info`,
look up `cds_dataset` and `cds_variable`, build a `request` dict with
`product_type`, `variable`, `year`/`month`/`day`, `time`, `data_format`,
`area`, optional `pressure_level`, then call
`self.client.retrieve(dataset, request, str(target))` and return `target`.

**Acceptance.** Calling `Earth2Observe(data_source="ecmwf", ...).download()`
for a registered variable produces a NetCDF file at
`<root_dir>/<file_name>_<dataset>.nc`.

**Depends on.** `H1` (correct attribute names), `H2`/`H5` (catalog returns
the new schema), `H7` (cdsapi installed).

---

### `C2` — `send_request()` calls `server.retrieve()` with no arguments

**What.** `send_request(server)` is a static helper that just calls
`server.retrieve()` with zero arguments. The cdsapi signature is
`retrieve(dataset_name, request_dict, target_path)` — three positional
arguments are required.

**Why it matters.** Even after `C1` is fixed, this helper would still raise
`TypeError: retrieve() missing 3 required positional arguments`. It is a
direct artifact of the old `ECMWFDataServer.retrieve(request_dict)` flow that
no longer applies.

**Where.** `src/earth2observe/ecmwf.py:250–261`.

**Fix.** The cleanest path is to delete `send_request()` entirely and inline
`self.client.retrieve(...)` into `api()` (per `C1`). If you prefer to keep a
helper, give it the right signature:

```python
@staticmethod
def send_request(client, dataset: str, request: dict, target: str) -> None:
    client.retrieve(dataset, request, target)
```

**Acceptance.** No reference to `server.retrieve()` with zero arguments
anywhere in the file; `pytest -k ecmwf` no longer raises `TypeError` for
missing `retrieve` arguments.

**Depends on.** `H1` (uses `self.client`, not `self.server`).

---

### `H1` — Replace `self.path`, `self.dates`, `self.server` with the right names

**What.** The class extends `AbstractDataSource`. The parent exposes:

- `self.client` — the data-source client (cdsapi here)
- `self.root_dir` — the output directory (a `Path`)
- `self.time` — a dict containing `start_date`, `end_date`, `time_freq`,
  `dates`

The current code references `self.path`, `self.dates`, and `self.server`
instead. None of those exist on the parent, so every line that touches them
raises `AttributeError` at runtime.

**Why it matters.** Even the most trivial method call
(`download()` → `logger.info(...)`) blows up before reaching any cdsapi logic.

**Where.** `ecmwf.py`:

- Line 167 — `self.time["start_date"]` / `self.time["end_date"]` are correct,
  but earlier sketches used `self.dates` for the date list
- Lines 242–243 — uses `self.space["lat_lim"]` / `self.space["lon_lim"]` —
  these are correct already
- Line 248 — `self.server` (does not exist; use `self.client`)
- Line 293 — `self.path` (use `self.root_dir`)
- Date loop in `post_download()` — `self.dates` (use `self.time["dates"]`)

**Fix.** Mechanical rename:

| Wrong          | Right                |
|----------------|----------------------|
| `self.path`    | `self.root_dir`      |
| `self.dates`   | `self.time["dates"]` |
| `self.server`  | `self.client`        |

**Acceptance.** `grep -nE "self\.(path|dates|server)" src/earth2observe/ecmwf.py`
returns no matches.

---

### `H2` — Stop returning old MARS-schema entries from `Catalog.get_dataset()`

**What.** `Catalog.get_catalog()` (line 399) reads `ecmwf_data_catalog.yaml`,
which contains entries shaped for the old MARS API: `number_para: 130`,
`download type: 1`, `var_name: t`, `factors_add`, `factors_mul`. The new
download path needs a different shape: `cds_dataset`, `cds_variable`, plus the
unit-conversion fields.

**Why it matters.** `download()` calls `catalog.get_dataset(var)` and threads
the result into `api()` and `post_download()`. With the wrong-shape dict every
downstream `var_info["cds_dataset"]` lookup raises `KeyError`.

**Where.** `ecmwf.py:399–407`, `ecmwf_data_catalog.yaml`,
`cds_data_catalog.yaml`.

**Fix.** Make `Catalog.get_catalog()` read `cds_data_catalog.yaml` and make
`get_dataset(var_name)` return the new-schema dict. Concretely:

```python
def get_catalog(self):
    with open(f"{__path__[0]}/cds_data_catalog.yaml") as fh:
        return yaml.safe_load(fh).get("variables", {})

def get_dataset(self, var_name):
    return self.catalog[var_name]
```

**Acceptance.** `Catalog().get_dataset("2T")` returns a dict containing
`cds_dataset`, `cds_variable`, `units`, `file_name`, `factors_add`,
`factors_mul`.

**Depends on.** `H5` (the new catalog must exist with the right shape),
`H6` (old catalog removed so it can't be picked up by accident).

---

### `H3` — Update `initialize()` error message to point at `~/.cdsapirc`

**What.** `initialize()` wraps `cdsapi.Client()` in a `try/except KeyError`
and raises `AuthenticationError` mentioning `ECMWF_API_URL`, `ECMWF_API_KEY`,
`ECMWF_API_EMAIL`. Two problems:

1. cdsapi reads its config from `~/.cdsapirc`, **not** from those env vars.
2. cdsapi does not raise `KeyError` on missing config — it raises a generic
   `Exception` (with a message about the missing/incomplete configuration
   file) or successfully constructs a Client that fails on the first
   `retrieve()` call.

**Why it matters.** Users who hit the error message will set three env vars
that have no effect, then come back convinced the package is broken.

**Where.** `ecmwf.py:102–115`.

**Fix.** Catch the actual exception (`Exception` is fine for this narrow
scope; the message check matters more than the type) and update the message:

```python
def initialize(self):
    try:
        client = cdsapi.Client()
    except Exception as exc:  # cdsapi raises a generic Exception here
        raise AuthenticationError(
            "cdsapi could not authenticate. Create ~/.cdsapirc with:\n"
            "    url: https://cds.climate.copernicus.eu/api\n"
            "    key: <YOUR-PERSONAL-ACCESS-TOKEN>\n"
            "See https://cds.climate.copernicus.eu/how-to-api"
        ) from exc
    return client
```

**Acceptance.** Running with no `~/.cdsapirc` raises `AuthenticationError`
whose message mentions `~/.cdsapirc` and links to
`https://cds.climate.copernicus.eu/how-to-api`.

---

### `H4` — Delete `src/earth2observe/ecmwf2_old.py`

**What.** This module subclasses the new `ECMWF` and overrides `api()` to
build the old MARS request — which it then sends with
`from ecmwfapi import ECMWFDataServer`. Once `ecmwf-api-client` is gone from
the dependency list, that import will fail at module load.

**Why it matters.** Pytest collects everything under `tests/` and any test
file that imports `earth2observe.ecmwf2_old` will hard-fail collection,
breaking the whole test run. Even unimported, the file is dead weight.

**Where.** `src/earth2observe/ecmwf2_old.py` (entire file).

**Fix.** `git rm src/earth2observe/ecmwf2_old.py`. Then verify nothing else
references it:

```bash
grep -rn "ecmwf2_old\|ECMWFNew" src tests docs
```

**Acceptance.** File no longer exists; the grep above returns no matches;
`pytest --collect-only` does not raise `ModuleNotFoundError: No module named
'ecmwfapi'`.

---

### `H5` — Restructure `cds_data_catalog.yaml` to a per-variable lookup

**What.** The current file lists CDS datasets as keys, each with arrays of
`variable` strings. That is a per-dataset listing; the download code needs to
go the **other** direction — start from a user-friendly variable code (`E`,
`T`, `2T`) and find which dataset hosts it, plus its CDS variable name and
unit-conversion metadata.

**Why it matters.** Without a per-variable entry, `Catalog.get_dataset(var)`
has nothing useful to return, and `api()` cannot know which dataset to query.

**Where.** `src/earth2observe/cds_data_catalog.yaml`.

**Fix.** Rewrite the file as a flat `variables:` map. Keep a `datasets:` index
for documentation. Example:

```yaml
datasets:
  - reanalysis-era5-single-levels
  - reanalysis-era5-single-levels-monthly-means
  - reanalysis-era5-pressure-levels
  - reanalysis-era5-pressure-levels-monthly-means
  - reanalysis-era5-land
  - reanalysis-era5-land-monthly-means

variables:
  E:
    cds_dataset: reanalysis-era5-single-levels
    cds_dataset_monthly: reanalysis-era5-single-levels-monthly-means
    cds_variable: evaporation
    units: mm
    file_name: Evaporation
    factors_add: 0
    factors_mul: 1000
  2T:
    cds_dataset: reanalysis-era5-single-levels
    cds_dataset_monthly: reanalysis-era5-single-levels-monthly-means
    cds_variable: 2m_temperature
    units: C
    file_name: Tair
    factors_add: -273.15
    factors_mul: 1
  T:
    cds_dataset: reanalysis-era5-pressure-levels
    cds_dataset_monthly: reanalysis-era5-pressure-levels-monthly-means
    cds_variable: temperature
    cds_pressure_level: ["1000"]
    units: C
    file_name: Tair2m
    factors_add: -273.15
    factors_mul: 1
  TP:
    cds_dataset: reanalysis-era5-single-levels
    cds_variable: total_precipitation
    units: mm
    file_name: Precipitation
    factors_add: 0
    factors_mul: 1000
  SP:
    cds_dataset: reanalysis-era5-single-levels
    cds_variable: surface_pressure
    units: kPa
    file_name: SurfPressure
    factors_add: 0
    factors_mul: 0.001
```

**Acceptance.**
`yaml.safe_load(open("cds_data_catalog.yaml"))["variables"]["2T"]["cds_dataset"]`
equals `"reanalysis-era5-single-levels"`, and at least the five variables
above are populated.

**Depends on.** Nothing — but `H2`, `H6`, `M2`, `M5` all build on this.

---

### `H6` — Delete `src/earth2observe/ecmwf_data_catalog.yaml`

**What.** This is the old MARS-style catalog (`T`, `2T`, `SRO`, … →
`number_para`, `download type`, `var_name`). After `H2` and `H5` make
`Catalog` read from `cds_data_catalog.yaml`, the old file is unreferenced.

**Why it matters.** Two catalog files for the same backend invites bit-rot.
A future reader will not know which one is authoritative and may "fix" the
wrong one.

**Where.** `src/earth2observe/ecmwf_data_catalog.yaml`.

**Fix.** `git rm src/earth2observe/ecmwf_data_catalog.yaml`. Also remove the
`earth2observe = ["*.yaml"]` package-data entry's reliance on the old name if
anything spelled it out (it currently uses a glob, so nothing to do there).

**Acceptance.** `grep -rn ecmwf_data_catalog src tests docs` returns nothing.

**Depends on.** `H2`, `H5` (the new catalog must be in place and wired up
*before* the old one is removed, or imports break).

---

### `H7` — Pin `cdsapi >= 0.7.7` and drop `ecmwf-api-client`

**What.** The latest `pyproject.toml` lists neither `cdsapi` nor
`ecmwf-api-client` in `[project.dependencies]`. The code imports `cdsapi` at
module top-level (`ecmwf.py:9`).

**Why it matters.** `pip install earth2observe` in a clean environment will
succeed, then crash on the first
`from earth2observe.ecmwf import ECMWF` with `ModuleNotFoundError`. End-users
have no way to know they need to install `cdsapi` separately.

**Where.** `pyproject.toml:29–41` (`dependencies` array). Possibly also
`[tool.pixi.dependencies]` and `pixi.lock` (run `pixi update` after editing).

**Fix.** Add `cdsapi >=0.7.7` to `dependencies`. Make sure
`ecmwf-api-client` is **not** present:

```toml
dependencies = [
    "boto3 >=1.26.50",
    "cdsapi >=0.7.7",
    "earthengine-api >=0.1.324",
    "joblib >=1.2.0",
    "loguru >=0.7.2",
    "netCDF4 >=1.6.1",
    "numpy >=2.1.3",
    "pandas >=2.2.3",
    "pyramids-gis >=0.7.1",
    "PyYAML >=6.0.2",
    "requests >=2.28.1",
    "serapeum_utils >=0.1.1",
]
```

Then refresh the lockfile:

```bash
pixi lock
```

**Acceptance.** `pip install -e .` resolves cdsapi >= 0.7.7;
`python -c "import earth2observe.ecmwf"` succeeds in a fresh virtualenv.

---

### `M1` — Update `docs/authentication.md` for `~/.cdsapirc` PAT

**What.** The page tells users to create `~/.ecmwfapirc`, get an API key from
`https://api.ecmwf.int/v1/key/`, and set `ECMWF_API_URL`, `ECMWF_API_KEY`,
`ECMWF_API_EMAIL`. None of that works for CDS.

**Why it matters.** Users who follow the docs verbatim end up with a working
`.ecmwfapirc` file that the new code completely ignores, then hit a
`AuthenticationError` whose error message still references the same dead env
vars (until `H3` lands). Two layers of misdirection.

**Where.** `docs/authentication.md` — the entire ECMWF section.

**Fix.** Replace the ECMWF section with a CDS recipe:

1. Register at <https://cds.climate.copernicus.eu/>
2. Visit <https://cds.climate.copernicus.eu/profile> and copy the Personal
   Access Token (PAT)
3. Create `~/.cdsapirc` (Windows: `C:\Users\<USER>\.cdsapirc`) containing:
   ```
   url: https://cds.climate.copernicus.eu/api
   key: <YOUR-PERSONAL-ACCESS-TOKEN>
   ```
4. On the CDS website, accept the terms of use for each dataset you intend to
   download (one-time, per-dataset; see `L3`)

Replace the old `images/ecmwf_key.png` reference with a CDS-profile screenshot
or remove it.

**Acceptance.** A reader following the page produces a working `~/.cdsapirc`
that authenticates against CDS without setting any env vars.

---

### `M2` — Update `docs/catalog.md` to describe the new schema

**What.** The page still shows the old ECMWF catalog example with
`'datasets': ['cams_gfas', 'cera20c', 'interim', ...]` and per-variable
entries with `download type: 3`, `number_para: 130`. After `H5`, the catalog
is shaped completely differently.

**Why it matters.** Users copying the example output will believe the package
returns dicts with `download type` keys that no longer exist, and write
client code against a phantom schema.

**Where.** `docs/catalog.md` — the ECMWF subsection.

**Fix.** Replace the example with output from the new schema:

```python
from earth2observe.ecmwf import Catalog

cat = Catalog()
cat.get_dataset("2T")
# {
#     'cds_dataset': 'reanalysis-era5-single-levels',
#     'cds_variable': '2m_temperature',
#     'units': 'C',
#     'file_name': 'Tair',
#     'factors_add': -273.15,
#     'factors_mul': 1,
# }
```

List the supported CDS dataset short names and link to
<https://cds.climate.copernicus.eu/datasets?q=era5> for discovery.

**Acceptance.** The Python snippets on the page run against the new catalog
unmodified and produce the documented output.

**Depends on.** `H5`.

---

### `M3` — Update `docs/data-sources.md` to drop ERA-Interim

**What.** The page describes downloading ERA-Interim via the ECMWF backend
and uses `dataset="interim"` examples. ERA-Interim was retired in 2019; the
public-datasets service hosting it was decommissioned in 2023.

**Why it matters.** Misleading users — there is no ERA-Interim to download.
They'll burn time investigating "why does my interim request hang".

**Where.** `docs/data-sources.md` — the ECMWF subsection.

**Fix.** Replace ERA-Interim mentions with ERA5. Update the example:

```python
e2o = Earth2Observe(
    data_source="ecmwf",
    temporal_resolution="daily",
    start="2022-01-01",
    end="2022-01-31",
    variables=["2T"],   # 2-metre temperature
    lat_lim=[4.19, 4.64],
    lon_lim=[-75.65, -74.73],
    path="examples/data/era5",
)
e2o.download()
```

Add a paragraph noting CDS queue times (see `L2`).

**Acceptance.** No mention of "ERA Interim" or `interim` remains in the page
or the rendered MkDocs site.

---

### `M4` — Mock `cdsapi.Client.retrieve` in tests

**What.** A real CDS request waits in a queue (minutes to hours), then
streams a possibly-large NetCDF file. Tests cannot exercise this path
directly — the timeout would be unbearable and the user's CDS quota would be
consumed by every CI run.

**Why it matters.** Without a mock, tests either become slow and flaky, or
get marked `@pytest.mark.skip` and stop catching regressions. Either way the
ECMWF backend ends up untested.

**Where.** `tests/test_ecmwf.py`.

**Fix.** Use `pytest-mock` (matches the pyramids pattern) or
`unittest.mock.patch` to:

1. Replace `cdsapi.Client.__init__` so it doesn't try to read `~/.cdsapirc`
2. Replace `cdsapi.Client.retrieve` with a stub that writes a minimal,
   syntactically-valid NetCDF file to the target path

Sketch:

```python
def test_download_ecmwf(tmp_path, monkeypatch):
    def fake_retrieve(self, dataset, request, target):
        Path(target).write_bytes(MINIMAL_NETCDF_BYTES)

    monkeypatch.setattr(cdsapi.Client, "__init__", lambda self: None)
    monkeypatch.setattr(cdsapi.Client, "retrieve", fake_retrieve)
    ...
```

**Acceptance.** `pytest tests/test_ecmwf.py` passes in a CI runner that has
neither `~/.cdsapirc` nor network access.

**Depends on.** `C1`, `C2` (the code being tested needs to actually exercise
the cdsapi call path first).

---

### `M5` — Handle the monthly request shape

**What.** For `temporal_resolution="monthly"`, the dataset name has a
`-monthly-means` suffix and the request needs
`product_type: "monthly_averaged_reanalysis"` (or
`monthly_averaged_reanalysis_by_hour_of_day`) instead of the daily-style
`time: ["00:00", "06:00", "12:00", "18:00"]`.

**Why it matters.** A daily-shaped request against a `*-monthly-means`
dataset returns 400 or — worse — silently returns a different product than
the user expects. Either way the ERA5 monthly path doesn't actually work.

**Where.** `ecmwf.py:api()` (after `C1`/`H5`), plus the `cds_dataset_monthly`
field added to the catalog in `H5`.

**Fix.** Branch on `self.temporal_resolution` inside `api()`:

```python
if self.temporal_resolution == "monthly":
    dataset = var_info.get("cds_dataset_monthly", var_info["cds_dataset"])
    request["product_type"] = ["monthly_averaged_reanalysis"]
    request.pop("time", None)
else:
    dataset = var_info["cds_dataset"]
    request["product_type"] = ["reanalysis"]
    request["time"] = ["00:00", "06:00", "12:00", "18:00"]
```

**Acceptance.** With a mocked client (per `M4`), a monthly download routes
to a `*-monthly-means` dataset with `product_type: monthly_averaged_reanalysis`
and no `time` key; a daily download keeps `time` and uses the non-monthly
counterpart.

**Depends on.** `H5`, `C1`, `M4`.

---

### `L1` — Watch for the `ecmwf-datastores-client` migration

**What.** ECMWF is rolling out `ecmwf-datastores-client` as the long-term
successor to `cdsapi`. As of early 2026, the CDS team explicitly says
*"users are not requested to migrate at this time"*.

**Why it matters.** When CDS announces a deprecation date for `cdsapi`, we
will want a deliberate migration plan, not a fire drill.

**Where.** Nowhere yet — this is monitoring.

**Fix.** Subscribe to the
[CDS migration knowledge-base entry](https://confluence.ecmwf.int/x/uINmFw)
and the [ECMWF API forum](https://forum.ecmwf.int/c/api/12). When CDS
announces a migration deadline, file a follow-up issue with `ecmwf-datastores-client`
specifics.

**Acceptance.** Nothing actionable today; revisit when CDS announces.

---

### `L2` — Document CDS queue times in the testing/auth docs

**What.** A real CDS request waits in a queue: typically minutes, sometimes
hours, depending on dataset size and load. `client.retrieve()` blocks the
entire time.

**Why it matters.** A user running an example for the first time will think
the program has hung. Saves a Slack/issue thread to mention this once in the
docs.

**Where.** `docs/authentication.md` (or a new "Testing" subsection).

**Fix.** Add a paragraph along the lines of: *"CDS queues each request
server-side. `client.retrieve()` blocks until the request reaches the front
of the queue and the file is generated — typically seconds to many minutes,
occasionally hours for large requests. In CI, mock the client (see
`tests/test_ecmwf.py`); locally, expect to wait."*

**Acceptance.** A reader of the docs knows to expect wait times and where
the mock pattern lives.

---

### `L3` — Document per-dataset license acceptance

**What.** Every CDS dataset has an independent terms-of-use that the user
must accept once on <https://cds.climate.copernicus.eu/profile>.
`client.retrieve()` returns 403 with *"Required licences not accepted"* until
they do.

**Why it matters.** First-time users hit this and cannot tell whether their
PAT is wrong or the dataset access is gated. The error message from cdsapi
mentions licenses but is easy to miss.

**Where.** `docs/authentication.md`.

**Fix.** Add a step to the auth recipe (per `M1`): *"Each CDS dataset has
its own terms of use. Visit your CDS profile and accept the licenses for the
datasets you plan to use — this is one-time, per-dataset."* Optionally link
the specific licenses for ERA5.

**Acceptance.** Documented; no other code change.

---

### `L4` — Move `AuthenticationError` definition above its first reference

**What.** `class AuthenticationError(Exception)` is defined at the bottom of
`ecmwf.py` (line 410), but it is referenced inside `initialize()` at line
110. Python resolves names at call time so this works at runtime, but it is
fragile and makes the file harder to read top-to-bottom.

**Why it matters.** If anything ever calls `initialize` at module-import
time (for example, a class-level decorator or a metaclass that pre-warms),
the reference will resolve to nothing. It is also a code smell flagged by
some linters.

**Where.** `ecmwf.py:410` (move it to just below the imports).

**Fix.** Cut the class definition from the bottom and paste it directly
after the `from earth2observe.abstractdatasource import ...` line.

**Acceptance.**
`grep -n "class AuthenticationError" src/earth2observe/ecmwf.py` returns a
line number lower than every `grep -n "raise AuthenticationError"` line.

---

## Issue Tracker

| ID   | Status | Title                                                                                                           |
|------|--------|-----------------------------------------------------------------------------------------------------------------|
| `C1` | Closed | `api()` never calls `client.retrieve()` — request is never sent                                                 |
| `C2` | Open   | `send_request()` calls `server.retrieve()` with no arguments                                                    |
| `H1` | Open   | Replace `self.path` / `self.dates` / `self.server` with `self.root_dir` / `self.time["dates"]` / `self.client`  |
| `H2` | Open   | Stop returning old MARS-schema catalog entries from `Catalog.get_dataset()`                                     |
| `H3` | Open   | Update `initialize()` error message to point at `~/.cdsapirc` PAT instead of `ECMWF_API_URL/KEY/EMAIL`          |
| `H4` | Open   | Delete `ecmwf2_old.py` (imports `ecmwfapi` which is no longer a dep)                                            |
| `H5` | Open   | Restructure `cds_data_catalog.yaml` to one entry per variable with `cds_dataset` / `cds_variable` / unit fields |
| `H6` | Open   | Delete obsolete `ecmwf_data_catalog.yaml`                                                                       |
| `H7` | Open   | Pin `cdsapi >= 0.7.7` in `pyproject.toml` and `[tool.pixi.dependencies]`                                        |
| `M1` | Open   | Update `docs/authentication.md` for `.cdsapirc` PAT instructions                                                |
| `M2` | Open   | Update `docs/catalog.md` to describe CDS dataset short names                                                    |
| `M3` | Open   | Update `docs/data-sources.md` to drop ERA-Interim and default to ERA5                                           |
| `M4` | Open   | Mock `cdsapi.Client.retrieve` in `tests/test_ecmwf.py`                                                          |
| `M5` | Open   | Handle monthly request shape (`*-monthly-means` + `product_type: monthly_averaged_reanalysis`)                  |
| `L1` | Open   | Watch `ecmwf-datastores-client` as the eventual successor to `cdsapi`                                           |
| `L2` | Open   | Document that CDS queue times require mocking or skipping in CI                                                 |
| `L3` | Open   | Document per-dataset license acceptance on the CDS profile page                                                 |
| `L4` | Open   | Move `AuthenticationError` definition above its first reference in `ecmwf.py`                                   |
