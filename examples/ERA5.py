import os
import glob
import datetime as dt
from loguru import logger
import geopandas as gpd
import pandas as pd
from pyramids.raster import Raster
from pyramids.convert import Convert
from osgeo import gdal
from osgeo.gdal import Dataset
import numpy as np
from pyramids.indexing import H3
from earthly.earthly import Earthly
rpath = os.getcwd()
rdir = "examples\project"
#%%
"""
First Download the ERA5 data from Amazon S3 data source
"""
start = "2022-05-01"
end = "2022-05-01"
time = "monthly"
path = rf"{rdir}\s3-backend"
source = "amazon-s3"
variables = ["precipitation"]
earthly = Earthly(
    data_source=source,
    temporal_resolution=time,
    start=start,
    end=end,
    path=path,
    variables=variables,
)

earthly.download()
#%%
"""
Convert the downloaded netcdf into rasters one for each time stamp in the ncdf file
For the example I converted only 1-hourly rasters 
"""
nc_file = f"{rdir}/202205_monthly_precipitation_amount_1hour_Accumulation.nc"
Convert.nctoTiff(nc_file, path, time_var_name="time1", prefix="Amazon-S3-ERA5")
#%%
"""
In this part we will create a spatial index for each cell in the downloaded rasters, and convert the rasters into a 
pandas dataframe, 
- First spatial indexing method, we will create an index raster with an id for each cell that will refer to the row in 
the dataframe to be able to locate the value and associate it to a specific location.
- Second method we will create a point/polygon geometry at the center of each cell so we can query the whole raster but 
using geometries relations
- Third we will use the H3 indexing method so we can assign a hexadecimal index (for each resolution 0-15) so we can 
use the different resolution of H3 tfor faster querying of data. 
- The creating of the polygon index will take a bit long time (3 min) but it is optional since we can only use the 
point index
- So the point/polygon and raster index will be created only once since all rasters have the same dimensions
- After converting all rasters into a dataframe ewe will use the point index to get the H3 index for all points for 
the 16 resolutions and add them to the same dataframe.
- In the last step we will save the dataframe as a parquet data type. 
"""
def create_metadata(src: Dataset, path: str):
    """Create the index raster and the geometry file (both point and polygon)

    Parameters
    ----------
    src: [Dataset]
        gdal Dataset.
    path: [str]
        path to where the metadata are going to be saved.
    """
    # first create the raster
    logger.info("First step (creating index raster)")
    arr = src.ReadAsArray()
    rows, cols = arr.shape

    unique_nums = list(range(1, rows * cols + 1))
    arr = np.array(unique_nums)
    new_arr = np.reshape(arr, (rows, cols))
    dst= Raster.rasterLike(src, new_arr, driver="MEM")
    Raster.saveRaster(dst, f"{path}/index.tif")
    # second create the point index file from the index raster
    logger.info("Second step (Create index point geometry file)")
    logger.info("The Point geometry will be created at the center of each cell so we can query the cells values by "
                "indexing the cell center location")
    gdf = Convert.rasterToGeoDataFrame(dst, add_geometry="point")
    gdf.to_parquet(f"{path}/index_points.parquet", index=False, compression='gzip')
    # third create the polygon index file from the index raster
    logger.info("Third step (Create index polygon geometry file)")
    gdf = Convert.rasterToGeoDataFrame(dst, add_geometry="polygon")
    gdf.to_parquet(f"{path}/index_polygon.parquet", index=False, compression='gzip')
    logger.info("Creating index data has finished successfully")
#%%
search_criteria = "*.tif"
file_list = glob.glob(os.path.join(f"{path}/", search_criteria))
fname = file_list[0]
src = gdal.Open(fname)
meta_data_path = f"{rdir}/metadata"
create_metadata(src, meta_data_path)
rows = src.RasterYSize
cols = src.RasterXSize
#%% convert the downloaded data into dataframes.
"""
In this part we will convert the rasters into Dataframe using the convert module in the Pyramids package.
"""
fmt = "%Y.%m.%d.%H.%M.%S"
hourly_fmt = "%Y-%m-%d-%H"
data = np.zeros(shape=(rows * cols, len(file_list))) * np.nan
file_order = []
for i, fname in enumerate(file_list):
    date_fragments = fname.split("_")[-1][:-4]
    file_order.append(dt.datetime.strptime(date_fragments, fmt))
    data[:, i] = Convert.rasterToGeoDataFrame(fname).values.reshape((rows*cols))

col_names = [date_i.strftime(hourly_fmt) for date_i in file_order]
# making the date as an index makes the files size grows drastically
df = pd.DataFrame(data, columns=col_names)
df.to_parquet(f"{rdir}/files/data.parquet", index=False, compression='gzip')
#%% indexing the data with h3
"""
read the parquet file containing the extracted cell values and generating the H3 index for each resolution level.
"""
df = pd.read_parquet(f"{rdir}/files/data.parquet")
# read the point index file and index
point_index = gpd.read_parquet(f"{rdir}/metadata/index_points.parquet")
coords = [(i.x, i.y) for i in point_index["geometry"]]

for res in range(16):
    print(f"H3 resolution :{res}")
    hex = [H3.geometryToIndex(xy[1], xy[0], res) for xy in coords]
    # hex = H3.getIndex(point_index, res)
    df[f"{res}"] = hex

df.to_parquet(f"{rdir}/files/data.parquet", index=False, compression='gzip')
#%%
"""
Now all the preprocessing tasks is done and you have the data saved in the parquet data format, we can read it and 
query it.
"""
df = pd.read_parquet(f"{rdir}/files/data.parquet")

date = "2022-05-05-00"
fmt = "%Y-%m-%d-%H"
res = 5

dt.datetime.strptime(date, fmt)
hex = df.loc[0, f"{res}"]

# def get_geometry
attr = H3.getAttributes(hex)
