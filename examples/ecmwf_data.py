"""Download Satellite data ECMWF Installation of ECMWF API key.

1 - to be able to use Hapi to download ECMWF data you need to register and setup your account in the ECMWF website (https://apps.ecmwf.int/registration/)

2 - Install ECMWF key (instruction are here https://confluence.ecmwf.int/display/WEBAPI/Access+ECMWF+Public+Datasets#AccessECMWFPublicDatasets-key)
"""
import os

from earth2observe.ecmwf import ECMWF, Catalog

rpath = os.getcwd()
path = rf"{rpath}\delete\data\ecmwf"
#%% precipitation
start = "2009-01-01"
end = "2009-01-10"
time = "daily"
lat = [4.190755, 4.643963]
lon = [-75.649243, -74.727286]
# Temperature, Evapotranspiration
variables = ["temperature", "evaporation"]
#%%
var = "temperature"
catalog = Catalog()
print(catalog.catalog)
catalog.get_variable(var)
#%% Temperature
start = "2009-01-01"
end = "2009-02-01"
time = "daily"
latlim = [4.19, 4.64]
lonlim = [-75.65, -74.73]
# %%
# Temperature, Evapotranspiration
variables = ["evaporation"]  # "temperature",

Coello = ECMWF(
    temporal_resolution=time,
    start=start,
    end=end,
    path=path,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
)

Coello.download(dataset="interim")
#%%
variables = ["surface-runoff"]
Coello = ECMWF(
    temporal_resolution=time,
    start=start,
    end=end,
    path=path,
    variables=variables,
    lat_lim=latlim,
    lon_lim=lonlim,
)

Coello.download()
