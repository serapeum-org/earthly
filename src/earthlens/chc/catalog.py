"""Variable-catalog loader for the CHIRPS FTP data source.

Hosts :class:`Catalog`, the pydantic-backed reader for
`chc_data_catalog.yaml`. Mirrors the design of
:mod:`earthlens.ecmwf.catalog` but adapted for the CHIRPS FTP
directory structure.

The YAML's two consumed top-level sections each map to a typed
field on :class:`Catalog`:

* `available_datasets` (informational list of CHIRPS dataset keys)
  → :attr:`Catalog.available_datasets`
* `datasets` (structural map of FTP datasets, each carrying spatial
  / temporal metadata and a per-variable map) →
  :attr:`Catalog.datasets`, with each value a :class:`Dataset`

Variables are addressed by the `(dataset_key, variable_name)` pair
via :meth:`Catalog.get_variable`. Although CHIRPS currently only
exposes precipitation, the two-level addressing keeps the interface
symmetric with the ECMWF catalog and forwards-compatible with any
future CHIRPS-family products (CHIRP, CHIRTSdaily, etc.).

The path to the bundled YAML lives at :data:`CATALOG_PATH`; tests
can monkey-patch that module attribute to redirect the loader at a
temporary file.

Examples:
    - Construct the catalog and look up a variable:

        ```python
        >>> from earthlens.chc import Catalog
        >>> cat = Catalog()
        >>> var = cat.get_variable("global-daily", "precipitation")
        >>> var.units
        'mm/day'
        >>> var.is_flux
        True

        ```
    - Inspect a dataset's metadata:

        ```python
        >>> from earthlens.chc import Catalog
        >>> ds = Catalog().get_dataset("global-monthly")
        >>> ds.temporal_resolution
        'monthly'
        >>> ds.pandas_freq
        'MS'
        >>> ds.lat_boundaries
        [-50.0, 50.0]

        ```
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from earthlens.base import AbstractCatalog

CATALOG_PATH: Path = Path(__file__).parent / "chc_data_catalog.yaml"

_FTP_HOST: str = "data.chc.ucsb.edu"


class _StrictSafeLoader(yaml.SafeLoader):
    """:class:`yaml.SafeLoader` that rejects duplicate keys in any mapping.

    Prevents silent shadowing when the same dataset key or variable
    name is accidentally duplicated in `chc_data_catalog.yaml`.
    """


def _construct_mapping_no_duplicates(
    loader: _StrictSafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """Build a dict from a YAML mapping node, rejecting duplicate keys."""
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            mark = key_node.start_mark
            raise ValueError(
                f"duplicate YAML key {key!r} at line {mark.line + 1}, "
                f"column {mark.column + 1} of {mark.name}: every key in "
                "a YAML mapping must be unique"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


class Variable(BaseModel):
    """Per-variable catalog entry for CHIRPS datasets.

    A frozen pydantic model carrying the metadata for one variable
    row in `chc_data_catalog.yaml`. CHIRPS currently only provides
    precipitation, but the typed model keeps the interface symmetric
    with the ECMWF catalog.

    Attributes:
        dataset_key: CHIRPS dataset identifier (e.g. `"global-daily"`).
        name: Variable short code (e.g. `"precipitation"`).
        description: Human-readable description of the variable.
        units: Unit string (e.g. `"mm/day"`, `"mm/month"`).
        types: `"flux"` for accumulated quantities like precipitation.
            `None` for instantaneous / state variables (not currently
            used by CHIRPS but kept for interface symmetry).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset_key: str
    name: str
    description: str
    units: str
    types: str | None = None

    @property
    def is_flux(self) -> bool:
        """Whether this variable is a flux (accumulated quantity).

        Returns:
            bool: `True` when `types == "flux"`. Precipitation is
            always a flux in CHIRPS.
        """
        return self.types == "flux"


class Dataset(BaseModel):
    """One CHIRPS dataset's section in the catalog.

    Mirrors the shape of a single `datasets.<key>:` block in
    `chc_data_catalog.yaml` and carries all metadata needed to
    construct FTP paths, generate date ranges, and validate user
    inputs.

    Attributes:
        ftp_bases: FTP directory paths per format code.  Keys are format
            strings (e.g. `"tif"`, `"netcdf"`, `"bil"`); values are the
            FTP directory path relative to the server root, possibly with
            `{year}` as a placeholder.  At least one entry is always
            present; additional entries are added as their server paths
            are verified. Use :attr:`ftp_base` for the default path.
        file_patterns: Remote filename templates per format code, keyed
            the same way as `ftp_bases`.  Each value is a Python
            format-string with placeholders such as `{year}`, `{month}`,
            `{day}`, `{dekad}`, `{pentad}`, `{res}`.  Use
            :attr:`file_pattern` for the default pattern.
        region: Geographic coverage label (e.g. `"global"`,
            `"africa"`, `"central-america-caribbean"`).
        temporal_resolution: Human-readable temporal resolution
            label (e.g. `"daily"`, `"monthly"`, `"dekadal"`).
        pandas_freq: Pandas offset alias for date-range generation
            (e.g. `"D"`, `"MS"`, `"10D"`).
        spatial_resolution: Pixel size(s) in degrees. A list because
            some datasets offer both 0.05° and 0.25° variants.
        formats: Available file formats on the FTP (e.g.
            `["tif", "cog", "netcdf"]`).
        lat_boundaries: `[south, north]` latitude limits.
        lon_boundaries: `[west, east]` longitude limits.
        start_date: Earliest available date (ISO 8601).
        end_date: Latest available date (ISO 8601) for archived datasets
            that are no longer updated. `None` for ongoing / operational
            products.
        preliminary: `True` for near-real-time preliminary datasets
            that may be revised later.
        variables: Per-variable map keyed by the short code
            (e.g. `"precipitation"`).

    Examples:
        - Inspect a dataset:

            ```python
            >>> from earthlens.chc import Catalog
            >>> ds = Catalog().get_dataset("global-daily")
            >>> ds.region
            'global'
            >>> ds.spatial_resolution
            [0.05, 0.25]
            >>> "precipitation" in ds.variables
            True

            ```
        - Access format-specific FTP paths:

            ```python
            >>> from earthlens.chc import Catalog
            >>> ds = Catalog().get_dataset("global-daily")
            >>> ds.ftp_base   # default (first) path
            'pub/org/chc/products/CHIRPS-2.0/global_daily/tifs/p05/'
            >>> "tif" in ds.ftp_bases
            True

            ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ftp_bases: dict[str, str]
    file_patterns: dict[str, str]
    region: str
    temporal_resolution: str
    pandas_freq: str
    spatial_resolution: list[float]
    formats: list[str]
    lat_boundaries: list[float]
    lon_boundaries: list[float]
    start_date: str
    end_date: str | None = None
    preliminary: bool = False
    variables: dict[str, Variable] = Field(default_factory=dict)

    @property
    def default_format(self) -> str:
        """First (default) format key in `ftp_bases`.

        Returns:
            str: The format code used by the primary FTP path, e.g.
            `"tif"`.
        """
        return next(iter(self.ftp_bases))

    @property
    def ftp_base(self) -> str:
        """FTP directory path for the default format.

        Convenience accessor equivalent to
        `ftp_bases[default_format]`.  Exists so existing code that
        reads `dataset.ftp_base` continues to work without changes.

        Returns:
            str: FTP directory path for the primary format.
        """
        return self.ftp_bases[self.default_format]

    @property
    def file_pattern(self) -> str:
        """Remote filename template for the default format.

        Convenience accessor equivalent to
        `file_patterns[default_format]`.

        Returns:
            str: Filename template for the primary format.
        """
        return self.file_patterns[self.default_format]


class Catalog(AbstractCatalog):
    """Variable catalog for the CHIRPS FTP data source.

    Reads `chc_data_catalog.yaml` (shipped as package data) and
    exposes its consumed top-level sections as typed pydantic fields.
    Instantiate with no arguments (`Catalog()`) — :func:`model_post_init`
    parses the YAML and populates every field in one pass.

    Variables are addressed by the `(dataset_key, variable_name)`
    pair via :meth:`get_variable`. The dataset key is part of the
    identity because the same variable name (`"precipitation"`)
    appears under every dataset with different metadata (units,
    FTP path, etc.).

    Attributes:
        available_datasets: Informational list of every CHIRPS dataset
            key. Mirrors the `available_datasets:` block in the YAML.
        available_regions: Structural map of geographic-coverage profiles
            keyed by region name (e.g. `"global"`, `"africa"`). Each
            value carries `lat_boundaries` and `lon_boundaries` lists.
            Populated from the `regions:` block (catalog v2+); empty dict
            for v1 files.
        datasets: Structural map keyed by dataset identifier. Each
            value is a :class:`Dataset` carrying spatial / temporal
            metadata and its per-variable map.

    Examples:
        - Look up a variable by `(dataset_key, variable_name)`:

            ```python
            >>> from earthlens.chc import Catalog
            >>> spec = Catalog().get_variable(
            ...     "global-daily", "precipitation"
            ... )
            >>> spec.units
            'mm/day'
            >>> spec.is_flux
            True

            ```
        - List all curated dataset keys:

            ```python
            >>> from earthlens.chc import Catalog
            >>> cat = Catalog()
            >>> "global-daily" in cat.datasets
            True
            >>> "africa-monthly" in cat.datasets
            True

            ```
        - Inspect available dataset count:

            ```python
            >>> from earthlens.chc import Catalog
            >>> len(Catalog().available_datasets) >= 90
            True

            ```
    """

    available_datasets: list[str] = Field(default_factory=list)
    available_regions: dict[str, dict[str, list[float]]] = Field(default_factory=dict)
    datasets: dict[str, Dataset] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Parse `chc_data_catalog.yaml` into the exposed fields.

        Overrides :func:`AbstractCatalog.model_post_init` to populate
        :attr:`available_datasets`, :attr:`available_regions`, and
        :attr:`datasets` directly, bypassing the flat `get_catalog` path
        the base class assumes.

        Since catalog v2 the YAML omits `lat_boundaries` / `lon_boundaries`
        from individual dataset entries; the loader expands them here from
        the top-level `regions:` block keyed by the dataset's `region:`
        field.  Per-dataset explicit overrides (if present) still win so
        the format is backward-compatible with v1 files.

        Raises:
            ValueError: If the YAML is missing, has an empty `datasets:`
                block, if no variables appear under any dataset, or if a
                dataset's `region:` key is not found in the `regions:` block
                and the dataset omits explicit `lat_boundaries` /
                `lon_boundaries`.
        """
        catalog_path = CATALOG_PATH
        with open(catalog_path, encoding="utf-8") as stream:
            data = yaml.load(stream, Loader=_StrictSafeLoader) or {}  # nosec B506

        datasets_yaml = data.get("datasets")
        if not datasets_yaml:
            raise ValueError(
                f"{catalog_path} is missing or has an empty "
                "'datasets' key. The catalog must contain at least "
                "one dataset with one variable."
            )

        # Regions block (optional for v1 back-compat; required for v2).
        regions_map: dict[str, dict[str, list[float]]] = data.get("regions") or {}

        structural: dict[str, Dataset] = {}
        total_vars = 0

        for ds_key, ds_body in datasets_yaml.items():
            ds_vars: dict[str, Variable] = {}
            for var_code, var_entry in (ds_body.get("variables") or {}).items():
                try:
                    ds_vars[var_code] = Variable(
                        dataset_key=ds_key,
                        name=var_code,
                        description=var_entry.get("description", ""),
                        units=var_entry.get("units", ""),
                        types=var_entry.get("types"),
                    )
                except ValidationError as exc:
                    raise ValueError(
                        f"chc_data_catalog.yaml variable {var_code!r} "
                        f"under dataset {ds_key!r} failed validation:\n{exc}"
                    ) from exc
                total_vars += 1

            # Expand lat/lon from the regions block when not given inline.
            region_key = ds_body.get("region", "")
            region_def = regions_map.get(region_key, {})
            lat_boundaries = ds_body.get("lat_boundaries") or region_def.get(
                "lat_boundaries"
            )
            lon_boundaries = ds_body.get("lon_boundaries") or region_def.get(
                "lon_boundaries"
            )
            if lat_boundaries is None or lon_boundaries is None:
                raise ValueError(
                    f"chc_data_catalog.yaml dataset {ds_key!r} has no "
                    "`lat_boundaries` / `lon_boundaries` and its region "
                    f"{region_key!r} is not defined in the top-level "
                    "`regions:` block."
                )

            try:
                structural[ds_key] = Dataset(
                    ftp_bases=ds_body["ftp_bases"],
                    file_patterns=ds_body["file_patterns"],
                    region=region_key,
                    temporal_resolution=ds_body["temporal_resolution"],
                    pandas_freq=ds_body["pandas_freq"],
                    spatial_resolution=ds_body["spatial_resolution"],
                    formats=ds_body["formats"],
                    lat_boundaries=lat_boundaries,
                    lon_boundaries=lon_boundaries,
                    start_date=ds_body["start_date"],
                    end_date=ds_body.get("end_date"),
                    preliminary=ds_body.get("preliminary", False),
                    variables=ds_vars,
                )
            except (ValidationError, KeyError) as exc:
                raise ValueError(
                    f"chc_data_catalog.yaml dataset {ds_key!r} "
                    f"failed validation:\n{exc}"
                ) from exc

        if total_vars == 0:
            raise ValueError(
                f"{catalog_path} has no variables under any dataset. "
                "The catalog must contain at least one variable."
            )

        self.available_datasets = list(data.get("available_datasets") or [])
        self.available_regions = regions_map
        self.datasets = structural

    def get_catalog(self) -> dict[str, Dataset]:
        """Return the structural per-dataset map.

        Satisfies the abstract base's contract; the actual parsing
        is done in :func:`model_post_init`.

        Returns:
            dict[str, Dataset]: One entry per CHIRPS dataset. Same
            object as :attr:`datasets`.

        Examples:
            - Inspect the dataset map:

                ```python
                >>> from earthlens.chc import Catalog
                >>> mapping = Catalog().get_catalog()
                >>> "global-daily" in mapping
                True

                ```
        """
        return self.datasets

    def get_variable(self, dataset_key: str, variable_name: str = "precipitation") -> Variable:
        """Return the :class:`Variable` for a `(dataset, variable)` pair.

        Args:
            dataset_key: CHIRPS dataset identifier as it appears as a
                key in :attr:`datasets` (e.g. `"global-daily"`).
            variable_name: Short variable code. Defaults to
                `"precipitation"` since CHIRPS only provides one
                variable.

        Returns:
            Variable: Per-variable metadata loaded from
            `chc_data_catalog.yaml`.

        Raises:
            KeyError: If `dataset_key` is not curated, or if
                `variable_name` is not declared under that dataset.

        Examples:
            - Look up precipitation for global daily data:

                ```python
                >>> from earthlens.chc import Catalog
                >>> spec = Catalog().get_variable("global-daily")
                >>> spec.units
                'mm/day'
                >>> spec.is_flux
                True

                ```
            - Explicit variable name (the only one available):

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().get_variable(
                ...     "africa-monthly", "precipitation"
                ... ).units
                'mm/month'

                ```
        """
        return self.datasets[dataset_key].variables[variable_name]

    def get_dataset(self, name: str) -> Dataset:
        """Return the :class:`Dataset` record for a CHIRPS dataset key.

        Args:
            name: CHIRPS dataset identifier (e.g. `"global-daily"`,
                `"africa-monthly"`).

        Returns:
            Dataset: Structural record carrying the dataset's
            spatial / temporal metadata and per-variable map.

        Raises:
            KeyError: If `name` is not a curated dataset.

        Examples:
            - Read a dataset's temporal resolution and FTP base:

                ```python
                >>> from earthlens.chc import Catalog
                >>> ds = Catalog().get_dataset("global-daily")
                >>> ds.temporal_resolution
                'daily'
                >>> ds.pandas_freq
                'D'

                ```
        """
        return self.datasets[name]

    def describe_region(self, region: str) -> dict[str, list[float]]:
        """Return the spatial bounds for a region name.

        Args:
            region: Region key as it appears in the `regions:` block of
                `chc_data_catalog.yaml` (e.g. `"global"`, `"africa"`,
                `"global-land"`).

        Returns:
            dict with keys `lat_boundaries` (`[south, north]`) and
            `lon_boundaries` (`[west, east]`).

        Raises:
            KeyError: If `region` is not defined in `available_regions`.

        Examples:
            - Read the standard global extent:

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().describe_region("global")
                {'lat_boundaries': [-50, 50], 'lon_boundaries': [-180, 180]}

                ```
            - CHIRTSdaily uses a wider land-surface extent:

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().describe_region("global-land")
                {'lat_boundaries': [-60, 70], 'lon_boundaries': [-180, 180]}

                ```
        """
        return self.available_regions[region]

    def describe(self, dataset_key: str) -> dict[str, Any]:
        """Return a structured introspection record for a CHIRPS dataset.

        Useful for "what metadata does dataset X expose?" questions at
        runtime — the caller can dump the result without needing to
        walk the YAML themselves.

        Args:
            dataset_key: CHIRPS dataset identifier as it appears as a
                key in :attr:`datasets`.

        Returns:
            dict with keys `dataset`, `region`,
            `temporal_resolution`, `pandas_freq`,
            `spatial_resolution`, `formats`, `lat_boundaries`,
            `lon_boundaries`, `start_date`, `file_pattern`,
            `preliminary`, and `variables`.

        Raises:
            KeyError: If `dataset_key` is not a curated dataset.

        Examples:
            - Describe global daily data at a glance:

                ```python
                >>> from earthlens.chc import Catalog
                >>> info = Catalog().describe("global-daily")
                >>> info["region"]
                'global'
                >>> info["temporal_resolution"]
                'daily'
                >>> "precipitation" in info["variables"]
                True

                ```
        """
        ds = self.get_dataset(dataset_key)
        return {
            "dataset": dataset_key,
            "region": ds.region,
            "temporal_resolution": ds.temporal_resolution,
            "pandas_freq": ds.pandas_freq,
            "spatial_resolution": ds.spatial_resolution,
            "formats": ds.formats,
            "ftp_bases": dict(ds.ftp_bases),
            "file_patterns": dict(ds.file_patterns),
            "lat_boundaries": ds.lat_boundaries,
            "lon_boundaries": ds.lon_boundaries,
            "start_date": ds.start_date,
            "end_date": ds.end_date,
            "preliminary": ds.preliminary,
            "variables": sorted(ds.variables),
        }

    def list_datasets(
        self,
        region: str | None = None,
        temporal_resolution: str | None = None,
    ) -> list[str]:
        """Return dataset keys, optionally filtered by region or resolution.

        Args:
            region: Filter by geographic coverage (e.g. `"global"`,
                `"africa"`). `None` returns all datasets.
            temporal_resolution: Filter by temporal resolution (e.g.
                `"daily"`, `"monthly"`). `None` returns all datasets.

        Returns:
            list[str]: Sorted list of matching dataset keys.

        Examples:
            - List all Africa datasets:

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().list_datasets(region="africa")
                ['africa-2-monthly', 'africa-3-monthly', 'africa-6-hourly', 'africa-daily', 'africa-dekad', 'africa-monthly', 'africa-pentad']

                ```
            - List all daily datasets regardless of region:

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().list_datasets(temporal_resolution="daily")
                ['africa-daily', 'chirp-daily', 'chirp-v3-global-daily', 'chirps-gefs-v12-daily-16day', 'chirps-gefs-v3-daily', 'chirps-v3-global-daily-prelim', 'chirps-v3-global-daily-rnl', 'chirps-v3-global-daily-sat', 'chirtsdaily-heat-index', 'chirtsdaily-relative-humidity', 'chirtsdaily-svp', 'chirtsdaily-tmax', 'chirtsdaily-tmin', 'chirtsdaily-vpd', 'global-daily', 'prelim-global-daily', 'western-hemisphere-daily']

                ```
        """
        result: list[str] = []
        for key, ds in self.datasets.items():
            if region is not None and ds.region != region:
                continue
            if temporal_resolution is not None and ds.temporal_resolution != temporal_resolution:
                continue
            result.append(key)
        return sorted(result)

