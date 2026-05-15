from earthlens.earthlens import EarthLens

# unified parameters for all data sources.
start = "2009-01-01"
end = "2009-01-10"
temporal_resolution = "daily"
latlim = [4.19, 4.64]
lonlim = [-75.65, -74.73]
# %%
source = "chc"
path = r"examples\data\chirps"
variables = ["precipitation"]
earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
# earthlens.download()
# %%
path = r"examples\data\chirps-cores"

earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
# earthlens.download(cores=4)
# %%

path = r"examples\data\ecmwf"
source = "ecmwf"
variables = ["precipitation"]
earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
# earthlens.download()

# %%
path = r"examples\data\s3-backend"
source = "amazon-s3"
variables = ["precipitation"]
earthlens = EarthLens(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    # lat_lim=latlim,
    # lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
earthlens.download()
