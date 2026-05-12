"""Download imagery from Google Earth Engine with earthlens.

Requires the `[gee]` extra (`pip install earthlens[gee]`) and a Google
Cloud service account whose project is registered for Earth Engine — see
`docs/reference/google-earth-engine/` (Introduction → Registering a
project → Service account setup → Usage). Set the two variables below to
your service-account email and the path to its JSON key (or the key's
JSON content as a string).
"""

import os

from earthlens import EarthLens
from earthlens.gee import Catalog

# %% Credentials — point these at your own service account / key.
SERVICE_ACCOUNT = os.environ.get(
    "GEE_SERVICE_ACCOUNT", "my-sa@my-project.iam.gserviceaccount.com"
)
SERVICE_KEY = os.environ.get("GEE_SERVICE_KEY", "/path/to/key.json")

# %% Browse the catalog (no network, no auth).
catalog = Catalog()
print(f"{len(catalog.datasets)} curated datasets; "
      f"{len(catalog.available_datasets)} in the Earth Engine STAC index")
print(list(catalog.datasets))
chirps = catalog.get_dataset("UCSB-CHG/CHIRPS/DAILY")
print("CHIRPS:", chirps.ee_type, chirps.cadence, "bands:", list(chirps.bands))
print("precipitation units:", catalog.get_band("UCSB-CHG/CHIRPS/DAILY", "precipitation").units)

# %% A small CHIRPS request — monthly composites over a tiny bbox — via the facade.
el = EarthLens(
    data_source="gee",
    start="2020-06-01",
    end="2020-08-31",
    temporal_resolution="monthly",        # one composite image per month
    variables={"UCSB-CHG/CHIRPS/DAILY": ["precipitation"]},
    lat_lim=[28.0, 32.0],                  # [lat_min, lat_max]
    lon_lim=[30.0, 34.0],                  # [lon_min, lon_max]
    path=r"examples/data/delete/gee",
    scale=5566,                            # output pixel size in metres
    service_account=SERVICE_ACCOUNT,
    service_key=SERVICE_KEY,
)
paths = el.download()
print("wrote:", [str(p) for p in paths])

# %% The same thing without the facade — use `earthlens.gee.GEE` directly.
# from earthlens.gee import GEE
# gee = GEE(
#     start="2000-02-11", end="2000-02-12",
#     variables={"USGS/SRTMGL1_003": ["elevation"]},
#     lat_lim=[29.9, 30.0], lon_lim=[31.2, 31.3],
#     path=r"examples/data/delete/gee", scale=90,
#     service_account=SERVICE_ACCOUNT, service_key=SERVICE_KEY,
# )
# gee.download()

# %% Large AOI: queue an asynchronous export to Google Drive instead of streaming.
# el = EarthLens(
#     data_source="gee",
#     start="2023-01-01", end="2023-12-31", temporal_resolution="monthly",
#     variables={"COPERNICUS/S2_SR_HARMONIZED": ["B4", "B8"]},
#     lat_lim=[51.0, 53.0], lon_lim=[4.0, 7.0],
#     scale=10, export_via="drive", drive_folder="ee_exports",
#     service_account=SERVICE_ACCOUNT, service_key=SERVICE_KEY,
# )
# locations = el.download()   # blocks while the batch tasks run; pull the files from Drive
