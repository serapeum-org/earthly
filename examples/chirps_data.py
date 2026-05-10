from earthlens.chirps import CHIRPS, Catalog

# %%
chirps_catalog = Catalog()
print(chirps_catalog.catalog)
# %% precipitation
start = "2009-01-01"
end = "2009-01-2"
time = "daily"
latlim = [4.19, 4.64]
lonlim = [-75.65, -74.73]

path = r"examples/data/delete/chirps"
Coello = CHIRPS(
    start=start,
    end=end,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=time,
    path=path,
)
# %%
Coello.download()  # cores=4
# %%
# Coello.download(cores=4)
