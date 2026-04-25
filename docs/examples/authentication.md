# Data Source Authentication

## ECMWF (Copernicus Climate Data Store)

The ECMWF backend talks to the [Copernicus Climate Data Store
(CDS)](https://cds.climate.copernicus.eu/) via the official `cdsapi`
client. The legacy ECMWF Web API (`ecmwf-api-client`,
`~/.ecmwfapirc`, `https://api.ecmwf.int/v1`) was decommissioned in
**June 2023** and is no longer supported — including for ERA-Interim,
which was retired in 2019. ERA5 on CDS is the production successor.

### 1. Create a CDS account

Register at <https://cds.climate.copernicus.eu/>. The account uses the
Copernicus single-sign-on and is the same account that grants access
to the Atmosphere Data Store (ADS) if you ever need it.

### 2. Copy your Personal Access Token

Visit your profile page at
<https://cds.climate.copernicus.eu/profile> and copy the **Personal
Access Token (PAT)** — it looks like a UUID. Treat it as a password.

### 3. Create `~/.cdsapirc`

cdsapi reads its credentials from `~/.cdsapirc`. On Windows, save the
file as `C:\Users\<USERNAME>\.cdsapirc`. The contents are two lines:

```
url: https://cds.climate.copernicus.eu/api
key: <YOUR-PERSONAL-ACCESS-TOKEN>
```

There is **no** `email:` line and **no** `<UID>:<KEY>`
colon-separated format — those are conventions of the retired legacy
API. Do not commit this file to source control.

As an alternative for CI runners, cdsapi will fall back to the
`CDSAPI_URL` and `CDSAPI_KEY` environment variables when the dotfile
is absent.

### 4. Accept dataset licences

Each CDS dataset has its own terms of use that must be accepted
**once, per dataset, per account**, on the website. For the variables
ships with `cds_data_catalog.yaml`, accept the licences for at least:

- ERA5 hourly on single levels — <https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels>
- ERA5 monthly on single levels — `…-monthly-means`
- ERA5 hourly on pressure levels — `…-pressure-levels`
- ERA5 monthly on pressure levels — `…-pressure-levels-monthly-means`

Open each dataset page, scroll to the **"Download"** tab, tick the
licence. Otherwise `client.retrieve()` will return *"Required licences
not accepted"*.

### 5. CDS request behaviour to expect

CDS queues each request server-side. `client.retrieve()` blocks until
your request reaches the front of the queue and the file is generated
— typically seconds to several minutes, occasionally longer for large
requests. In CI the test suite mocks the client (see
`tests/test_ecmwf.py`); locally, expect to wait. The end-to-end test
suite is opt-in via `RUN_CDS_E2E=1`.

For the full setup walkthrough see
<https://cds.climate.copernicus.eu/how-to-api>.

## CHIRPS

CHIRPS data source provides data through a public FTP server that does not require any registration.

## Amazon S3

The `era5-pds` bucket provides data publicly and does not require an AWS account to download data.
