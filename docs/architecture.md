# Architecture

This page documents the internal architecture of `earth2observe` using [Mermaid](https://mermaid.js.org/) diagrams. It replaces the original draw.io class diagram.

## System Overview

The `Earth2Observe` facade exposes a uniform API on top of several concrete data-source backends. Each backend implements the `AbstractDataSource` interface, and each has a companion `Catalog` class that describes available variables.

```mermaid
flowchart LR
    user([User])
    e2o[Earth2Observe]
    user --> e2o
    e2o --> CHIRPS
    e2o --> S3
    e2o --> ECMWF
    e2o --> GEE
    CHIRPS --> FTP[(UCSB FTP<br/>data.chc.ucsb.edu)]
    S3 --> AWS[(AWS S3<br/>era5-pds bucket)]
    ECMWF --> CDS[(ECMWF<br/>Climate Data Store)]
    GEE --> Earth[(Google<br/>Earth Engine)]
```

## Class Diagram

The core abstraction is `AbstractDataSource`. Concrete classes `CHIRPS`, `S3`, `ECMWF`, and the `GEE` subpackage implement it. `AbstractCatalog` plays the same role for the variable/dataset metadata catalogs.

```mermaid
classDiagram
    class AbstractDataSource {
        <<abstract>>
        +space: Dict
        +time: Dict
        +client
        +root_dir: Path
        +temporal_resolution: str
        +variables: list
        +check_input_dates(start, end, res, fmt)*
        +initialize()*
        +create_grid(lat_lim, lon_lim)*
        +download()*
        +download_dataset()
        +api()*
    }

    class AbstractCatalog {
        <<abstract>>
        +catalog: Dict
        +get_catalog()
        +get_dataset(var_name)
    }

    class CHIRPS {
        +start_date
        +end_date
        +lat_limits
        +lon_limits
        +check_input_dates(...)
        +initialize()
        +create_grid(lat_lim, lon_lim)
        +download(progress_bar, cores)
        +API(date, args)
        +callAPI(pathFTP, path, filename)
        +post_download(...)
    }

    class S3 {
        +bucket: str
        +check_input_dates(...)
        +initialize(bucket)
        +create_grid(lat_lim, lon_lim)
        +download(progress_bar)
        +downloadDataset(var, progress_bar)
        +API(s3_file_path, local_dir, bucket)
        +parse_response_metadata(response)$
    }

    class ECMWF {
        +check_input_dates(...)
        +initialize()
        +create_grid(lat_lim, lon_lim)
        +download(...)
        +download_dataset(...)
        +api(var_info, dataset)
        +send_request(...)
        +post_download(...)
    }

    class Earth2Observe {
        +DataSources: Dict
        +datasource: AbstractDataSource
        +download(progress_bar, *args, **kwargs)
    }

    AbstractDataSource <|-- CHIRPS
    AbstractDataSource <|-- S3
    AbstractDataSource <|-- ECMWF
    Earth2Observe o--> AbstractDataSource : delegates to
    AbstractCatalog <|-- CHIRPS_Catalog
    AbstractCatalog <|-- S3_Catalog
    AbstractCatalog <|-- ECMWF_Catalog
    class CHIRPS_Catalog["Catalog (CHIRPS)"]
    class S3_Catalog["Catalog (S3)"] {
        +initialize(bucket)$
        +get_catalog()
        +get_variable(var_name)
        +get_available_years(bucket)
        +get_available_data(...)
    }
    class ECMWF_Catalog["Catalog (ECMWF)"] {
        +get_catalog()
        +get_dataset(var_name)
    }
```

## GEE Subpackage

The Google Earth Engine backend lives in its own subpackage and has a different shape: rather than implementing `AbstractDataSource`, it wraps the `earthengine-api` client directly through a small class hierarchy.

```mermaid
classDiagram
    class GEE {
        +service_account: str
        +service_key_path: str
        +initialize(service_account, service_key)$
        +encodeServiceAccount(key_dir)$
        +decodeServiceAccount(key_bytes)$
    }

    class Dataset {
        +getDate(...)
        +addBoundary(gdf)
        +filterByRegion(gdf)
    }

    GEE <|-- Dataset
```

## Download Sequence

The user calls `Earth2Observe.download()`, which delegates to the selected backend. Each backend follows the same high-level sequence: authenticate / open a session, iterate over dates × variables, fetch, and post-process.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant E2O as Earth2Observe
    participant DS as AbstractDataSource
    participant Server as Remote server<br/>(FTP / S3 / CDS)
    participant Pyramids as pyramids-gis

    User->>E2O: Earth2Observe(data_source, start, end, ...)
    E2O->>DS: instantiate backend
    DS->>DS: initialize() / check_input_dates() / create_grid()
    User->>E2O: download()
    E2O->>DS: download()
    loop for each date × variable
        DS->>Server: api() / callAPI()
        Server-->>DS: NetCDF / raw file
        DS->>Pyramids: post_download() → clip + convert
        Pyramids-->>DS: GeoTIFF
    end
    DS-->>User: files saved under path/
```

## Catalog Pattern

Every data source has a companion `Catalog` class that loads variable metadata from a YAML file (for CHIRPS and ECMWF) or introspects the remote bucket (for S3).

```mermaid
flowchart TB
    subgraph CHIRPS
        direction TB
        C1[Catalog]
        C2[(chirps entries<br/>in code)]
        C1 --> C2
    end
    subgraph ECMWF
        direction TB
        E1[Catalog]
        E2[(ecmwf_data_catalog.yaml<br/>cds_data_catalog.yaml)]
        E1 --> E2
    end
    subgraph S3
        direction TB
        S1[Catalog]
        S2[(era5-pds<br/>S3 bucket listing)]
        S1 --> S2
    end
    subgraph GEE
        direction TB
        G1[Catalog]
        G2[(gee/catalog.yaml)]
        G1 --> G2
    end
```
