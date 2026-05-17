# Climate Hazards Center — introduction

The [Climate Hazards Center](https://www.chc.ucsb.edu/) (CHC) at UC
Santa Barbara is one of the canonical public sources of long-record
satellite + station-blended rainfall, temperature, and drought-index
products. CHC's flagship dataset, **CHIRPS** (Climate Hazards Group
InfraRed Precipitation with Station data), starts in **1981** and is
updated within a few weeks of the present day — that combination of
length, latency, and quality is unusual in remote-sensing rainfall and
is why CHIRPS is the working dataset behind a large fraction of
operational drought-monitoring and famine-early-warning analyses.

This page orients the `earthlens` CHC backend. For the hands-on
download walkthrough see [Usage](usage.md); for catalog mechanics see
[Catalog](catalog.md); the rendered API is the [Reference](chc.md)
page.

## Why it matters here

The other earthlens backends fetch data from a vendor cloud (ECMWF /
Copernicus over `cdsapi`, ERA5 from AWS, Google Earth Engine). CHC is
different in two ways:

- **Plain anonymous FTP at `data.chc.ucsb.edu`.** No accounts, no
  API keys, no quotas, no terms of service to accept. Open a TCP
  socket, log in as `anonymous`, walk the tree. The earthlens
  backend wraps `ftplib` (stdlib) and that's all that's required.
- **Catalog-driven, not provider-driven.** The CHC FTP layout is
  per-product-family (`pub/org/chc/products/CHIRPS-2.0/global_daily/...`,
  `.../WBGT/wbgt_global_dekad_data/...`) with per-family filename
  conventions. The backend doesn't hardcode any of that — it
  consults `earthlens.chc.Catalog` and substitutes per-date
  placeholders against the templates the catalog declares.

The result: any CHC dataset addressable in the catalog (97 today, of
which only ~40 are CHIRPS-the-product) is one
`CHIRPS(variables={"<ds-key>": [...]})` away.

## The product line

The CHC catalog covers 13 product families:

| Family | Datasets | Variable | Cadence | Coverage |
|---|---|---|---|---|
| **CHIRPS-2.0** | 27 | `precipitation` | daily / pentadal / dekadal / monthly / 2-/3-monthly / annual | 1981-present, ±50° |
| **CHIRPS v3** | 11 | `precipitation` | daily (rnl / sat / prelim) / pentadal / dekadal / monthly / 2-monthly / annual | 1981-present, ±50° |
| **CHIRP / CHIRP v3** | 7 | `precipitation` | daily / pentadal / dekadal / monthly + v3 daily / dekadal / monthly | 1981-present, ±50° |
| **CHIRTSdaily / CHIRTSmonthly** | 7 | `tmax`, `tmin`, `relative-humidity`, `heat-index`, `svp`, `vpd` | daily / monthly | 1983-present, ±60° land |
| **CHIRPS-GEFS v12** | 9 | `precipitation` | daily / pentadal / dekadal / 16-day forecast | 2000-present, ±50° |
| **CHPclim v2** | 1 | `precipitation` | 12 static climatological months (no time axis) | 1981–2010 baseline |
| **WBGT** | 2 | `wbgt` (Wet Bulb Globe Temperature) | monthly / dekadal | 1980-present, ±90° |
| **SPI / CHIRPS3** | 9 | `spi` (standardised precipitation index) | pentadal at 1 / 2 / 5 / 6 / 9 / 12 / 15 / 18 / 21-month windows | 1981-present, ±50° |
| **SPEI v1** | 6 | `spei` (precip - PET, MERRA-2 PET) | monthly at 1 / 2 / 3 / 6 / 9 / 12-month windows | 1981-present, ±50° |
| **CHC_CMIP6 deltas** | 16 | `precip_delta`, `tmax_delta`, `tmin_delta`, `rh_delta` | daily climatology delta | SSP245 / SSP585, 2030 / 2050 targets |
| **CentennialTrends v1** | 2 | `precipitation` | monthly + 4-season | 1900–2014, East Africa |

Browse the full list via `Catalog.list_datasets()` or
`Catalog.available_datasets`. Every entry is reachable from
`pub/org/chc/products/...` on the FTP.

## Data is shipped as files, not as a service

Every CHC dataset is a static raster (or NetCDF) sitting in a directory
on the FTP. Two filename shapes show up:

- **Per-date partitions** (the common case). One file per timestep,
  named after the date — e.g.
  `chirps-v2.0.2024.01.15.tif.gz` for CHIRPS-2.0 daily 2024-01-15.
  The catalog stores a filename template (`{year}/chirps-v2.0.{year}.{month}.{day}.tif.gz`)
  with placeholders the backend expands per date.
- **Discrete archive files** (CHPclim v2's 12 climatological months,
  CenTrends v1's multi-year NetCDFs). The whole dataset is N fixed
  filenames, not a time-partitioned series. The catalog declares
  `discrete_files:` instead of `file_patterns:`; the backend iterates
  the list once per `(dataset, variable)` request rather than doing
  date substitution.

The earthlens backend handles both shapes transparently
(`Dataset.is_discrete` selects the path); a consumer of `CHIRPS(...)`
never has to think about it.

## Catalog layout

The CHC catalog ships as package data under
`src/earthlens/chc/catalog/` in a **GEE-style per-family split**:

```
src/earthlens/chc/catalog/
├── _index.yaml          # available_datasets: + regions: block
├── chirps-2.0.yaml      # 27 datasets (the CHIRPS-2.0 family)
├── chirps-v3.yaml       # 11 datasets
├── chirp.yaml           # 7 datasets (CHIRP + CHIRP v3)
├── chirts.yaml          # 7 datasets (daily + monthly + derived)
├── cmip6.yaml           # 16 datasets (CHC_CMIP6 scenario deltas)
├── derived.yaml         # 5 datasets (CHPclim + WBGT + CenTrends)
├── gefs.yaml            # 9 datasets (CHIRPS-GEFS v12)
└── indices.yaml         # 15 datasets (SPI + SPEI)
```

`_index.yaml` carries an informational walk-order list of every dataset
key and the `regions:` block (9 named geographic-coverage profiles —
`global`, `africa`, `central-america-caribbean`, `east-africa`,
`east-africa-centennial`, `global-extended`, `global-land`, `indonesia`,
`western-hemisphere`). The per-family files carry the actual
dataset bodies; the loader merges every `*.yaml` sibling except
`_index.yaml` into one `Catalog.datasets` dict. Dataset keys must be
unique across files.

This is the CHC analogue of the ECMWF `cds_data_catalog.yaml` and the
GEE per-data-type catalog under `src/earthlens/gee/catalog/`. See
[Catalog](catalog.md) for the details.

## Authentication

**None.** `data.chc.ucsb.edu` accepts anonymous FTP. The backend logs
in as `anonymous` with no password (`ftp.login()` defaults). No
account creation, no key files, no quotas, no rate limiting beyond
what an over-eager script would induce on its own.

A pragmatic consequence: an institutional firewall that blocks
outbound FTP port 21 will block the backend. Confirm with
`gh repo view --json url | jq` … sorry, with a quick `python -c "from
ftplib import FTP; FTP('data.chc.ucsb.edu').login()"` from the target
machine before debugging the backend itself.

## Cost

**Free, public, no terms-of-service click-through.** CHC datasets
carry CC-BY-style licences (the exact terms vary per product; see
each dataset's CHC landing page for the citation block). Egress from
`data.chc.ucsb.edu` is free at the source; the only cost is your own
bandwidth.

## References

- CHC home: <https://www.chc.ucsb.edu/>
- CHIRPS paper (Funk et al. 2015): <https://doi.org/10.1038/sdata.2015.66>
- CHIRTS paper (Funk et al. 2019): <https://doi.org/10.1175/JCLI-D-18-0698.1>
- WBGT (Spangler et al. 2024): <https://doi.org/10.1029/2023GH001003>
- CenTrends v1 (Funk et al. 2015): <https://doi.org/10.7289/V5RX99FT>
- CHC FTP root: <ftp://data.chc.ucsb.edu/pub/org/chc/>
- CHC product browser (HTTPS mirror): <https://data.chc.ucsb.edu/products/>
- earthlens CHC usage: [Usage](usage.md)
- earthlens CHC catalog mechanics: [Catalog](catalog.md)
- earthlens CHC API: [Reference](chc.md)
