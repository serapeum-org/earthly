# Registering a Google Cloud project for Earth Engine

A bare Google Cloud project **cannot** call Earth Engine. Until the
project is *registered* for Earth Engine use, every API call — whether
from the Code Editor, the Python `earthengine-api`, or the earthlens
GEE backend — fails with:

```
EEException: Project <PROJECT_ID> is not registered to use Earth Engine.
Visit https://console.cloud.google.com/earth-engine/configuration?project=<PROJECT_ID> to register your project.
See https://developers.google.com/earth-engine/guides/access for more details.
```

This page is the step-by-step for getting past that. It assumes you
already have (or are about to create) a Cloud project; for the full
service-account walkthrough that this is part of, see
[Service account setup](service-account-setup.md), and for background
see the [Introduction](introduction.md).

> **Where the "Noncommercial" choice actually is.** It is **not** on the
> Cloud Console `console.cloud.google.com/earth-engine` page — that page
> only *displays* a project's registration status. The wizard that lets
> you pick the noncommercial vs. commercial track is the **Earth Engine
> registration flow** at **<https://code.earthengine.google.com/register>**.

## Prerequisites

| Need | Why | Check |
|---|---|---|
| A Google account | You register *as* someone | — |
| A Cloud project (or create one in the wizard) | Registration attaches to a project ID | <https://console.cloud.google.com/projectcreate> |
| **Owner** or **Editor** role on that project | The wizard won't let you register a project you can't administer | <https://console.cloud.google.com/iam-admin/iam> |
| **Earth Engine API enabled** on the project | The project won't appear / will error in the wizard otherwise | <https://console.cloud.google.com/apis/library/earthengine.googleapis.com> |
| (Commercial track only) a **billing account** attached | Commercial use is billed | <https://console.cloud.google.com/billing> |

Enabling the API from the CLI, if you prefer:

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable earthengine.googleapis.com
```

## Step 1 — Open the Earth Engine registration wizard

Go to **<https://code.earthengine.google.com/register>**.

Make sure the top-right account switcher shows the Google account that
owns/edits the project. If the page looks blank or read-only, you're
not signed in (or signed in as the wrong account) — fix that first. An
incognito window signed directly into the right account is the reliable
way to rule out a stale session.

## Step 2 — Choose the usage track

The wizard's first question is **"Register a Noncommercial or
Commercial Cloud project"** with two options:

- **Unpaid usage → Noncommercial** — *free*. For research, education,
  journalism, non-profits/NGOs/IGOs, government public-interest work,
  and individual/hobbyist projects. Requires picking an eligibility
  sub-category (see step 3). No billing account required.
- **Paid usage → Commercial** — for any for-profit or operational use.
  Requires a billing account; billed per
  [Earth Engine commercial pricing](https://earthengine.google.com/commercial/)
  (tiered subscription).

If you only see a paid/commercial option and no unpaid choice at all,
that almost always means you're not signed in, signed in as the wrong
account, or the account/region has been flagged — retry in an incognito
window signed directly into the account.

## Step 3 — Pick the eligibility category (noncommercial track)

If you chose Noncommercial, the wizard asks what kind of use it is.
Typical categories:

- **Academia & research** — university / research-institute work.
- **Educator or student** — teaching or coursework.
- **Government** — public-sector agencies.
- **Nonprofit** — registered NGOs / IGOs / non-profits.
- **Individual / personal / hobbyist** — not affiliated with an
  institution; tighter quotas than the institutional categories.

Pick the one that fits, and fill in the short form (institution name,
a one-line description of what you'll use Earth Engine for). Be
accurate — misrepresenting commercial use as noncommercial violates the
terms.

## Step 4 — Attach a Cloud project

The wizard then asks which Cloud project to register:

- Pick your existing project from the dropdown (it must have the Earth
  Engine API enabled — step shown in *Prerequisites* — and you must be
  Owner/Editor), **or**
- Create a new project right there in the wizard.

Confirm. The project ID you attach here is the one you'll pass to
`ee.Initialize(..., project="<PROJECT_ID>")` and to the earthlens GEE
backend.

## Step 5 — Submit and wait

Submit the form.

- **Most noncommercial institutional categories** are approved
  effectively instantly.
- Some categories (or borderline cases) go to a **short manual review**
  — usually hours to a day or two. You'll get an email.
- **Commercial** registration is tied to your billing setup and
  agreement acceptance.

When done, the project's status at
<https://console.cloud.google.com/earth-engine> shows as **registered**,
and the registration link in the original error stops applying.

## Step 6 — Verify

A standalone check with `earthengine-api` (ships with the
`earthlens[gee]` extra, or `pip install earthengine-api`):

```python
import ee

PROJECT = "<PROJECT_ID>"            # the project you just registered
ee.Authenticate()                   # one-time browser login (laptop)
ee.Initialize(project=PROJECT)

print(ee.Image("USGS/SRTMGL1_003").bandNames().getInfo())  # -> ['elevation']
```

`['elevation']` means the project is registered and reachable. If you
still get `Project ... is not registered`, the registration hasn't
propagated yet (wait, re-check the status page) or you registered a
*different* project than the one in `project=`.

For a non-interactive (service-account) verification — and the rest of
the setup — continue with [Service account setup](service-account-setup.md).

## Common problems

| Symptom | Cause | Fix |
|---|---|---|
| Register page is blank / read-only | Not signed in, or wrong Google account | Sign in as the project owner; try incognito |
| Only a commercial/paid option appears | Stale session, flagged account, or region restriction | Incognito window signed directly into the account; if it persists, contact Earth Engine support |
| Project not in the wizard's dropdown | Earth Engine API not enabled, or you lack Owner/Editor | Enable `earthengine.googleapis.com`; get the IAM role |
| `Project ... is not registered` after submitting | Registration still propagating, or you initialized a different project ID | Wait and re-check the status page; confirm the `project=` value matches |
| Previously had noncommercial access, now denied | Eligibility lapsed | Re-verify at <https://developers.google.com/earth-engine/guides/access#configuring_noncommercial_access> |

## References

- Earth Engine registration wizard: <https://code.earthengine.google.com/register>
- Project registration status (Cloud Console): <https://console.cloud.google.com/earth-engine>
- Access & registration guide (free vs. commercial): <https://developers.google.com/earth-engine/guides/access>
- Noncommercial use & eligibility: <https://earthengine.google.com/noncommercial/>
- Commercial plans & pricing: <https://earthengine.google.com/commercial/>
- Enable the Earth Engine API: <https://console.cloud.google.com/apis/library/earthengine.googleapis.com>
