import os

from earthly.s3 import S3, Catalog

#%%
s3_catalog = Catalog()
print(s3_catalog.catalog)
s3_catalog.get_variable("precipitation")
years = s3_catalog.get_available_years()
date = "2022-05-01"
# available_date_abs_path = s3_catalog.get_available_data(date, bucket='era5-pds', absolute_path=True)
# available_date = s3_catalog.get_available_data(date, bucket='era5-pds', absolute_path=False)
#%%
start = "2022-05-01"
end = "2022-05-01"
time = "monthly"
lat = [4.190755, 4.643963]
lon = [-75.649243, -74.727286]
variables = ["precipitation"]
rpath = os.getcwd()
path = rf"{rpath}/examples/data/s3-era5"

s3_era5 = S3(
    temporal_resolution=time,
    start=start,
    end=end,
    path=path,
    variables=variables,
    # lat_lim=lat,
    # lon_lim=lon,
)
#%%
s3_era5.download()
#%%
