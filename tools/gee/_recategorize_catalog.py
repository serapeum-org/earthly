"""Regroup the GEE catalog from per-provider files into per-category files.

Walks every ``catalog/*.yaml`` (except ``_index.yaml``) in the bundled
catalog, classifies each dataset stanza into one of a small fixed set of
data-type categories (``optical-multispectral``, ``sar-radar``,
``climate-reanalysis``, ``precipitation``, ``elevation-terrain``,
``land-cover-change``, ``atmosphere-chemistry``, ``hydrology-water``,
``community``, ``other``) and re-writes the catalog directory grouped by
category. Per-stanza text is sliced verbatim (preserving comments,
ordering, and formatting); only the per-file headers and the
``datasets:`` wrapper are re-emitted.

Classification uses, in priority order:

1. Exact / prefix matches on the asset id (highest signal).
2. Title keyword matches.
3. Provider field hints.
4. A safety net that drops to ``other`` (printed at the end so the long
   tail can be reviewed).

Run with ``--dry-run`` to print the bucket distribution without
touching disk. Idempotent: running it on an already-categorised tree
produces the same output.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO / "src" / "earthlens" / "gee" / "catalog"

# All categories we emit, in display / file-order. Anything not matched
# by the rules table lands in ``other``.
CATEGORIES = [
    "optical-multispectral",
    "sar-radar",
    "climate-reanalysis",
    "precipitation",
    "elevation-terrain",
    "land-cover-change",
    "atmosphere-chemistry",
    "hydrology-water",
    "community",
    "other",
]


# Each rule is (kind, needle, category). `kind` is one of:
#   "id_prefix" — asset_id startswith needle
#   "id_contains" — needle in asset_id
#   "title_kw"  — needle (lowercased) in title (lowercased)
# Rules are tried in order; first match wins.
_RULES: list[tuple[str, str, str]] = [
    # --- community (projects/...) ---
    ("id_prefix", "projects/", "community"),

    # --- population / boundaries / infrastructure / vector POI (route to 'other' explicitly) ---
    ("id_prefix", "CIESIN/GPWv", "other"),
    ("id_prefix", "WorldPop/", "other"),
    ("id_prefix", "DOE/ORNL/LandScan_HD/", "other"),
    ("id_prefix", "TIGER/", "other"),
    ("id_prefix", "USDOS/LSIB", "other"),
    ("id_prefix", "WCMC/WD", "other"),
    ("id_prefix", "UN/Geodata/", "other"),
    ("id_prefix", "WM/geoLab/geoBoundaries/", "other"),
    ("id_prefix", "FAO/GAUL/", "other"),
    ("id_prefix", "FAO/GAUL_SIMPLIFIED", "other"),
    ("id_prefix", "EPA/Ecoregions/", "other"),
    ("id_prefix", "BNETD/landcover/", "land-cover-change"),
    ("id_prefix", "WRI/GPPD/", "other"),
    ("id_prefix", "EDF/OGIM/", "other"),
    ("id_prefix", "iNaturalist/", "other"),
    ("id_prefix", "overture-maps/", "other"),
    ("id_prefix", "GOOGLE/AirView/", "other"),
    ("id_prefix", "GOOGLE/Research/open-buildings", "other"),
    ("id_prefix", "GOOGLE/SATELLITE_EMBEDDING/", "other"),
    ("id_prefix", "NOAA/IBTrACS/", "other"),
    ("id_prefix", "NOAA/NHC/HURDAT2/", "other"),
    ("id_prefix", "ISDASOIL/", "other"),
    ("id_prefix", "ISRIC/SoilGrids", "other"),
    ("id_prefix", "OpenLandMap/SOL/", "other"),
    ("id_prefix", "CSIRO/SLGA", "other"),
    ("id_prefix", "USDA/SOLUS100", "other"),
    ("id_prefix", "JRC/LUCAS", "other"),

    # --- precipitation (resolve before optical so MODIS/CHIRPS/IMERG hit) ---
    ("id_prefix", "UCSB-CHG/CHIRPS", "precipitation"),
    ("id_prefix", "UCSB-CHG/CHIRP/", "precipitation"),
    ("id_prefix", "NASA/GPM_L3/", "precipitation"),
    ("id_prefix", "NASA/GPM/", "precipitation"),
    ("id_prefix", "JAXA/GPM_L3/", "precipitation"),
    ("id_prefix", "NOAA/PERSIANN", "precipitation"),
    ("id_prefix", "NOAA/CPC/", "precipitation"),
    ("id_prefix", "NOAA/NCEP_RE/", "precipitation"),
    ("id_prefix", "TRMM/", "precipitation"),
    ("id_prefix", "JAXA/GCOM-W1/L3/GSMaP", "precipitation"),
    ("id_prefix", "NOAA/NESDIS/SSM/", "precipitation"),
    ("id_contains", "/3B-DAY/", "precipitation"),
    ("title_kw", "precipitation", "precipitation"),
    ("title_kw", "rainfall", "precipitation"),
    ("title_kw", "rain rate", "precipitation"),

    # --- evapotranspiration (resolve before catch-all 'climate' / 'optical' kicks in) ---
    ("id_prefix", "OpenET/", "hydrology-water"),

    # --- glaciers / marine / surface water that needs explicit hits ---
    ("id_prefix", "GLIMS/", "hydrology-water"),
    ("id_prefix", "GLCF/GLS_WATER", "hydrology-water"),
    ("id_prefix", "COPERNICUS/MARINE/", "hydrology-water"),
    ("id_prefix", "WRI/Aqueduct_Water_Risk/", "hydrology-water"),
    ("id_prefix", "UQ/murray/Intertidal/", "hydrology-water"),
    ("id_prefix", "JCU/Murray/GIC/", "hydrology-water"),

    # --- elevation / terrain / bathymetry / lidar height ---
    ("id_prefix", "USGS/SRTMGL1", "elevation-terrain"),
    ("id_prefix", "USGS/3DEP", "elevation-terrain"),
    ("id_prefix", "USGS/NED", "elevation-terrain"),
    ("id_prefix", "USGS/GMTED2010", "elevation-terrain"),
    ("id_prefix", "USGS/GTOPO30", "elevation-terrain"),
    ("id_prefix", "CGIAR/SRTM90_V4", "elevation-terrain"),
    ("id_prefix", "NASA/NASADEM_HGT", "elevation-terrain"),
    ("id_prefix", "NASA/ASTER_GED", "elevation-terrain"),
    ("id_prefix", "NASA/JPL/global_forest_canopy_height_2005", "elevation-terrain"),
    ("id_prefix", "NASA/MEASURES/", "elevation-terrain"),
    ("id_prefix", "JAXA/ALOS/AW3D30", "elevation-terrain"),
    ("id_prefix", "JAXA/ALOS/AVNIR-2", "optical-multispectral"),
    ("id_prefix", "COPERNICUS/DEM/", "elevation-terrain"),
    ("id_prefix", "AHN/", "elevation-terrain"),
    ("id_prefix", "MERIT/DEM/", "elevation-terrain"),
    ("id_prefix", "MERIT/Hydro/", "elevation-terrain"),
    ("id_prefix", "MERIT/Hydro_reduced/", "elevation-terrain"),
    ("id_prefix", "NOAA/NGDC/ETOPO", "elevation-terrain"),
    ("id_prefix", "GEBCO/", "elevation-terrain"),
    ("id_prefix", "OSU/GIMP/", "elevation-terrain"),
    ("id_prefix", "BYU/Greenland", "elevation-terrain"),
    ("id_prefix", "CPOM/", "elevation-terrain"),
    ("id_prefix", "DLR/WSF/", "land-cover-change"),
    ("id_prefix", "Oxford/MAP/", "land-cover-change"),
    ("id_prefix", "MINES_PARISTECH/", "elevation-terrain"),
    ("id_prefix", "MERIT/Hydro", "elevation-terrain"),
    ("id_prefix", "NASA/GEDI/", "elevation-terrain"),
    ("id_prefix", "NASA/ICESAT2/", "elevation-terrain"),
    ("id_prefix", "LARSE/GEDI/", "elevation-terrain"),
    ("id_prefix", "AU/GA/DEM_1SEC", "elevation-terrain"),
    ("id_prefix", "UMN/PGC/REMA", "elevation-terrain"),
    ("id_prefix", "UK/EA/ENGLAND_1M_TERRAIN", "elevation-terrain"),
    ("id_prefix", "CSP/ERGo/", "elevation-terrain"),
    ("title_kw", "digital elevation", "elevation-terrain"),
    ("title_kw", "dem ", "elevation-terrain"),
    ("title_kw", "bathymetry", "elevation-terrain"),
    ("title_kw", "topograph", "elevation-terrain"),
    ("title_kw", "canopy height", "elevation-terrain"),
    ("title_kw", "ice mass", "elevation-terrain"),
    ("title_kw", "ice surface", "elevation-terrain"),

    # --- SAR / radar ---
    ("id_prefix", "COPERNICUS/S1_", "sar-radar"),
    ("id_prefix", "COPERNICUS/S1", "sar-radar"),
    ("id_prefix", "JAXA/ALOS/PALSAR", "sar-radar"),
    ("id_prefix", "ASF/", "sar-radar"),
    ("id_prefix", "OPERA/RTC/L2_V1/S1", "sar-radar"),
    ("id_prefix", "Earth_Big_Data/GLOBAL_SEASONAL_S1/", "sar-radar"),
    ("title_kw", "synthetic aperture radar", "sar-radar"),
    ("title_kw", "palsar", "sar-radar"),
    ("title_kw", "sar mosaic", "sar-radar"),

    # --- atmosphere / air chemistry ---
    ("id_prefix", "COPERNICUS/S5P/", "atmosphere-chemistry"),
    ("id_prefix", "ECMWF/CAMS/", "atmosphere-chemistry"),
    ("id_prefix", "NASA/EMIT/", "atmosphere-chemistry"),
    ("id_prefix", "EDF/MethaneSAT/", "atmosphere-chemistry"),
    ("id_prefix", "NASA/TEMPO/", "atmosphere-chemistry"),
    ("id_prefix", "NASA/GEOS-CF/", "atmosphere-chemistry"),
    ("id_prefix", "NOAA/CDR/PATMOSX", "atmosphere-chemistry"),
    ("id_prefix", "TOMS/", "atmosphere-chemistry"),
    ("id_prefix", "NASA/GSFC/MERRA/aer/", "atmosphere-chemistry"),
    ("id_prefix", "NASA/MEaSUREs/GLDAS/", "hydrology-water"),
    ("id_prefix", "MODIS/061/MOD08_M3", "atmosphere-chemistry"),
    ("id_prefix", "MODIS/061/MCD19A2", "atmosphere-chemistry"),
    ("id_prefix", "MODIS/006/MCD19A2", "atmosphere-chemistry"),
    ("id_prefix", "MODIS/006/MOD08", "atmosphere-chemistry"),
    ("title_kw", "aerosol", "atmosphere-chemistry"),
    ("title_kw", "tropomi", "atmosphere-chemistry"),
    ("title_kw", "ozone", "atmosphere-chemistry"),
    ("title_kw", "methane", "atmosphere-chemistry"),
    ("title_kw", "carbon monoxide", "atmosphere-chemistry"),
    ("title_kw", "nitrogen dioxide", "atmosphere-chemistry"),
    ("title_kw", "no2 ", "atmosphere-chemistry"),
    ("title_kw", "so2 ", "atmosphere-chemistry"),
    ("title_kw", "ch4 ", "atmosphere-chemistry"),
    ("title_kw", "co2 ", "atmosphere-chemistry"),
    ("title_kw", "formaldehyde", "atmosphere-chemistry"),
    ("title_kw", "atmospheric composition", "atmosphere-chemistry"),
    ("title_kw", "ghg ", "atmosphere-chemistry"),

    # --- climate reanalysis / model output ---
    ("id_prefix", "ECMWF/ERA5", "climate-reanalysis"),
    ("id_prefix", "ECMWF/", "climate-reanalysis"),
    ("id_prefix", "NASA/GSFC/MERRA/", "climate-reanalysis"),
    ("id_prefix", "NASA/GLDAS/", "hydrology-water"),
    ("id_prefix", "NASA/FLDAS/", "hydrology-water"),
    ("id_prefix", "NASA/NLDAS/", "hydrology-water"),
    ("id_prefix", "NASA/NEX-DCP30", "climate-reanalysis"),
    ("id_prefix", "NASA/NEX-GDDP", "climate-reanalysis"),
    ("id_prefix", "NOAA/CFSV2/", "climate-reanalysis"),
    ("id_prefix", "NOAA/CFSR/", "climate-reanalysis"),
    ("id_prefix", "NOAA/GFS", "climate-reanalysis"),
    ("id_prefix", "NOAA/NCEP_RE/", "climate-reanalysis"),
    ("id_prefix", "NCEP_RE/", "climate-reanalysis"),
    ("id_prefix", "IDAHO_EPSCOR/GRIDMET", "climate-reanalysis"),
    ("id_prefix", "IDAHO_EPSCOR/MACAv2_METDATA", "climate-reanalysis"),
    ("id_prefix", "IDAHO_EPSCOR/TERRACLIMATE", "climate-reanalysis"),
    ("id_prefix", "IDAHO_EPSCOR/PDSI", "climate-reanalysis"),
    ("id_prefix", "IDAHO_EPSCOR/EDDI", "climate-reanalysis"),
    ("id_prefix", "OREGONSTATE/PRISM/", "climate-reanalysis"),
    ("id_prefix", "WORLDCLIM/V1/", "climate-reanalysis"),
    ("id_prefix", "NASA/ORNL/DAYMET_V", "climate-reanalysis"),
    ("id_prefix", "NASA/ECOSTRESS/", "climate-reanalysis"),
    ("id_prefix", "SNU/ESL/BESS/", "climate-reanalysis"),
    ("id_prefix", "IPCC/AR6/", "climate-reanalysis"),
    ("id_prefix", "CSIC/SPEI/", "climate-reanalysis"),
    ("id_prefix", "GRIDMET/DROUGHT", "climate-reanalysis"),
    ("id_prefix", "OpenLandMap/CLM/", "climate-reanalysis"),
    ("id_prefix", "NOAA/CDR/HEAT_FLUXES", "climate-reanalysis"),
    ("id_prefix", "NOAA/CDR/ATMOS_NEAR_SURFACE", "climate-reanalysis"),
    ("id_prefix", "NOAA/CDR/GRIDSAT-B1", "climate-reanalysis"),
    ("id_prefix", "WHRC/", "land-cover-change"),
    ("id_prefix", "UCSB-CHG/CHIRTS", "climate-reanalysis"),
    ("id_prefix", "UCSB-CHG/CHC_CMIP6", "climate-reanalysis"),
    ("id_prefix", "NCAR/", "climate-reanalysis"),
    ("title_kw", "reanalysis", "climate-reanalysis"),
    ("title_kw", "era5", "climate-reanalysis"),
    ("title_kw", "merra", "climate-reanalysis"),
    ("title_kw", "cmip", "climate-reanalysis"),
    ("title_kw", "climate forecast", "climate-reanalysis"),
    ("title_kw", "drought index", "climate-reanalysis"),

    # --- hydrology / water / ocean / snow / ice ---
    ("id_prefix", "JRC/GSW1_", "hydrology-water"),
    ("id_prefix", "JRC/GSW/", "hydrology-water"),
    ("id_prefix", "NASA/SMAP/", "hydrology-water"),
    ("id_prefix", "NASA_USDA/HSL/SMAP", "hydrology-water"),
    ("id_prefix", "NASA_USDA/HSL/", "hydrology-water"),
    ("id_prefix", "NASA/GRACE/", "hydrology-water"),
    ("id_prefix", "NASA/JPL/HYCOM", "hydrology-water"),
    ("id_prefix", "HYCOM/", "hydrology-water"),
    ("id_prefix", "NOAA/CDR/SSMI", "hydrology-water"),
    ("id_prefix", "NOAA/CDR/SST", "hydrology-water"),
    ("id_prefix", "NOAA/CDR/OISST", "hydrology-water"),
    ("id_prefix", "NOAA/CDR/HEAT_CONTENT", "hydrology-water"),
    ("id_prefix", "NOAA/NWS/RTMA", "hydrology-water"),
    ("id_prefix", "NOAA/NWS/QPE", "hydrology-water"),
    ("id_prefix", "NASA/OCEANDATA/", "hydrology-water"),
    ("id_prefix", "MODIS/006/MOD10", "hydrology-water"),
    ("id_prefix", "MODIS/061/MOD10", "hydrology-water"),
    ("id_prefix", "MODIS/006/MYD10", "hydrology-water"),
    ("id_prefix", "MODIS/061/MYD10", "hydrology-water"),
    ("id_prefix", "MODIS/006/MOD29", "hydrology-water"),
    ("id_prefix", "MODIS/061/MOD29", "hydrology-water"),
    ("id_prefix", "NSIDC/", "hydrology-water"),
    ("id_prefix", "FAO/WAPOR", "hydrology-water"),
    ("id_prefix", "TOMS/", "atmosphere-chemistry"),
    ("id_prefix", "WWF/HydroSHEDS/", "hydrology-water"),
    ("id_prefix", "WWF/HydroATLAS/", "hydrology-water"),
    ("id_prefix", "WWF/FreeFlowingRivers", "hydrology-water"),
    ("id_prefix", "WRI/AQUEDUCT_FLOODS_", "hydrology-water"),
    ("id_prefix", "JRC/CEMS_GLOFAS/", "hydrology-water"),
    ("id_prefix", "MERIT/Hydro_", "hydrology-water"),
    ("id_prefix", "GLOBAL_FLOOD_DB/", "hydrology-water"),
    ("title_kw", "soil moisture", "hydrology-water"),
    ("title_kw", "snow cover", "hydrology-water"),
    ("title_kw", "snow depth", "hydrology-water"),
    ("title_kw", "sea ice", "hydrology-water"),
    ("title_kw", "sea surface", "hydrology-water"),
    ("title_kw", "sst ", "hydrology-water"),
    ("title_kw", "ocean colour", "hydrology-water"),
    ("title_kw", "ocean color", "hydrology-water"),
    ("title_kw", "chlorophyll", "hydrology-water"),
    ("title_kw", "evapotranspiration", "hydrology-water"),
    ("title_kw", "river discharge", "hydrology-water"),
    ("title_kw", "streamflow", "hydrology-water"),
    ("title_kw", "flood", "hydrology-water"),
    ("title_kw", "watershed", "hydrology-water"),
    ("title_kw", "water surface", "hydrology-water"),
    ("title_kw", "surface water", "hydrology-water"),

    # --- land cover / change / forest / cropland / fire / built / vegetation indices ---
    ("id_prefix", "UMD/hansen/", "land-cover-change"),
    ("id_prefix", "GLAD/", "land-cover-change"),
    ("id_prefix", "UMD/GLAD/", "land-cover-change"),
    ("id_prefix", "UMD/", "land-cover-change"),
    ("id_prefix", "USGS/NLCD", "land-cover-change"),
    ("id_prefix", "USGS/GAP/", "land-cover-change"),
    ("id_prefix", "USGS/GFSAD1000", "land-cover-change"),
    ("id_prefix", "USDA/NASS/CDL", "land-cover-change"),
    ("id_prefix", "AAFC/ACI", "land-cover-change"),
    ("id_prefix", "ESA/WorldCover/", "land-cover-change"),
    ("id_prefix", "ESA/CCI/", "land-cover-change"),
    ("id_prefix", "ESA/GlobCover", "land-cover-change"),
    ("id_prefix", "COPERNICUS/Landcover/", "land-cover-change"),
    ("id_prefix", "COPERNICUS/CORINE/", "land-cover-change"),
    ("id_prefix", "MODIS/006/MCD12", "land-cover-change"),
    ("id_prefix", "MODIS/061/MCD12", "land-cover-change"),
    ("id_prefix", "MODIS/006/MOD13", "land-cover-change"),
    ("id_prefix", "MODIS/061/MOD13", "land-cover-change"),
    ("id_prefix", "MODIS/006/MYD13", "land-cover-change"),
    ("id_prefix", "MODIS/061/MYD13", "land-cover-change"),
    ("id_prefix", "MODIS/006/MOD15", "land-cover-change"),
    ("id_prefix", "MODIS/061/MOD15", "land-cover-change"),
    ("id_prefix", "MODIS/006/MYD15", "land-cover-change"),
    ("id_prefix", "MODIS/061/MYD15", "land-cover-change"),
    ("id_prefix", "MODIS/006/MOD17", "land-cover-change"),
    ("id_prefix", "MODIS/061/MOD17", "land-cover-change"),
    ("id_prefix", "MODIS/006/MOD44", "land-cover-change"),
    ("id_prefix", "MODIS/061/MOD44", "land-cover-change"),
    ("id_prefix", "MODIS/006/MCD43", "land-cover-change"),
    ("id_prefix", "MODIS/061/MCD43", "land-cover-change"),
    ("id_prefix", "MODIS/006/MOD14", "land-cover-change"),
    ("id_prefix", "MODIS/061/MOD14", "land-cover-change"),
    ("id_prefix", "MODIS/006/MCD64", "land-cover-change"),
    ("id_prefix", "MODIS/061/MCD64", "land-cover-change"),
    ("id_prefix", "FIRMS", "land-cover-change"),
    ("id_prefix", "JRC/GWIS/", "land-cover-change"),
    ("id_prefix", "JRC/GHSL/", "land-cover-change"),
    ("id_prefix", "JRC/GFC2020", "land-cover-change"),
    ("id_prefix", "JRC/D5/", "land-cover-change"),
    ("id_prefix", "GOOGLE/DYNAMICWORLD", "land-cover-change"),
    ("id_prefix", "GOOGLE/Research/open-buildings", "other"),
    ("id_prefix", "NASA/ORNL/biomass_carbon_density", "land-cover-change"),
    ("id_prefix", "OXFORD/MAP/TCB", "land-cover-change"),
    ("id_prefix", "OXFORD/MAP/TCW", "land-cover-change"),
    ("id_prefix", "BIOPAMA/", "land-cover-change"),
    ("id_prefix", "BNETD/", "land-cover-change"),
    ("id_prefix", "BNU/FGS/CCNL/", "land-cover-change"),
    ("id_prefix", "Tsinghua/FROM-GLC", "land-cover-change"),
    ("id_prefix", "Oxford/MAP/", "land-cover-change"),
    ("id_prefix", "NICFI/", "optical-multispectral"),
    ("id_prefix", "ACA/reef_habitat/", "land-cover-change"),
    ("id_prefix", "ESA/WorldCereal/", "land-cover-change"),
    ("id_prefix", "USFS/GTAC/LCMS/", "land-cover-change"),
    ("id_prefix", "USFS/GTAC/MTBS/", "land-cover-change"),
    ("id_prefix", "USFS/GTAC/TreeMap/", "land-cover-change"),
    ("id_prefix", "UMT/Climate/IrrMapper", "land-cover-change"),
    ("id_prefix", "UMT/NTSG/", "land-cover-change"),
    ("id_prefix", "Tsinghua/DESS/", "land-cover-change"),
    ("id_prefix", "RUB/RUBCLIM/LCZ/", "land-cover-change"),
    ("id_prefix", "FAO/SOFO/", "land-cover-change"),
    ("id_prefix", "FAO/GHG/", "land-cover-change"),
    ("id_prefix", "WRI/SBTN/", "land-cover-change"),
    ("id_prefix", "WRI/GFW/FORMA/", "land-cover-change"),
    ("id_prefix", "BLM/AIM/", "land-cover-change"),
    ("id_prefix", "CSP/HM/", "land-cover-change"),
    ("id_prefix", "NOAA/DMSP-OLS/", "land-cover-change"),
    ("id_prefix", "NOAA/CDR/AVHRR/LAI_FAPAR", "land-cover-change"),
    ("id_prefix", "IUCN/GlobalEcosystemTypology", "land-cover-change"),
    ("id_prefix", "OpenLandMap/PNV/", "land-cover-change"),
    ("id_prefix", "NASA/VIIRS/002/VNP46A2", "land-cover-change"),
    ("id_prefix", "GOOGLE/GLOBAL_CCDC/", "land-cover-change"),
    ("id_prefix", "GFW/", "land-cover-change"),
    ("id_prefix", "FORMA/", "land-cover-change"),
    ("title_kw", "land cover", "land-cover-change"),
    ("title_kw", "land use", "land-cover-change"),
    ("title_kw", "ndvi", "land-cover-change"),
    ("title_kw", "evi ", "land-cover-change"),
    ("title_kw", "vegetation indices", "land-cover-change"),
    ("title_kw", "vegetation continuous", "land-cover-change"),
    ("title_kw", "forest", "land-cover-change"),
    ("title_kw", "cropland", "land-cover-change"),
    ("title_kw", "crop type", "land-cover-change"),
    ("title_kw", "burned area", "land-cover-change"),
    ("title_kw", "fire ", "land-cover-change"),
    ("title_kw", "lai ", "land-cover-change"),
    ("title_kw", "fpar", "land-cover-change"),
    ("title_kw", "biomass", "land-cover-change"),
    ("title_kw", "tree cover", "land-cover-change"),
    ("title_kw", "built-up", "land-cover-change"),
    ("title_kw", "settlement", "land-cover-change"),
    ("title_kw", "urban", "land-cover-change"),
    ("title_kw", "phenology", "land-cover-change"),
    ("title_kw", "albedo", "land-cover-change"),

    # --- optical multispectral (catch-all for remaining MODIS / Landsat / Sentinel-2 / VIIRS / GOES) ---
    ("id_prefix", "LANDSAT/", "optical-multispectral"),
    ("id_prefix", "COPERNICUS/S2", "optical-multispectral"),
    ("id_prefix", "COPERNICUS/S3", "optical-multispectral"),
    ("id_prefix", "MODIS/", "optical-multispectral"),
    ("id_prefix", "NOAA/VIIRS/", "optical-multispectral"),
    ("id_prefix", "NOAA/GOES/", "optical-multispectral"),
    ("id_prefix", "NASA/HLS/", "optical-multispectral"),
    ("id_prefix", "ASTER/", "optical-multispectral"),
    ("id_prefix", "VITO/PROBAV/", "optical-multispectral"),
    ("id_prefix", "NASA/MEASURES/GFCC", "land-cover-change"),
    ("id_prefix", "RESOLVE/", "land-cover-change"),
    ("id_prefix", "SKYSAT/", "optical-multispectral"),
    ("id_prefix", "JAXA/GCOM-C/", "optical-multispectral"),
    ("id_prefix", "NASA/GIMMS/", "land-cover-change"),
    ("id_prefix", "AIRBUS/SPOT_2_4_5/", "optical-multispectral"),
    ("id_prefix", "EO1/HYPERION", "optical-multispectral"),
    ("id_prefix", "NASA/LANCE/", "optical-multispectral"),
    ("id_prefix", "NASA/VIIRS/", "optical-multispectral"),
    ("id_prefix", "NOAA/CDR/AVHRR/SR", "optical-multispectral"),
    ("id_prefix", "USDA/NAIP/", "optical-multispectral"),
    ("id_prefix", "USGS/LIMA/", "optical-multispectral"),
    ("id_prefix", "WHBU/NBAR", "optical-multispectral"),
    ("id_prefix", "TUBerlin/BigEarthNet/", "optical-multispectral"),
    ("id_prefix", "Netherlands/", "optical-multispectral"),
    ("id_prefix", "Estonia/", "optical-multispectral"),
    ("id_prefix", "Finland/", "optical-multispectral"),
    ("id_prefix", "Latvia/", "optical-multispectral"),
    ("id_prefix", "Slovakia/", "optical-multispectral"),
    ("id_prefix", "Spain/PNOA/", "optical-multispectral"),
    ("id_prefix", "Switzerland/SWISSIMAGE/", "optical-multispectral"),
    ("id_prefix", "Germany/Brandenburg/", "optical-multispectral"),
    ("id_prefix", "GOOGLE/CLOUD_SCORE_PLUS/", "optical-multispectral"),
]


_STANZA_RE = re.compile(r"^  (?P<asset>[A-Za-z0-9_./\-]+):\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^    title:\s*(.+?)\s*$", re.MULTILINE)


def _categorise(asset_id: str, title: str) -> str:
    """Return the bucket name for one (asset_id, title) pair."""
    title_lc = title.lower()
    for kind, needle, category in _RULES:
        if kind == "id_prefix" and asset_id.startswith(needle):
            return category
        if kind == "id_contains" and needle in asset_id:
            return category
        if kind == "title_kw" and needle in title_lc:
            return category
    return "other"


def _extract_title(stanza: str) -> str:
    m = _TITLE_RE.search(stanza)
    if not m:
        return ""
    t = m.group(1).strip()
    if t.startswith("'") and t.endswith("'"):
        t = t[1:-1].replace("''", "'")
    elif t.startswith('"') and t.endswith('"'):
        t = t[1:-1]
    return t


def _split_stanzas_in_file(text: str) -> list[tuple[str, str]]:
    """Slice a per-provider file's body into `(asset_id, stanza_text)` pairs."""
    # Strip leading per-file comment headers + the `datasets:` line.
    m = re.search(r"^datasets:\s*\n", text, re.MULTILINE)
    if not m:
        return []
    body = text[m.end():]
    matches = list(_STANZA_RE.finditer(body))
    pairs: list[tuple[str, str]] = []
    for i, mm in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        pairs.append((mm.group("asset"), body[mm.start():end]))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print bucket counts only")
    parser.add_argument("--show-other", action="store_true", help="list every asset id that lands in 'other'")
    args = parser.parse_args()

    files = sorted(p for p in CATALOG_DIR.glob("*.yaml") if p.name != "_index.yaml")
    if not files:
        print(f"no catalog *.yaml files under {CATALOG_DIR}", file=sys.stderr)
        return 1

    by_cat: dict[str, list[tuple[str, str]]] = defaultdict(list)
    asset_ids: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for asset_id, stanza in _split_stanzas_in_file(text):
            title = _extract_title(stanza)
            cat = _categorise(asset_id, title)
            by_cat[cat].append((asset_id, stanza))
            asset_ids.append(asset_id)

    total = sum(len(v) for v in by_cat.values())
    print(f"classified {total} datasets across {len(files)} files:")
    for cat in CATEGORIES:
        n = len(by_cat.get(cat, []))
        print(f"  {n:5d}  {cat}.yaml")
    other = sorted(aid for aid, _ in by_cat.get("other", []))
    if args.show_other and other:
        print(f"\n--- 'other' bucket ({len(other)}) ---")
        for aid in other:
            print(f"  {aid}")
    if args.dry_run:
        return 0

    # Read the existing _index.yaml verbatim so we don't churn its order.
    index_text = (CATALOG_DIR / "_index.yaml").read_text(encoding="utf-8")

    # Delete old per-provider files (everything except _index.yaml).
    for path in files:
        path.unlink()

    for cat in CATEGORIES:
        stanzas = by_cat.get(cat, [])
        if not stanzas:
            continue
        out = CATALOG_DIR / f"{cat}.yaml"
        parts = [
            f"# Auto-grouped slice of the GEE catalog: {cat}.\n",
            f"# {len(stanzas)} dataset(s). Edit in place; the loader merges every\n",
            "# *.yaml file in this directory into one Catalog at import time.\n",
            "\n",
            "datasets:\n",
        ]
        for _, stanza in sorted(stanzas, key=lambda p: p[0]):
            parts.append(stanza)
            if not stanza.endswith("\n"):
                parts.append("\n")
        out.write_text("".join(parts), encoding="utf-8")

    # _index.yaml is preserved as-is.
    (CATALOG_DIR / "_index.yaml").write_text(index_text, encoding="utf-8")

    print(f"\nwrote {len([c for c in CATEGORIES if by_cat.get(c)])} category files under {CATALOG_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
