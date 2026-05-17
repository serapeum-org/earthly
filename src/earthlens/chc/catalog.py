"""Variable-catalog loader for the CHC (Climate Hazards Center) FTP data source.

Hosts :class:`Catalog`, the pydantic-backed reader for the per-family
YAML files under `<package>/catalog/`. Mirrors the GEE backend's
split-catalog layout (one file per product family + `_index.yaml`)
and adapts it for the CHC FTP directory structure.

Catalog layout (under `src/earthlens/chc/catalog/`):

* `_index.yaml` — `available_datasets:` (informational walk-order
  list of every CHC dataset key) and `regions:` (named
  geographic-coverage profiles keyed by region name). Read into
  :attr:`Catalog.available_datasets` and
  :attr:`Catalog.available_regions`.
* `<family>.yaml` — one file per product family
  (`chirps-2.0.yaml`, `chirps-v3.yaml`, `chirp.yaml`, `chirts.yaml`,
  `gefs.yaml`, `climatology.yaml`, `wbgt.yaml`, `indices.yaml`,
  `cmip6.yaml`, `centennial-trends.yaml`). Each carries a
  `datasets:` block whose entries are merged into
  :attr:`Catalog.datasets`. Dataset keys must be unique across files.

Variables are addressed by the `(dataset_key, variable_name)` pair
via :meth:`Catalog.get_variable`.

The bundled-catalog directory lives at :data:`CATALOG_PATH`; tests
can monkey-patch that module attribute to redirect the loader at a
temporary directory (or, via the back-compat single-file branch, a
flat legacy YAML).

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
from typing import Any, Literal


#: Canonical `temporal_resolution` vocabulary for CHC datasets (M1).
#:
#: Every value here is currently used by at least one bundled dataset.
#: New cadences must be added here AND to `Dataset.temporal_resolution`
#: (the `Literal[...]` annotation in step with this tuple). Pydantic
#: rejects any string outside this list at load time, so a typo
#: (e.g. `"daly"` instead of `"daily"`) raises ValidationError instead
#: of silently loading.
_TEMPORAL_RESOLUTIONS: tuple[str, ...] = (
    "10-day",
    "15-day",
    "2-monthly",
    "3-monthly",
    "5-day",
    "6-hourly",
    "annual",
    "daily",
    "daily-delta",
    "dekadal",
    "monthly",
    "monthly-climatology",
    "pentadal",
    "seasonal",
)

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from earthlens.base import AbstractCatalog, FluxableLeaf, Provider
from earthlens.base.providers import (
    clear_providers_cache as _clear_providers_cache_base,
    load_providers,
)

CATALOG_PATH: Path = Path(__file__).parent / "catalog"
PROVIDERS_PATH: Path = Path(__file__).parent / "providers.yaml"

# Module-level cache of parsed catalog data. The cache key is
# `(resolved_path, fingerprint)`, where the fingerprint is:
#
#   * For a single-file catalog (legacy back-compat): the file's
#     `stat().st_mtime_ns`.
#   * For the directory layout: a tuple of `(filename, mtime_ns)`
#     pairs sorted by filename. This is collision-free under
#     permutations of mtimes -- if file A's mtime increases by N
#     and file B's decreases by N (rare but legitimate, e.g.
#     after a manual `touch -r ...`), the tuple still differs
#     because both names are pinned. The previous sum-of-mtimes
#     fingerprint (pre-H4) collapsed those cases to a collision.
#
# Any real file mutation invalidates the entry naturally. Mirrors
# the GEE / ECMWF pattern so repeated `Catalog()` construction is
# ~1 ms instead of paying YAML parse + pydantic validation each
# time.
_CacheKey = tuple[str, int | tuple[tuple[str, int], ...]]
_CATALOG_CACHE: dict[
    _CacheKey,
    tuple[list[str], dict[str, dict[str, list[float]]], dict[str, "Dataset"]],
] = {}


def clear_catalog_cache() -> None:
    """Empty the module-level catalog + providers parse caches (test helper)."""
    _CATALOG_CACHE.clear()
    _clear_providers_cache_base()


class _StrictSafeLoader(yaml.SafeLoader):
    """:class:`yaml.SafeLoader` that rejects duplicate keys in any mapping.

    Prevents silent shadowing when the same dataset key or variable
    name is accidentally duplicated inside a single CHC catalog YAML.
    Cross-file duplicates are caught separately by the directory
    loader (which keeps a `seen_in: {ds_key: filename}` map and raises
    `ValueError` on the second occurrence).
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


class Variable(FluxableLeaf):
    """Per-variable catalog entry for CHIRPS datasets.

    A frozen pydantic model carrying the metadata for one variable
    row in a CHC catalog YAML. CHIRPS-the-product only provides
    precipitation, but the typed model keeps the interface symmetric
    with the ECMWF catalog. Inherits `types` + `is_flux` from
    :class:`earthlens.base.FluxableLeaf`.

    Attributes:
        dataset_key: CHIRPS dataset identifier (e.g. `"global-daily"`).
        name: Variable short code (e.g. `"precipitation"`).
        description: Human-readable description of the variable.
        units: Unit string (e.g. `"mm/day"`, `"mm/month"`).
    """

    # `model_config` (frozen=True, extra="forbid") + `types` field +
    # `is_flux` property are inherited from `FluxableLeaf`.

    dataset_key: str
    name: str
    description: str
    units: str

    # `is_flux` property inherited from `FluxableLeaf` (N1 in
    # planning/catalog-cross-backend-comparison.md).


class Dataset(BaseModel):
    """One CHIRPS dataset's section in the catalog.

    Mirrors the shape of a single `datasets.<key>:` block in a CHC
    catalog YAML and carries all metadata needed to
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
            :attr:`file_pattern` for the default pattern. `None` for
            datasets that publish a fixed enumerated set of files
            (`discrete_files`) instead of per-date partitions.
        discrete_files: Format-keyed map of fixed filenames for datasets
            that publish a small set of multi-year archive files rather
            than per-date partitions (CenTrends and similar). When set,
            the backend iterates `discrete_files[fmt]` once instead of
            doing date substitution on `file_patterns[fmt]`. Exactly one
            of `file_patterns` / `discrete_files` must be set.
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
    file_patterns: dict[str, str] | None = None
    discrete_files: dict[str, list[str]] | None = None
    region: str
    temporal_resolution: Literal[
        "10-day",
        "15-day",
        "2-monthly",
        "3-monthly",
        "5-day",
        "6-hourly",
        "annual",
        "daily",
        "daily-delta",
        "dekadal",
        "monthly",
        "monthly-climatology",
        "pentadal",
        "seasonal",
    ]
    pandas_freq: str
    spatial_resolution: list[float]
    formats: list[str]
    lat_boundaries: list[float]
    lon_boundaries: list[float]
    start_date: str
    end_date: str | None = None
    preliminary: bool = False
    provider: str | None = None
    variables: dict[str, Variable] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_pattern_or_discrete(self) -> Dataset:
        """Exactly one of `file_patterns` / `discrete_files` must be set."""
        has_patterns = bool(self.file_patterns)
        has_discrete = bool(self.discrete_files)
        if not has_patterns and not has_discrete:
            raise ValueError(
                "Dataset must declare either `file_patterns` "
                "(per-date templates) or `discrete_files` (fixed "
                "enumerated multi-year archive files)."
            )
        if has_patterns and has_discrete:
            raise ValueError(
                "Dataset declares both `file_patterns` and "
                "`discrete_files`; pick one. The backend chooses its "
                "download path based on which one is set."
            )
        return self

    @property
    def default_format(self) -> str:
        """First (default) format key in `ftp_bases`.

        Returns:
            str: The format code used by the primary FTP path, e.g.
            `"tif"`.
        """
        return next(iter(self.ftp_bases))

    @property
    def primary_spatial_resolution(self) -> float:
        """The first (primary) pixel size from :attr:`spatial_resolution`.

        Convenience accessor for the common single-resolution case.
        After C3 every shipped dataset has a 1-element list, so this
        is equivalent to `ds.spatial_resolution[0]` without the
        consumer having to think about list shape (L4).

        Returns:
            float: Pixel size in degrees.

        Raises:
            IndexError: If `spatial_resolution` is somehow empty
                (defended against by the
                `test_every_spatial_resolution_is_non_empty_positive_floats`
                test that ships with the catalog).
        """
        return self.spatial_resolution[0]

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

        Raises:
            ValueError: If this dataset uses `discrete_files` rather
                than per-date `file_patterns`.
        """
        if self.file_patterns is None:
            raise ValueError(
                f"Dataset uses `discrete_files`; iterate "
                f"`dataset.discrete_files[fmt]` instead."
            )
        return self.file_patterns[self.default_format]

    @property
    def is_discrete(self) -> bool:
        """True for datasets that publish enumerated multi-year files."""
        return self.discrete_files is not None


def _build_chc_dataset(
    ds_key: str,
    ds_body: dict[str, Any],
    regions_map: dict[str, dict[str, list[float]]],
    source_path: Path,
) -> tuple["Dataset", int]:
    """Build one :class:`Dataset` from its YAML body + variables (N1).

    Validates every variable into a :class:`Variable`, resolves the
    dataset's `region:` key against `regions_map` (with per-dataset
    inline `lat_boundaries` / `lon_boundaries` overrides winning), and
    constructs the :class:`Dataset` record.

    Args:
        ds_key: The dataset name (used in error messages).
        ds_body: Raw mapping for one dataset out of `datasets:`.
        regions_map: Top-level `regions:` block.
        source_path: Path of the YAML file that produced `ds_body` —
            the per-family file under the split layout, or the
            single-file legacy catalog. Used only to surface the file
            name in error messages so a maintainer can locate the
            offending row.

    Returns:
        (`Dataset`, `n_vars`) — the constructed record and the count
        of variables under it.

    Raises:
        ValueError: If a variable or the dataset itself fails pydantic
            validation, or if the region key can't be resolved and the
            dataset doesn't override `lat_boundaries` / `lon_boundaries`.
    """
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
                f"{source_path.name} variable {var_code!r} "
                f"under dataset {ds_key!r} failed validation:\n{exc}"
            ) from exc

    # M2: validate pandas_freq against the live pandas registry. Catches
    # both typos (e.g. `"daly"`) and deprecated aliases (e.g. `"AS"`,
    # removed in pandas 3.x; "H" deprecated for "h" in pandas 2.2).
    # Discrete-files datasets keep a placeholder pandas_freq; the check
    # still runs so even the placeholder must be a legal alias.
    freq_value = ds_body.get("pandas_freq")
    try:
        pd.tseries.frequencies.to_offset(freq_value)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"{source_path.name} dataset {ds_key!r} has invalid "
            f"`pandas_freq` {freq_value!r}: {exc}. See "
            "https://pandas.pydata.org/docs/user_guide/timeseries.html"
            "#offset-aliases for the current alias table."
        ) from exc

    # Per H1: `regions:` is the single source of truth for spatial bounds.
    # The loader no longer accepts inline `lat_boundaries`/`lon_boundaries`
    # on a dataset — reject them with a clear pointer at the regions block
    # so the maintainer either deletes the inline copy or, for a genuinely
    # custom extent, defines a new named region.
    inline_lat = ds_body.get("lat_boundaries")
    inline_lon = ds_body.get("lon_boundaries")
    if inline_lat is not None or inline_lon is not None:
        raise ValueError(
            f"{source_path.name} dataset {ds_key!r} carries inline "
            "`lat_boundaries` / `lon_boundaries`, which is no longer "
            "accepted (H1). Spatial bounds come from the `regions:` "
            "block. To use a non-standard extent, add a new entry to "
            "`_index.yaml`'s `regions:` block and point `region:` at "
            "it (see `east-africa-centennial` for the CenTrends "
            "precedent)."
        )
    region_key = ds_body.get("region", "")
    region_def = regions_map.get(region_key)
    if region_def is None:
        raise ValueError(
            f"{source_path.name} dataset {ds_key!r} has region "
            f"{region_key!r} which is not defined in `_index.yaml`'s "
            "`regions:` block. Add it there or pick an existing "
            f"region from {sorted(regions_map)}."
        )
    lat_boundaries = region_def.get("lat_boundaries")
    lon_boundaries = region_def.get("lon_boundaries")
    if lat_boundaries is None or lon_boundaries is None:
        raise ValueError(
            f"{source_path.name} dataset {ds_key!r} resolved to "
            f"region {region_key!r} but that region is missing "
            "`lat_boundaries` or `lon_boundaries`."
        )

    try:
        ds = Dataset(
            ftp_bases=ds_body["ftp_bases"],
            file_patterns=ds_body.get("file_patterns") or None,
            discrete_files=ds_body.get("discrete_files") or None,
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
            # Read the publisher slug from the YAML when set; default to
            # `"ucsb-chc"` because UCSB's Climate Hazards Center is the
            # de-facto publisher of every dataset shipped today. Future
            # YAML entries can override per-dataset (e.g. a CHIRPS-GEFS
            # row that wants to attribute NCEP, or an SPEI row that
            # wants to attribute MERRA-2 for the PET term); the
            # `providers.yaml` registry must carry the slug for the
            # load-time validator to accept it.
            provider=ds_body.get("provider", "ucsb-chc"),
            variables=ds_vars,
        )
    except (ValidationError, KeyError) as exc:
        raise ValueError(
            f"{source_path.name} dataset {ds_key!r} "
            f"failed validation:\n{exc}"
        ) from exc
    return ds, len(ds_vars)


def _load_catalog_data(
    path: Path,
) -> tuple[list[str], dict[str, dict[str, list[float]]], dict[str, "Dataset"]]:
    """Parse, validate, and cache the CHC catalog at `path` (M2).

    Dispatches on file vs. directory:

    * **Directory** — the GEE-style split layout. Reads `_index.yaml`
      for the informational `available_datasets:` list and the
      `regions:` map, then merges every other `*.yaml` sibling's
      `datasets:` block into one dict. Duplicate dataset keys across
      files are rejected.
    * **File** — the legacy single-file layout (`chc_data_catalog.yaml`).
      Kept for backwards compatibility and tests that pass a single
      flat YAML in via `Catalog.load(catalog_path=...)`.

    Returns a `(available_datasets, regions_map, datasets)` triple of
    the same shape :class:`Catalog` exposes. Results are cached on
    `(resolved-path, mtime-fingerprint)`; for a directory the
    fingerprint is the sum of all member-file `mtime_ns` values, so
    editing any per-family YAML invalidates the cache naturally.

    Raises:
        ValueError: If the YAML is missing, has no `datasets:` block,
            has no variables under any dataset, has duplicate dataset
            keys across files, or any region key / field validation
            fails.
    """
    resolved = str(path.resolve())
    fingerprint: int | tuple[tuple[str, int], ...]
    try:
        if path.is_dir():
            # Tuple of (name, mtime) pairs sorted by name. Collision-
            # free under mtime permutations (see _CATALOG_CACHE comment).
            fingerprint = tuple(
                (child.name, child.stat().st_mtime_ns)
                for child in sorted(path.glob("*.yaml"))
            )
        else:
            fingerprint = path.stat().st_mtime_ns
    except FileNotFoundError:
        fingerprint = 0
    key: _CacheKey = (resolved, fingerprint)
    cached = _CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    if path.is_dir():
        result = _load_catalog_directory(path)
    else:
        result = _load_catalog_file(path)
    _CATALOG_CACHE[key] = result
    return result


def _load_catalog_file(
    path: Path,
) -> tuple[list[str], dict[str, dict[str, list[float]]], dict[str, "Dataset"]]:
    """Read a legacy single-file CHC catalog into the standard triple."""
    with open(path, encoding="utf-8") as stream:
        data = yaml.load(stream, Loader=_StrictSafeLoader) or {}  # nosec B506

    datasets_yaml = data.get("datasets")
    if not datasets_yaml:
        raise ValueError(
            f"{path} is missing or has an empty "
            "'datasets' key. The catalog must contain at least "
            "one dataset with one variable."
        )

    regions_map: dict[str, dict[str, list[float]]] = data.get("regions") or {}

    structural: dict[str, Dataset] = {}
    total_vars = 0
    for ds_key, ds_body in datasets_yaml.items():
        ds, n_vars = _build_chc_dataset(ds_key, ds_body, regions_map, path)
        structural[ds_key] = ds
        total_vars += n_vars

    if total_vars == 0:
        raise ValueError(
            f"{path} has no variables under any dataset. "
            "The catalog must contain at least one variable."
        )

    available = list(data.get("available_datasets") or [])
    return available, regions_map, structural


def _load_catalog_directory(
    directory: Path,
) -> tuple[list[str], dict[str, dict[str, list[float]]], dict[str, "Dataset"]]:
    """Read a GEE-style split catalog (one file per product family).

    Layout expected under `directory`:

    * `_index.yaml` — `available_datasets:` (informational walk-order
      list) + `regions:` map (named geographic-coverage profiles).
    * `<family>.yaml` — one or more per-family files, each with a
      `datasets:` block. Family file names are not load-bearing;
      anything matching `*.yaml` except `_index.yaml` is merged.

    Dataset keys must be unique across all files; duplicates raise
    `ValueError` with both filenames.
    """
    index_path = directory / "_index.yaml"
    if not index_path.is_file():
        raise ValueError(
            f"{directory} has no `_index.yaml`. The CHC catalog "
            "directory must contain `_index.yaml` (with "
            "`available_datasets:` + `regions:`) alongside one or "
            "more per-family `<family>.yaml` files."
        )

    with index_path.open(encoding="utf-8") as stream:
        index_data = yaml.load(stream, Loader=_StrictSafeLoader) or {}  # nosec B506
    available = list(index_data.get("available_datasets") or [])
    regions_map: dict[str, dict[str, list[float]]] = (
        index_data.get("regions") or {}
    )

    structural: dict[str, Dataset] = {}
    seen_in: dict[str, str] = {}
    total_vars = 0
    for yaml_path in sorted(directory.glob("*.yaml")):
        if yaml_path.name == "_index.yaml":
            continue
        with yaml_path.open(encoding="utf-8") as stream:
            file_data = yaml.load(stream, Loader=_StrictSafeLoader) or {}  # nosec B506
        for ds_key, ds_body in (file_data.get("datasets") or {}).items():
            if ds_key in structural:
                raise ValueError(
                    f"duplicate dataset key {ds_key!r}: declared in "
                    f"both `{seen_in[ds_key]}` and `{yaml_path.name}`. "
                    "Each CHC dataset key must live in exactly one "
                    "per-family file."
                )
            ds, n_vars = _build_chc_dataset(
                ds_key, ds_body, regions_map, yaml_path
            )
            structural[ds_key] = ds
            seen_in[ds_key] = yaml_path.name
            total_vars += n_vars

    if total_vars == 0:
        raise ValueError(
            f"{directory} has no variables under any dataset. "
            "The CHC catalog must contain at least one variable."
        )

    return available, regions_map, structural


class Catalog(AbstractCatalog):
    """Variable catalog for the CHIRPS FTP data source.

    Reads the per-family `catalog/*.yaml` files (shipped as package data) and
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

    _catalog_kind: str = "CHC catalog"

    available_datasets: list[str] = Field(default_factory=list)
    available_regions: dict[str, dict[str, list[float]]] = Field(default_factory=dict)
    datasets: dict[str, Dataset] = Field(default_factory=dict)
    providers: dict[str, Provider] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Auto-load the bundled CHC catalog when the user didn't supply one.

        `Catalog()` with no args is sugar for `Catalog.load()` — it
        reads the bundled YAML through the `(path, mtime_ns)`-keyed
        cache so repeated construction is ~1 ms. If the caller passed
        `datasets=...`, the disk read is skipped (test path).

        Raises:
            ValueError: When auto-loading, propagates the same errors
                as :meth:`load`.
        """
        if self.datasets:
            return
        loaded = Catalog.load()
        self.available_datasets = loaded.available_datasets
        self.available_regions = loaded.available_regions
        self.datasets = loaded.datasets
        self.providers = loaded.providers

    @classmethod
    def load(
        cls,
        catalog_path: Path | None = None,
        providers_path: Path | None = None,
    ) -> Catalog:
        """Read the CHC catalog + providers registry from disk (cached).

        Mirrors :meth:`earthlens.gee.Catalog.load` and
        :meth:`earthlens.ecmwf.Catalog.load`. Default args resolve
        `CATALOG_PATH` / `PROVIDERS_PATH` at *call* time so test
        monkey-patches take effect. Validates every `Dataset.provider`
        slug against the registry.

        Args:
            catalog_path: Path to a CHC catalog. Two shapes are
                accepted: a directory containing `_index.yaml` plus
                one or more `<family>.yaml` siblings (the GEE-style
                default), or a single flat YAML file with
                `available_datasets:` / `regions:` / `datasets:` at
                top level (legacy back-compat). Defaults to
                module-level :data:`CATALOG_PATH` (the directory).
            providers_path: Path to `providers.yaml`. Defaults to
                module-level :data:`PROVIDERS_PATH`.

        Returns:
            A fully-populated :class:`Catalog`.

        Raises:
            ValueError: Propagated from :func:`_load_catalog_data` or
                :func:`earthlens.base.providers.load_providers`, plus
                an unregistered-slug error if any dataset references
                a provider not in the registry.
        """
        catalog_path = catalog_path if catalog_path is not None else CATALOG_PATH
        providers_path = providers_path if providers_path is not None else PROVIDERS_PATH
        available_datasets, regions_map, datasets = _load_catalog_data(catalog_path)
        providers = load_providers(providers_path)
        unknown = sorted(
            {d.provider for d in datasets.values() if d.provider and d.provider not in providers}
        )
        if unknown:
            raise ValueError(
                f"the following provider slugs are referenced by "
                f"`{catalog_path.name}` but missing from {providers_path}: "
                f"{unknown}. Add them to providers.yaml or fix the typo."
            )
        return cls(
            available_datasets=list(available_datasets),
            available_regions=dict(regions_map),
            datasets=dict(datasets),
            providers=dict(providers),
        )

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

    def get_variable(self, dataset_key: str, variable_name: str) -> Variable:
        """Return the :class:`Variable` for a `(dataset, variable)` pair.

        Args:
            dataset_key: CHC dataset identifier as it appears as a
                key in :attr:`datasets` (e.g. `"global-daily"`).
            variable_name: Short variable code. Required (M4).
                Pre-M4 this defaulted to `"precipitation"`, which
                was CHIRPS-2.0-centric and silently `KeyError`d on
                datasets whose variable was named differently
                (`chirtsdaily-tmax.tmax`, `wbgt-monthly.wbgt`,
                `spi-chirps3-*.spi`, `chc-cmip6-tmax-*.tmax_delta`,
                …). Callers must now pass the variable name
                explicitly.

        Returns:
            Variable: Per-variable metadata from the CHC catalog.

        Raises:
            KeyError: If `dataset_key` is not curated, or if
                `variable_name` is not declared under that dataset.

        Examples:
            - Look up precipitation for global daily data:

                ```python
                >>> from earthlens.chc import Catalog
                >>> spec = Catalog().get_variable("global-daily", "precipitation")
                >>> spec.units
                'mm/day'
                >>> spec.is_flux
                True

                ```
            - Non-precipitation variables work the same way:

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().get_variable("chirtsdaily-tmax", "tmax").units
                'degC'

                ```
        """
        return self.datasets[dataset_key].variables[variable_name]

    # `get_dataset(name)` (with the did-you-mean hint) and the dict-like
    # dunders are inherited from
    # :class:`earthlens.base.AbstractCatalog` (M1 in
    # planning/catalog-cross-backend-comparison.md).

    def health(self) -> dict[str, list[str]]:
        """Report structural hygiene issues across the loaded catalog.

        Returns a mapping `check_name -> [offending_keys]`. Most
        schema-level invariants (missing required fields, duplicate
        keys, unresolved `region:` keys) are already caught at load
        time — this method covers the residual quality checks that
        can't be expressed in the pydantic schema.

        Checks reported:

        * `dataset_without_variables` — datasets carrying zero
          curated variables (should always be `[]`; defence in depth).
        * `end_date_before_start_date` — datasets whose `end_date` is
          earlier than `start_date` (would yield an empty download
          window for every request).
        * `unreferenced_region` — region keys in `available_regions:`
          that no dataset's `region:` field points at — registry rot.
        * `unregistered_provider` — datasets whose `provider:` slug is
          missing from `providers.yaml`. Mirrors the load-time check
          but kept here so a Catalog built without `Catalog.load` can
          still self-report.
        * `unused_provider` — providers in the registry that no dataset
          references. Pure registry rot, same shape as
          `unreferenced_region`.
        * `index_missing_in_datasets` — keys in `available_datasets:`
          that have no entry under `datasets:` (consumer iterating the
          index would `KeyError` on `get_dataset(key)`). This is the
          C5 invariant; H1's centennial-trends mismatch was an instance
          of this drift.
        * `datasets_missing_in_index` — the reverse: keys under
          `datasets:` that the index doesn't advertise. Less severe
          but still surfaces a stale `_index.yaml`.
        * `variable_metadata_drift` — `(variable_name, temporal_resolution)`
          groups where the constituent rows disagree on `(units, types)`.
          Reported as `"<variable>/<temporal_resolution>"` strings. The
          description field is **not** considered (CMIP6 scenario rows
          legitimately vary by SSP/target year). Catches H3-style drift
          where a maintainer edits one row's units but forgets the
          siblings.
        """
        empty_dataset: list[str] = []
        bad_window: list[str] = []
        unregistered_provider: list[str] = []
        used_regions: set[str] = set()
        used_providers: set[str] = set()
        # Track `(units, types)` tuples per `(variable_name, temporal_resolution)`
        # so the drift check can flag heterogeneous groups in one pass.
        variable_metadata: dict[
            tuple[str, str], set[tuple[str, str | None]]
        ] = {}
        for ds_key, ds in self.datasets.items():
            if not ds.variables:
                empty_dataset.append(ds_key)
            if ds.region:
                used_regions.add(ds.region)
            if ds.end_date and ds.end_date < ds.start_date:
                bad_window.append(ds_key)
            if ds.provider:
                used_providers.add(ds.provider)
                if ds.provider not in self.providers:
                    unregistered_provider.append(ds_key)
            for var_name, var in ds.variables.items():
                bucket = variable_metadata.setdefault(
                    (var_name, ds.temporal_resolution), set()
                )
                bucket.add((var.units, var.types))
        unreferenced_region = sorted(set(self.available_regions) - used_regions)
        unused_provider = sorted(set(self.providers) - used_providers)
        index_set = set(self.available_datasets)
        datasets_set = set(self.datasets)
        index_missing_in_datasets = sorted(index_set - datasets_set)
        datasets_missing_in_index = sorted(datasets_set - index_set)
        variable_metadata_drift = sorted(
            f"{var_name}/{tres}"
            for (var_name, tres), tuples in variable_metadata.items()
            if len(tuples) > 1
        )
        return {
            "dataset_without_variables": sorted(empty_dataset),
            "end_date_before_start_date": sorted(bad_window),
            "unreferenced_region": unreferenced_region,
            "unregistered_provider": sorted(unregistered_provider),
            "unused_provider": unused_provider,
            "index_missing_in_datasets": index_missing_in_datasets,
            "datasets_missing_in_index": datasets_missing_in_index,
            "variable_metadata_drift": variable_metadata_drift,
        }

    def describe_region(self, region: str) -> dict[str, list[float]]:
        """Return the spatial bounds for a region name.

        Args:
            region: Region key as it appears in the `regions:` block of
                `_index.yaml` (e.g. `"global"`, `"africa"`,
                `"global-land"`).

        Returns:
            dict with keys `lat_boundaries` (`[south, north]`) and
            `lon_boundaries` (`[west, east]`).

        Raises:
            KeyError: If `region` is not defined in `available_regions`.

        Examples:
            - Read the standard global extent (pydantic coerces YAML
              ints to floats per the `list[float]` field annotation):

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().describe_region("global")
                {'lat_boundaries': [-50.0, 50.0], 'lon_boundaries': [-180.0, 180.0]}

                ```
            - CHIRTSdaily uses a wider land-surface extent:

                ```python
                >>> from earthlens.chc import Catalog
                >>> Catalog().describe_region("global-land")
                {'lat_boundaries': [-60.0, 70.0], 'lon_boundaries': [-180.0, 180.0]}

                ```
        """
        return self.available_regions[region]

    def describe(self, dataset_key: str) -> dict[str, Any]:
        """Return a structured introspection record for a CHC dataset.

        Useful for "what metadata does dataset X expose?" questions at
        runtime — the caller can dump the result without needing to
        walk the YAML themselves.

        For per-date datasets the record carries `file_patterns`;
        for discrete-files datasets (CenTrends, CHPclim v2) it
        carries `discrete_files` instead. The unused slot is `None`
        so consumers can branch on which is set without `KeyError`.

        Args:
            dataset_key: CHC dataset identifier as it appears as a
                key in :attr:`datasets`.

        Returns:
            dict with keys `dataset`, `region`,
            `temporal_resolution`, `pandas_freq`,
            `spatial_resolution`, `formats`, `lat_boundaries`,
            `lon_boundaries`, `start_date`, `end_date`,
            `ftp_bases`, `file_patterns`, `discrete_files`,
            `is_discrete`, `preliminary`, and `variables`.

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
                >>> info["is_discrete"]
                False
                >>> "precipitation" in info["variables"]
                True

                ```
            - Discrete-files datasets carry `discrete_files` instead
              of `file_patterns`; both keys exist so the caller can
              branch on `is_discrete` without a `KeyError`:

                ```python
                >>> from earthlens.chc import Catalog
                >>> info = Catalog().describe("centennial-trends-v1-monthly")
                >>> info["is_discrete"]
                True
                >>> info["file_patterns"] is None
                True
                >>> info["discrete_files"]["netcdf"]
                ['CenTrends_v1_monthly.nc']

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
            "file_patterns": (
                dict(ds.file_patterns) if ds.file_patterns is not None else None
            ),
            "discrete_files": (
                {k: list(v) for k, v in ds.discrete_files.items()}
                if ds.discrete_files is not None
                else None
            ),
            "is_discrete": ds.is_discrete,
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
                `"africa"`). `None` returns all datasets. An unknown
                region name (one not declared in `available_regions`)
                raises `ValueError` rather than silently returning an
                empty list (H2).
            temporal_resolution: Filter by temporal resolution (e.g.
                `"daily"`, `"monthly"`). `None` returns all datasets.

        Returns:
            list[str]: Sorted list of matching dataset keys.

        Raises:
            ValueError: If `region` is provided but not in
                `available_regions`. The message lists every valid
                region key so the caller can correct the typo.

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
        if region is not None and region not in self.available_regions:
            raise ValueError(
                f"region {region!r} is not declared in `_index.yaml`'s "
                f"`regions:` block. Known regions: "
                f"{sorted(self.available_regions)}."
            )
        if (
            temporal_resolution is not None
            and temporal_resolution not in _TEMPORAL_RESOLUTIONS
        ):
            raise ValueError(
                f"temporal_resolution {temporal_resolution!r} is not in "
                f"the catalog's vocabulary. Known values: "
                f"{list(_TEMPORAL_RESOLUTIONS)}."
            )
        result: list[str] = []
        for key, ds in self.datasets.items():
            if region is not None and ds.region != region:
                continue
            if temporal_resolution is not None and ds.temporal_resolution != temporal_resolution:
                continue
            result.append(key)
        return sorted(result)

