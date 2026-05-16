# Google Earth Engine — introduction

[Google Earth Engine](https://earthengine.google.com/) (GEE) is a
cloud platform for planetary-scale geospatial analysis. It pairs a
multi-petabyte, analysis-ready **data catalog** (satellite imagery,
climate reanalyses, land-cover products, elevation, and more — decades
deep, global) with a **server-side compute engine**: you describe a
computation against image collections, Earth Engine runs it across
Google's infrastructure, and you pull back only the result (a
composite, a time series, a clipped GeoTIFF) rather than the raw
archive.

This page is the orientation for the earthlens GEE backend. For the
hands-on credential walkthrough, see
[Service account setup](service-account-setup.md).

## Why it matters here

The other earthlens backends (CHIRPS, ERA5-on-S3, ECMWF/CDS) download
files from a server. GEE is different in kind: the data never lands on
your disk in bulk — you send a *recipe* (filter this collection to
these dates and this bounding box, select these bands, reduce to a
monthly mean) and Earth Engine returns the finished product. That makes
it ideal for "I need a 10 m Sentinel-2 cloud-free composite over this
watershed for last summer" without provisioning terabytes of scenes.

Two ways pixels come out of Earth Engine, both surfaced by the backend:

- **Synchronous** — `ee.Image.getDownloadURL` returns a link to a
  small GeoTIFF (hard cap ~32 MB / ~50 M pixels). Fast, simple, good
  for small AOIs. (`export_via="url"`)
- **Asynchronous batch export** — `ee.batch.Export.image.toDrive` /
  `.toCloudStorage` queues a job you poll until it finishes; no
  practical size limit. Needed for real, large exports.
  (`export_via="drive"` / `"gcs"`)

## The data catalog

Earth Engine hosts ~1000+ public datasets, each addressed by an asset
ID like `LANDSAT/LC09/C02/T1_L2`, `COPERNICUS/S2_SR_HARMONIZED`,
`MODIS/061/MOD13Q1`, `ECMWF/ERA5_LAND/DAILY_AGGR`,
`UCSB-CHG/CHIRPS/DAILY`, `USGS/SRTMGL1_003`, `ESA/WorldCover/v200`.
Each is either an `Image` (a single static raster, e.g. SRTM
elevation), an `ImageCollection` (a time series of scenes/composites),
or a `Table` (a `FeatureCollection` — vectors, out of scope for the
raster backend). Datasets carry per-band metadata (units, scale/offset
factors, wavelength), a native spatial resolution in metres, a temporal
cadence, and a valid date range — all browsable in the
[Earth Engine Data Catalog](https://developers.google.com/earth-engine/datasets)
and machine-readable via its
[STAC catalog](https://storage.googleapis.com/earthengine-stac/catalog/catalog.json).

earthlens ships a curated subset of this in
`src/earthlens/gee/catalog/` — a directory of per-provider YAML files
(`MODIS.yaml`, `COPERNICUS.yaml`, `LANDSAT.yaml`, `community.yaml` for
`projects/...` assets, …) plus `_index.yaml` carrying the merged
`available_datasets:` list. The loader (`earthlens.gee.Catalog`)
parses every file and merges them. This is the GEE analogue of the
ECMWF `cds_data_catalog.yaml`, mapping asset IDs to the band and
aggregation metadata the backend needs.

## Authentication

Earth Engine requires authenticated access tied to a registered Google
Cloud project. Two modes:

- **Interactive** — `ee.Authenticate()` opens a browser, caches a token
  in `~/.config/earthengine/credentials`. Fine for a laptop; useless
  for CI or a headless server.
- **Service account** — a Google Cloud service account plus a JSON key
  file; works everywhere. **This is what the earthlens GEE backend
  expects** (`service_account=` + `service_key=`).

Either way, the *project* must be registered for Earth Engine — a bare
Cloud project will fail with `Project <id> is not registered to use
Earth Engine`. Full step-by-step: [Service account setup](service-account-setup.md).

## Cost — and how to use it for free

**Earth Engine is free for noncommercial use; commercial use requires
a paid license.** The free-vs-paid split is about *who you are and what
you're doing*, not about query volume.

### Free — noncommercial track

Research, education, journalism, non-profits, and government agencies
doing public-interest work qualify. You still create a Cloud project
and register it, but you pick the **noncommercial** track:

- **No billing account required**, **no charge** for Earth Engine
  compute or for storage of the EE service itself.
- An **eligibility questionnaire** at
  <https://console.cloud.google.com/earth-engine> — approval is usually
  quick but can take a day or two.
- **Quotas** apply (concurrent requests, the `getDownloadURL` /
  `getThumbURL` ~32 MB / ~50 M-pixel cap, batch-export limits, an EECU
  compute fair-use budget) — but there is no dollar cost.

To use it for free, concretely:

1. Create a Google Cloud project (no billing account needed for this
   path).
2. Go to <https://console.cloud.google.com/earth-engine>, select the
   project, choose **Noncommercial**, complete the eligibility form,
   wait for approval.
3. Enable the Earth Engine API, create a service account + JSON key,
   grant it `roles/earthengine.viewer` — see
   [Service account setup](service-account-setup.md).
4. In earthlens, use `export_via="url"` (small AOIs) or
   `export_via="drive"` (large exports). Both stay within the free
   service.

### Paid — commercial track

Any for-profit or operational use needs an **Earth Engine commercial
license**: tiered **subscription** plans (the entry tier is on the
order of low-thousands USD/month; larger plans scale up). It is a
subscription model for the managed service, not pay-per-query.

### A separate, always-applicable cost: Google Cloud charges

Independent of the EE track, *any* user incurs normal **Google Cloud**
charges if they use other GCP services alongside Earth Engine — most
relevantly, exporting to **Cloud Storage** (`export_via="gcs"`) bills
GCS storage + egress. Exporting to **Google Drive**
(`export_via="drive"`) or pulling via `getDownloadURL`
(`export_via="url"`) avoids GCP billing entirely. If you're on the free
track and want to stay at zero cost, don't use `export_via="gcs"`.

### Quick reference

| Use case | EE license | Billing account? | Typical cost |
|---|---|---|---|
| Academic research / teaching | Noncommercial | No | Free (within quotas) |
| Non-profit / NGO public-interest work | Noncommercial | No | Free (within quotas) |
| Government / public-sector analysis | Noncommercial | No | Free (within quotas) |
| Any commercial / operational product | Commercial | Yes | Paid subscription (tiered) |
| Exporting results to a GCS bucket (any track) | — | Yes | Standard GCS storage + egress |

## References

- Earth Engine home: <https://earthengine.google.com/>
- Data Catalog: <https://developers.google.com/earth-engine/datasets>
- Access & registration (free vs. commercial): <https://developers.google.com/earth-engine/guides/access>
- Commercial / paid plans: <https://earthengine.google.com/commercial/>
- Noncommercial eligibility: <https://earthengine.google.com/noncommercial/>
- Python install: <https://developers.google.com/earth-engine/guides/python_install>
- Service accounts: <https://developers.google.com/earth-engine/guides/service_account>
- Service account setup (this docs section): [service-account-setup.md](service-account-setup.md)
