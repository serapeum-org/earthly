# Catalog & tooling

The CHC backend is **catalog-driven end-to-end**. No FTP path,
filename pattern, region bound, or per-variable unit is hardcoded
in `backend.py`. This page documents the catalog's on-disk shape,
the validation it enforces at load, the `health()` self-report, and
the tooling that supports catalog work.

## At a glance

| File / tool | Role |
|---|---|
| `src/earthlens/chc/catalog/_index.yaml` | The informational `available_datasets:` walk-order list + the `regions:` block (named geographic-coverage profiles) |
| `src/earthlens/chc/catalog/<family>.yaml` | 8 per-family files (CHIRPS-2.0, CHIRPS v3, CHIRP, CHIRTS, GEFS, CMIP6, indices, derived) carrying the actual `datasets:` blocks |
| `src/earthlens/chc/catalog.py` | The loader (`Catalog`, `Dataset`, `Variable`, `_build_chc_dataset`, `_load_catalog_data`, `_StrictSafeLoader`) |
| `src/earthlens/chc/backend.py` | The `CHIRPS` class — consumes the catalog through `Dataset.ftp_bases` / `Dataset.file_patterns` / `Dataset.discrete_files` |
| `tools/chc/refresh_chc_catalog.py` | Walks `data.chc.ucsb.edu` and regenerates the `available_datasets:` index |
| `tools/chc/audit_chc_datasets.py` | Coverage / staleness classifier (parallel of `tools/gee/audit_gee_datasets.py`) |
| `tools/chc/probe_chirps_gefs.py` | CHIRPS-GEFS FTP probe — was used to verify the v3 file patterns before they were withdrawn |

## Layout — per-family split

The catalog is a **directory of per-family YAML files**, mirroring
the GEE-style layout under `src/earthlens/gee/catalog/`:

```
src/earthlens/chc/catalog/
├── _index.yaml              # 145 lines — available_datasets: + regions:
├── chirps-2.0.yaml          # 27 datasets — CHIRPS-2.0 global/regional/preliminary
├── chirps-v3.yaml           # 11 datasets — CHIRPS v3 (final + preliminary)
├── chirp.yaml               # 7 datasets — CHIRP + CHIRP v3
├── chirts.yaml              # 7 datasets — CHIRTSdaily + CHIRTSmonthly
├── gefs.yaml                # 9 datasets — CHIRPS-GEFS v12
├── indices.yaml             # 15 datasets — SPI/CHIRPS3 + SPEI v1
├── cmip6.yaml               # 16 datasets — CHC_CMIP6 scenario deltas
└── derived.yaml             # 5 datasets — CHPclim v2, WBGT, CentennialTrends v1
```

Family-file names are **not load-bearing** — the loader walks
`*.yaml` in the directory and merges every `datasets:` block into one
dict. Family files exist for editorial clarity (a maintainer touching
WBGT opens `derived.yaml`, not a 5000-line monolith). Dataset keys
must be **unique across files**; a collision raises `ValueError`
naming both filenames.

`Catalog.load()` accepts both the directory shape (canonical) and a
legacy single-file YAML for backwards compatibility / tests. The
loader dispatches on `path.is_dir()`.

## `_index.yaml`

Two top-level blocks, no `datasets:`:

```yaml
available_datasets:
  - global-daily
  - global-monthly
  - africa-daily
  - chirts-daily-tmax
  - wbgt-monthly
  - centennial-trends-v1-monthly
  - chc-cmip6-precip-daily-delta-2030-ssp245
  # … 97 entries total

regions:
  global:
    lat_boundaries: [-50, 50]
    lon_boundaries: [-180, 180]
  global-land:
    lat_boundaries: [-60, 70]
    lon_boundaries: [-180, 180]
  global-extended:
    lat_boundaries: [-90, 90]
    lon_boundaries: [-180, 180]
  africa:
    lat_boundaries: [-40, 40]
    lon_boundaries: [-20, 55]
  central-america-caribbean:
    lat_boundaries: [5, 35]
    lon_boundaries: [-120, -55]
  east-africa:
    lat_boundaries: [-12, 6]
    lon_boundaries: [28, 42]
  east-africa-centennial:                # CenTrends-specific wider extent
    lat_boundaries: [-12.25, 22.25]
    lon_boundaries: [21.25, 51.25]
  indonesia:
    lat_boundaries: [-11, 6]
    lon_boundaries: [95, 141]
  western-hemisphere:
    lat_boundaries: [-50, 50]
    lon_boundaries: [-180, 0]
```

The `regions:` block is the **single source of truth for spatial
bounds**. Every dataset references a region by name; the loader
resolves `ds.region` → `regions[ds.region]` → `(lat_boundaries,
lon_boundaries)` and pins those on the `Dataset` instance. A dataset
that carries **inline** `lat_boundaries` / `lon_boundaries` is
rejected with a `ValueError` pointing at the regions block: pick an
existing region or add a new entry. This stops the drift case where a
region rename in `_index.yaml` silently disagrees with stale inline
bounds on N datasets.

## Per-family file structure

A per-family `<family>.yaml` carries a `datasets:` block; each entry
follows this schema:

### Schema skeleton

```yaml
datasets:

  <dataset_key>:                                  # one block per dataset

    ftp_bases:                                    # REQUIRED — format-keyed FTP dirs
      tif: pub/org/chc/products/CHIRPS-2.0/...    #   relative to the FTP root
      cog: pub/org/chc/products/CHIRPS-2.0/...    #   additional formats as available
      netcdf: ...

    file_patterns:                                # REQUIRED for per-date datasets
      tif: "{year}/chirps-v2.0.{year}.{month}.{day}.tif.gz"   # one template per format
      cog: ...                                    #   placeholders expanded by _placeholders()

    discrete_files:                               # XOR with file_patterns — fixed list
      tif:
      - CHPclim2.90-90.01.tif                     #   12 files, one per climatological month
      - CHPclim2.90-90.02.tif
      # …

    region: <name>                                # REQUIRED — must exist in regions:

    temporal_resolution: <Literal>                # REQUIRED — see vocabulary below
    pandas_freq: <alias>                          # REQUIRED — validated via to_offset
    spatial_resolution: [<deg>]                   # REQUIRED — pixel size in degrees
    formats: [<fmt>, ...]                         # REQUIRED — must match ftp_bases keys

    start_date: <YYYY-MM-DD>                      # REQUIRED — inclusive
    end_date: <YYYY-MM-DD>                        # optional — None for ongoing products
    preliminary: <bool>                           # optional — defaults to false

    variables:                                    # REQUIRED — per-variable map
      <variable_code>:
        description: <string>                     #   short human-readable description
        units: <string>                           #   unit string (e.g. "mm/day")
        types: <Literal>                          #   "flux" or "state"
```

### Required vs optional

- `ftp_bases`, `region`, `temporal_resolution`, `pandas_freq`,
  `spatial_resolution`, `formats`, `start_date`, `variables` —
  REQUIRED. Omission raises `ValidationError` at load.
- Exactly **one** of `file_patterns` / `discrete_files` must be set
  (enforced by a `model_validator(mode="after")` on `Dataset`).
- `end_date`, `preliminary` — OPTIONAL.

The model is frozen (`ConfigDict(frozen=True, extra="forbid")`) so a
typo'd field name raises rather than silently filing under the wrong
slot.

## `temporal_resolution` vocabulary

Constrained to a 14-entry `Literal[...]` over the
`_TEMPORAL_RESOLUTIONS` tuple:

```
10-day              5-day               annual
15-day              6-hourly            daily
2-monthly           daily-delta         dekadal
3-monthly           monthly             monthly-climatology
                    pentadal            seasonal
```

A typo (e.g. `"daly"`) raises `ValidationError` at load with the
literal vocabulary in the message. Adding a new cadence is a
two-line edit: add to the tuple AND to the `Literal[...]` annotation
on `Dataset.temporal_resolution`. `Catalog.list_datasets(temporal_resolution=...)`
also validates the argument against the same tuple.

## `pandas_freq`

Every dataset's `pandas_freq` is validated at load via
`pd.tseries.frequencies.to_offset(value)`. Catches:

- Typos (`"daly"`, `"montly"`).
- Deprecated aliases (pandas 2.2 deprecated `"H"` for `"h"`;
  pandas 3.x removed `"AS"` outright — see the H3 commit on this
  branch that swapped every `"AS"` for `"YS"`).
- Non-string values.

The error message points at the [pandas offset alias table](https://pandas.pydata.org/docs/user_guide/timeseries.html#offset-aliases).
Discrete-files datasets carry a placeholder `pandas_freq`; the
check still runs (the placeholder must be a legal alias).

## `file_patterns` vs `discrete_files`

A dataset publishes its bytes in one of two shapes — never both. The
discriminator is structural, not a separate field:

| Shape | Field | Backend path |
|---|---|---|
| Per-date partitions (the common case) | `file_patterns: {fmt: template}` | `_download_dataset` iterates `pd.date_range(start, end, freq=pandas_freq)`, calls `_placeholders(date, pandas_freq)` per date, formats the template, fetches over FTP |
| Fixed archive files (CHPclim, CenTrends) | `discrete_files: {fmt: [filename, ...]}` | `_download_discrete` iterates the filename list once per request, no date substitution |

Placeholders the backend's `_placeholders()` expands:

- `{year}`, `{month}`, `{day}` — calendar position
- `{dekad}` (`1`/`2`/`3`) — third of the month
- `{pentad}` (`1`..`6`) — fifth of the month
- `{hour}` — `00`–`23`
- `{doy}` — Julian day-of-year (3-digit, zero-padded)
- `{start_yyyymmdd}` / `{end_yyyymmdd}` — period-window endpoints
  derived from `pandas_freq` (WBGT)

`{month_pair}` (CHIRPS v3 2-monthly) and `{res}` / `{scale}` are not
implemented; a row using them would silently hit the per-date
KeyError-skip path. None of the shipped rows use them today.

## `Catalog.health()`

A structural-hygiene self-report. Most schema invariants are caught
at load time; `health()` covers the residual quality checks that
don't fit the pydantic schema:

| Check | What it surfaces |
|---|---|
| `dataset_without_variables` | datasets carrying zero curated variables — defence in depth, should always be `[]` |
| `end_date_before_start_date` | `end_date < start_date` (would yield an empty download window for every request) |
| `unreferenced_region` | keys in `regions:` that no dataset's `region:` field points at — registry rot |
| `index_missing_in_datasets` | keys in `available_datasets:` that have no entry under `datasets:` (`get_dataset(key)` would `KeyError`) |
| `datasets_missing_in_index` | the reverse — keys in `datasets:` that the index doesn't advertise |
| `variable_metadata_drift` | `(variable_name, temporal_resolution)` groups where the constituent rows disagree on `(units, types)` |

```python
from earthlens.chc import Catalog

issues = Catalog().health()
{k: v for k, v in issues.items() if v}
# {'variable_metadata_drift': ['precipitation/daily']}
# (the H3-tracked drift across the multi-region CHIRPS-2.0 daily rows
#  whose precipitation `description` legitimately varies)
```

A clean catalog returns `{...: []}` for every key.

## Caching

`Catalog()` parses through a module-level cache keyed on
`(resolved_path, fingerprint)`:

- **Directory layout**: fingerprint is a `tuple((name, mtime_ns), ...)`
  over the sorted `*.yaml` members. Editing any per-family file
  bumps the tuple and invalidates the entry. Collision-free under
  mtime permutations (a swap that leaves the sum unchanged still
  produces a different tuple).
- **File layout** (legacy single-file): fingerprint is the file's
  `stat().st_mtime_ns`.

Repeated `Catalog()` construction across a process is therefore ~1 ms
(cache hit) rather than re-parsing every YAML. Tests that
monkey-patch `CATALOG_PATH` should call `clear_catalog_cache()` to
avoid stale entries.

## Strict YAML loading

`_StrictSafeLoader` rejects duplicate keys in any mapping:

```python
# An accidentally-duplicated key in the YAML:
#
#   datasets:
#     global-daily: { ... }
#     global-monthly: { ... }
#     global-daily: { ... }     # ← second occurrence
#
# Pre-fix: silent shadowing (the second wins, the first is lost).
# Post-fix:
#     ValueError: duplicate YAML key 'global-daily' at line 142,
#     column 3 of chirps-2.0.yaml: every key in a YAML mapping must
#     be unique
```

Cross-file duplicates are caught separately by the directory loader
(which keeps a `{ds_key: filename}` map and raises on the second
occurrence).

## Tooling

### `tools/chc/refresh_chc_catalog.py`

Walks `data.chc.ucsb.edu` and regenerates the
`available_datasets:` index in `_index.yaml`. Run after CHC publishes
a new product. CHC analogue of
`tools/gee/refresh_gee_catalog.py` and
`tools/ecmwf/refresh_available_datasets.py`.

### `tools/chc/audit_chc_datasets.py`

Coverage / staleness classifier. Walks the catalog and reports which
shipped datasets have been verified against the live FTP (and which
haven't been touched since N days). Parallel of
`tools/gee/audit_gee_datasets.py`.

### `tools/chc/probe_chirps_gefs.py`

CHIRPS-GEFS-specific FTP probe. Lists the real contents of each
`CHIRPS-GEFS/...` directory on the live FTP, prints a sample of
filenames, and suggests a filename template inferred from the
listing. Was the basis for the H2 decision to **withdraw** the
CHIRPS-GEFS v3 rows — the probe found the directory shape didn't
match the YAML's provisional patterns
(`year/` partitioning vs the YAML's `year/month/` assumption;
`anom/data/zscore` subdir split on the dekad/pentad variants). The
rows are reinstated only when the probe confirms a verified pattern.

## Adding a new dataset

1. Pick the right per-family file (e.g. a new SPEI window goes in
   `indices.yaml`).
2. Add a `datasets:` entry following the schema above. If the
   region is new, add it to `_index.yaml`'s `regions:` block first.
3. Add the dataset key to `_index.yaml`'s `available_datasets:`
   (alphabetical or walk-order — the order is informational).
4. Run the catalog test suite:
   ```bash
   pixi run -e dev python -m pytest tests/test_chc_catalog/ -q
   ```
5. Run `Catalog().health()` and make sure no new keys show up
   non-empty.
6. If the dataset uses a new placeholder, extend
   `CHIRPS._placeholders()` to expand it.
7. If the FTP layout is provisional, leave a banner comment in the
   family file and write a probe under `tools/chc/` before relying on
   the row.
