# Change Log

## 0.5.0 (2026-05-13)

### Feat

- **gee**: add the Google Earth Engine backend — `GEE(AbstractDataSource)` with
  service-account auth, `ee.ImageCollection` build/composite, and `getDownloadURL` /
  `ee.batch.Export.image.toDrive` / `toCloudStorage` exports (`export_via`,
  32768-px synchronous cap); a pydantic YAML catalog (`gee_data_catalog.yaml` —
  `Catalog` / `Dataset` / `Band` / `Cadence` / `Extent`, the full ~1100-entry
  `available_datasets:` index from the Earth Engine STAC plus a curated `datasets:`
  map); `EarthEngineAuth`; Shapely/GeoDataFrame → `ee` geometry helpers; the
  `tools/gee/refresh_gee_catalog.py` / `tools/gee/audit_gee_datasets.py` STAC tooling; the
  `"gee"` / `"google-earth-engine"` keys in the `EarthLens` facade (with a
  `**backend_kwargs` passthrough; `download()` now returns the backend's result); and
  the `docs/reference/google-earth-engine/` documentation.
- **base**: factor the duplicate-key-rejecting YAML loader into
  `earthlens/base/yaml_loader.py`, shared by the ECMWF and GEE catalogs.

### Refactor

- **gee**: remove the legacy stub modules (`gee.py` → `auth.py`, `dataset.py`,
  `data.py`, `dataset_catalog.json`, `imagecollection.py`, `images.py`).

## 0.4.0 (2026-05-10)

### Refactor

- rename earthly -> earthlens and fix CDS notebooks (#47)

## 0.3.0 (2026-05-07)

### Feat

- **ecmwf**: migrate from legacy MARS API to cdsapi and rebuild backend, catalog, and tooling (#30)

### Fix

- **pyproject**: update pyramids-gis dependency and add commitizen configuration

## 0.2.2 (2023-01-29)

- Add documentation
- Bump up pyramids versions

## 0.2.1 (2023-01-25)

- Add Amazon S3 data source and catalog for the data available in ERA5 bucket (ERA5 only tested)
- Replace utility functions with the serapeum_utils package

## 0.2.0 (2023-01-15)

- Bump up numpy and pyramids versions
- Create an abstract class for datasource and catalog as a blueprint for all data sources
- Test all classes in CI
- Use pathlib to deal with paths

## 0.1.7 (2022-12-26)

- Fix PyPI package names in the requirements.txt file
- Fix python version in requirements.txt

## 0.1.6 (2022-12-26)

- Use environment.yaml and requirements.txt instead of pyproject.toml and replace poetry env by conda env
- Lock numpy to 1.23.5

## 0.1.5 (2022-12-07)

- First release on PyPI
- Add ECMWF data catalog
