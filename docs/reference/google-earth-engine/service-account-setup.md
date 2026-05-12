# Creating a Google Cloud project and service account for Earth Engine

The Earth Engine (GEE) backend authenticates with a **Google Cloud
service account** and a **JSON key file** — no interactive browser
login on the machine that runs the download. This page walks through
everything from zero: creating the Cloud project, registering it for
Earth Engine, creating the service account, granting it the right
roles, generating the key, and verifying that the credentials can
actually reach Earth Engine.

> **Why a service account (not `earthengine authenticate`)?** The
> interactive flow (`ee.Authenticate()` → browser → token cached in
> `~/.config/earthengine/credentials`) is convenient for a laptop but
> useless for CI, cron jobs, or a headless server. A service-account
> key works everywhere and is the form the earthlens GEE backend
> expects.

## At a glance

| Step | What you create                                 | Where                                                                      |
|------|-------------------------------------------------|----------------------------------------------------------------------------|
| 1    | A Google Cloud **project**                      | <https://console.cloud.google.com/projectcreate>                           |
| 2    | **Earth Engine registration** for that project  | <https://console.cloud.google.com/earth-engine>                            |
| 3    | The **Earth Engine API** enabled on the project | <https://console.cloud.google.com/apis/library/earthengine.googleapis.com> |
| 4    | A **service account**                           | <https://console.cloud.google.com/iam-admin/serviceaccounts>               |
| 5    | An **IAM role** grant on the service account    | same page → *Permissions*                                                  |
| 6    | A **JSON key** for the service account          | service account → *Keys* → *Add key*                                       |
| 7    | Verification                                    | the snippet in [§7](#7-verify-the-credentials)                             |

You need: a Google account, and (for the *commercial* track) a billing
account. The *noncommercial* track (research, education, non-profit)
does not require billing but does require an eligibility check.

---

## 1. Create a Google Cloud project

1. Go to <https://console.cloud.google.com/projectcreate>.
2. Pick a **project name** (e.g. `earthlens-gee`). Note the
   auto-generated **project ID** (e.g. `earthlens-gee-431207`) — the
   ID, not the name, is what you pass to `ee.Initialize(...,
   project=...)` and what must be registered for Earth Engine.
3. (Commercial track only) attach a **billing account** when prompted.

If you already have a project you want to use, skip to step 2 — just
make sure you know its **project ID** (`gcloud projects list`, or the
project picker in the console header).

## 2. Register the project for Earth Engine

A bare Cloud project **cannot** call Earth Engine until it is
registered. If you skip this you will hit:

```
EEException: Project <id> is not registered to use Earth Engine.
Visit https://console.cloud.google.com/earth-engine/configuration?project=<id> to register your project.
```

1. Go to <https://console.cloud.google.com/earth-engine> (or the
   `configuration?project=<id>` link from the error).
2. Choose the project from step 1.
3. Pick a usage track:
   - **Noncommercial** — research / education / non-profit. Free.
     Requires an eligibility questionnaire; approval is usually quick
     but can take a day or two.
   - **Commercial / paid** — anything else. Requires a billing account
     and is billed per Earth Engine pricing.
4. Complete the form. Once the project shows as *registered* you can
   proceed.

> If you registered a project for noncommercial use in the past and
> lost access, you may need to **re-verify eligibility** at the same
> page.

## 3. Enable the Earth Engine API

1. Go to
   <https://console.cloud.google.com/apis/library/earthengine.googleapis.com>.
2. Select your project (top of the page).
3. Click **Enable**.

Equivalent CLI:

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable earthengine.googleapis.com
```

## 4. Create the service account

1. Go to
   <https://console.cloud.google.com/iam-admin/serviceaccounts>, make
   sure the right project is selected, and click **Create service
   account**.
2. **Name** — e.g. `earthlens-ee`. The console derives an email like
   `earthlens-ee@<PROJECT_ID>.iam.gserviceaccount.com`. Write this
   address down — it is the `client_email` you (and the earlier
   `gcloud` examples) refer to as the *service account*.
3. Click **Create and continue**.

Equivalent CLI:

```bash
gcloud iam service-accounts create earthlens-ee \
  --display-name "earthlens Earth Engine" \
  --project <PROJECT_ID>
```

## 5. Grant the service account an Earth Engine role

On the *Grant this service account access to the project* step (or
later from **IAM & Admin → IAM**), add **one** of:

| Role | ID | Use when |
|---|---|---|
| **Earth Engine Resource Viewer** | `roles/earthengine.viewer` | Read-only: filtering collections, computing, downloading pixels — what the earlthens backend's `getDownloadURL` / `Export` paths need. **Recommended.** |
| **Earth Engine Resource Writer** | `roles/earthengine.writer` | Also creating/managing assets in the project (uploading your own assets). |
| **Earth Engine Resource Admin** | `roles/earthengine.admin` | Full control incl. ACLs. Rarely needed. |

If you plan to use `export_via="gcs"` (export to a Cloud Storage
bucket), also grant **Storage Object Admin** (`roles/storage.objectAdmin`)
on the project or, better, on the specific bucket.

Equivalent CLI (read-only role):

```bash
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member "serviceAccount:earthlens-ee@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role roles/earthengine.viewer
```

> Some older docs tell you to "register the service account" via
> `signup.earthengine.google.com`. With the current Cloud-project
> model that is **not** needed — registering the *project* (step 2)
> and giving the service account an Earth Engine IAM role (step 5) is
> sufficient.

## 6. Create and download the JSON key

1. On the service accounts list, click your service account →
   **Keys** tab → **Add key → Create new key → JSON → Create**.
2. The browser downloads a file like
   `<PROJECT_ID>-abc123.json`. **This is a secret** — anyone with it
   can act as the service account. Store it outside any git
   repository, restrict its permissions, and never commit it.
3. Rename / move it somewhere stable, e.g.
   `C:\Users\<you>\.config\earthlens\ee-service-account.json` (Windows)
   or `~/.config/earthlens/ee-service-account.json` (Linux/macOS).

The file looks like:

```json
{
  "type": "service_account",
  "project_id": "<PROJECT_ID>",
  "private_key_id": "…",
  "private_key": "-----BEGIN PRIVATE KEY-----\n…\n-----END PRIVATE KEY-----\n",
  "client_email": "earthlens-ee@<PROJECT_ID>.iam.gserviceaccount.com",
  "client_id": "…",
  "token_uri": "https://oauth2.googleapis.com/token",
  …
}
```

Equivalent CLI:

```bash
gcloud iam service-accounts keys create ee-service-account.json \
  --iam-account earthlens-ee@<PROJECT_ID>.iam.gserviceaccount.com
```

## 7. Verify the credentials

A standalone check (no earthlens involved), using the `earthengine-api`
package (`pip install earthengine-api`, or it ships with the
`earthlens[gee]` extra):

```python
import json

import ee

KEY = r"C:\Users\you\.config\earthlens\ee-service-account.json"  # adjust
info = json.load(open(KEY))

credentials = ee.ServiceAccountCredentials(info["client_email"], KEY)
ee.Initialize(credentials, project=info["project_id"])

# Touch a public dataset to prove the round-trip works:
print(ee.Image("USGS/SRTMGL1_003").bandNames().getInfo())  # -> ['elevation']
```

Expected output: `['elevation']`. Common failures:

| Error | Cause | Fix |
|---|---|---|
| `Project <id> is not registered to use Earth Engine` | Step 2 skipped | Register the project at <https://console.cloud.google.com/earth-engine> |
| `Earth Engine API has not been used in project <id> before or it is disabled` | Step 3 skipped | Enable the API (step 3) |
| `Caller does not have required permission` / `PERMISSION_DENIED` | Step 5 missing or wrong role | Grant `roles/earthengine.viewer` to the service account |
| `invalid_grant` / `Invalid JWT Signature` | Wrong/rotated/corrupt key file, or machine clock skew | Re-download the key (step 6); check system time |
| `Project not specified` | `project=` omitted and no default | Pass `project=info["project_id"]` to `ee.Initialize` |

## 8. Use it with earthlens

Once the GEE backend ships, the service account flows straight into the
`EarthLens` facade:

```python
from earthlens import EarthLens

el = EarthLens(
    data_source="gee",
    variables={"USGS/SRTMGL1_003": ["elevation"]},
    start="2020-01-01",
    end="2020-01-02",
    lat_lim=[29.9, 30.1],
    lon_lim=[31.1, 31.3],
    path="data/gee",
    service_account="earthlens-ee@<PROJECT_ID>.iam.gserviceaccount.com",
    service_key=r"C:\Users\you\.config\earthlens\ee-service-account.json",
)
el.download()
```

For CI, store the key's **contents** in a secret (e.g.
`GEE_SERVICE_KEY`) and the email in another (`GEE_SERVICE_ACCOUNT`);
the backend accepts `service_key` as either a path *or* the raw JSON
string, so a runner can write the secret to a temp file or pass it
directly.

## Security notes

- The JSON key is a long-lived credential. Prefer the least-privilege
  role (`earthengine.viewer`), keep the key out of version control,
  and rotate it periodically (`gcloud iam service-accounts keys
  list / delete`).
- `GOOGLE_APPLICATION_CREDENTIALS` (Application Default Credentials)
  pointing at the same JSON file also works for many Google client
  libraries, but the earthlens GEE backend takes `service_account` /
  `service_key` explicitly so the credential is unambiguous.
- Deleting the service account or the key immediately revokes access;
  there is no separate "Earth Engine deregistration" to do.

## References

- Earth Engine — service accounts: <https://developers.google.com/earth-engine/guides/service_account>
- Earth Engine — access & registration: <https://developers.google.com/earth-engine/guides/access>
- Earth Engine — Python install: <https://developers.google.com/earth-engine/guides/python_install>
- Google Cloud — creating and managing service accounts: <https://cloud.google.com/iam/docs/service-accounts-create>
- Google Cloud — creating service account keys: <https://cloud.google.com/iam/docs/keys-create-delete>
