from earthlens.chc import CHIRPS, Catalog

# %%
chirps_catalog = Catalog()
print(f"datasets: {len(chirps_catalog.datasets)}")
print(chirps_catalog.list_datasets(region="global", temporal_resolution="daily"))
# %% precipitation
start = "2009-01-01"
end = "2009-01-02"
latlim = [4.19, 4.64]
lonlim = [-75.65, -74.73]
path = r"examples/data/delete/chirps"

# Legacy list-shape call (auto-routes to `global-daily` via temporal_resolution):
Coello = CHIRPS(
    start=start,
    end=end,
    lat_lim=latlim,
    lon_lim=lonlim,
    variables=["precipitation"],
    temporal_resolution="daily",
    path=path,
)
# Catalog dict-shape call would be equivalent:
# Coello = CHIRPS(
#     start=start, end=end,
#     lat_lim=latlim, lon_lim=lonlim,
#     variables={"global-daily": ["precipitation"]},
#     path=path,
# )
# %%
Coello.download()  # cores=4 for parallel FTP
