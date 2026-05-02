"""Download Satellite data ECMWF Installation of ECMWF API key.

1 - to be able to use Hapi to download ECMWF data you need to register and setup your account in the ECMWF website (https://apps.ecmwf.int/registration/)

2 - Install ECMWF key (instruction are here https://confluence.ecmwf.int/display/WEBAPI/Access+ECMWF+Public+Datasets#AccessECMWFPublicDatasets-key)
"""
import os

from earthly.ecmwf import ECMWF, Catalog

rpath = os.getcwd()
path = rf"{rpath}\delete\data\ecmwf"
#%% precipitation
start = "2009-01-01"
end = "2009-01-10"
time = "daily"
lat = [4.190755, 4.643963]
lon = [-75.649243, -74.727286]
# Variables addressed by (dataset_name, variable_name).
variables = {
    "reanalysis-era5-pressure-levels": ["temperature"],
    "reanalysis-era5-single-levels": ["evaporation"],
}
#%%
catalog = Catalog()
print(list(catalog.datasets))
catalog.get_variable("reanalysis-era5-pressure-levels", "temperature")
#%% Temperature
start = "2009-01-01"
end = "2009-02-01"
time = "daily"
latlim = [4.19, 4.64]
lonlim = [-75.65, -74.73]
# %%
# Single-dataset download example.
variables = {
    "reanalysis-era5-single-levels": ["evaporation"],
}

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
#%%
variables = {
    "reanalysis-era5-single-levels": ["surface-runoff"],
}
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
