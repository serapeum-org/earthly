from earthly.earthly import Earthly

# unified parameters for all data sources.
start = "2009-01-01"
end = "2009-01-10"
temporal_resolution = "daily"
latlim = [4.19, 4.64]
lonlim = [-75.65, -74.73]
#%%
source = "chirps"
path = r"examples\data\chirps"
variables = ["precipitation"]
e2o = Earthly(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
# e2o.download()
#%%
path = r"examples\data\chirps-cores"

e2o = Earthly(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
# e2o.download(cores=4)
#%%

path = r"examples\data\ecmwf"
source = "ecmwf"
variables = ["precipitation"]
e2o = Earthly(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
# e2o.download()

#%%
path = r"examples\data\s3-backend"
source = "amazon-s3"
variables = ["precipitation"]
e2o = Earthly(
    data_source=source,
    start=start,
    end=end,
    variables=variables,
    # lat_lim=latlim,
    # lon_lim=lonlim,
    temporal_resolution=temporal_resolution,
    path=path,
)
e2o.download()

#%%