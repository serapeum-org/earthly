# Climate Hazards Center (CHC)

Climate Hazards Center data sources via FTP. The backend covers CHC's
full product line — CHIRPS / CHIRP precipitation, CHIRTS temperature
& humidity, CHIRPS-GEFS ensemble forecasts, CHPclim v2 climatology,
WBGT, SPI / SPEI drought indices, and CHC_CMIP6 scenario deltas — all
served from `data.chc.ucsb.edu` over anonymous FTP and discoverable
through the catalog.

The class is named `CHIRPS` for brand-recognition reasons; pass the
catalog dataset key under `variables=` to address a non-CHIRPS
product (e.g.
`variables={"chirtsdaily-tmax": ["tmax"]}`).

::: earthlens.chc.CHIRPS

::: earthlens.chc.Catalog
