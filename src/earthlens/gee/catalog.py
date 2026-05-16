"""Dataset/band catalog loader for the Google Earth Engine backend.

Hosts :class:`Catalog`, the pydantic-backed reader for the bundled GEE
catalog — the analogue of `earthlens.ecmwf.Catalog` /
`cds_data_catalog.yaml`. The catalog ships as a directory of
per-category YAML files at `src/earthlens/gee/catalog/`
(`optical-multispectral.yaml`, `climate-reanalysis.yaml`,
`land-cover-change.yaml`, `hydrology-water.yaml`,
`community.yaml` for `projects/...` user-contributed assets, …),
plus a single `_index.yaml` carrying the merged
`available_datasets:` list. Per-file sections each map to a typed
field on :class:`Catalog` once merged:

* `available_datasets` (informational list of Earth Engine asset ids)
  → :attr:`Catalog.available_datasets`
* `datasets` (curated map of collections, each with band + aggregation
  metadata) → :attr:`Catalog.datasets`, with each value a
  :class:`Dataset` and each band a :class:`Band`.

Datasets are addressed by their Earth Engine asset id (e.g.
`"USGS/SRTMGL1_003"`, `"COPERNICUS/S2_SR_HARMONIZED"`); bands by
`(asset_id, band_id)` via :meth:`Catalog.get_band` (aliased as
:meth:`Catalog.get_variable` for parity with the ECMWF catalog).

The path to the bundled catalog directory lives at
:data:`CATALOG_PATH`; tests can monkey-patch that module attribute to
redirect the loader at a temporary directory or single YAML file.

Examples:
    - Construct the catalog and look up a dataset / band:

        ```python
        >>> from earthlens.gee.catalog import Catalog
        >>> cat = Catalog()
        >>> cat.get_dataset("USGS/SRTMGL1_003").spatial_resolution
        30.0
        >>> cat.get_band("USGS/SRTMGL1_003", "elevation").units
        'm'

        ```
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from earthlens.base import AbstractCatalog
from earthlens.base.yaml_loader import load_yaml_strict

CATALOG_PATH: Path = Path(__file__).parent / "catalog"
PROVIDERS_PATH: Path = Path(__file__).parent / "providers.yaml"

# Module-level cache of parsed catalog data. Keyed on the resolved path
# plus a tuple of `(file, mtime_ns)` for every YAML the load touched (so
# editing any per-provider file invalidates the cache without us having
# to inspect every entry). The full parse + pydantic validation across
# 11k+ band entries is ~5 s on the bundled catalog; a second `Catalog()`
# call on an unchanged tree should be ~1 ms.
_CATALOG_CACHE: dict[Any, tuple[list[str], dict[str, "Dataset"]]] = {}


def _yaml_files_for(path: Path) -> list[Path]:
    """Return the sorted list of YAML files that contribute to a catalog load.

    `path` may point at either:

    * a directory containing per-category `*.yaml` files (the default
      layout — `src/earthlens/gee/catalog/`); or
    * a single `*.yaml` file (back-compat for tests that monkey-patch
      `CATALOG_PATH` to a temp file, and for any external user still
      shipping the legacy monolithic `gee_data_catalog.yaml`).
    """
    if path.is_dir():
        return sorted(path.glob("*.yaml"))
    return [path]


def _load_catalog_data(path: Path) -> tuple[list[str], dict[str, "Dataset"]]:
    """Parse, validate and cache the catalog at `path`.

    Returns a `(available_datasets, datasets)` tuple of the same shape
    the :class:`Catalog` model exposes. The result is cached on the
    resolved path + every contributing file's mtime, so a fresh
    `Catalog()` on an unchanged tree skips YAML parsing and pydantic
    validation.

    Args:
        path: Filesystem path — either the per-category catalog directory
            (default `src/earthlens/gee/catalog/`) or a single `*.yaml`
            file.

    Returns:
        Tuple of `(list[str], dict[str, Dataset])` — the parsed
        `available_datasets:` (merged across files when loading a
        directory) and `datasets:` blocks.

    Raises:
        ValueError: If the YAML is missing, has no `datasets:` block,
            declares a duplicate dataset/band key, contains an unknown
            band field, or lists a curated dataset that is absent from
            `available_datasets`.
    """
    resolved = str(path.resolve())
    files = _yaml_files_for(path)
    mtime_tuple: tuple[tuple[str, int], ...]
    try:
        mtime_tuple = tuple((str(f), f.stat().st_mtime_ns) for f in files)
    except FileNotFoundError:
        mtime_tuple = ((resolved, 0),)
    key = (resolved, mtime_tuple)
    cached = _CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    merged_available: list[str] = []
    merged_datasets_yaml: dict[str, Any] = {}
    asset_origin: dict[str, Path] = {}
    for file_path in files:
        data = load_yaml_strict(file_path) or {}
        for aid in data.get("available_datasets") or []:
            merged_available.append(aid)
        for asset_id, body in (data.get("datasets") or {}).items():
            if asset_id in merged_datasets_yaml:
                first_seen = asset_origin[asset_id]
                raise ValueError(
                    f"dataset {asset_id!r} declared in two catalog files: "
                    f"{first_seen} and {file_path}"
                )
            merged_datasets_yaml[asset_id] = body
            asset_origin[asset_id] = file_path

    if not merged_datasets_yaml:
        raise ValueError(
            f"{path} is missing or has an empty 'datasets:' block. "
            "The catalog must contain at least one curated dataset."
        )

    available = set(merged_available)
    datasets: dict[str, Dataset] = {}
    for asset_id, body in merged_datasets_yaml.items():
        body = dict(body or {})
        bands_yaml = dict(body.pop("bands", {}) or {})
        bands: dict[str, Band] = {}
        for band_id, band_body in bands_yaml.items():
            try:
                bands[band_id] = Band(id=band_id, **dict(band_body or {}))
            except ValidationError as exc:
                raise ValueError(
                    f"invalid band {band_id!r} under dataset {asset_id!r} "
                    f"in {asset_origin[asset_id]}: {exc}"
                ) from exc
        try:
            datasets[asset_id] = Dataset(id=asset_id, bands=bands, **body)
        except ValidationError as exc:
            raise ValueError(
                f"invalid dataset {asset_id!r} in {asset_origin[asset_id]}: {exc}"
            ) from exc
        if available and asset_id not in available:
            raise ValueError(
                f"dataset {asset_id!r} is in 'datasets:' but missing from "
                f"'available_datasets:' in {path}; add it there too."
            )

    _CATALOG_CACHE[key] = (merged_available, datasets)
    return _CATALOG_CACHE[key]


def clear_catalog_cache() -> None:
    """Empty the module-level catalog + providers caches.

    Useful in tests that rewrite the catalog on disk and want to force a
    re-parse. Production callers do not need this — the cache keys
    include `st_mtime_ns`, so any real file mutation invalidates the
    entry on its own.
    """
    _CATALOG_CACHE.clear()
    _PROVIDERS_CACHE.clear()


class Cadence(BaseModel):
    """Native temporal step of an Earth Engine collection.

    A frozen value object derived from the STAC `gee:interval` field
    (which is sometimes inaccurate — the catalog YAML hand-corrects
    known cases).

    Attributes:
        interval: Number of `unit` periods between successive images
            (e.g. `16` for a 16-day composite).
        unit: The period unit.

    Examples:
        - Build a 16-day cadence and read its parts:
            ```python
            >>> c = Cadence(interval=16, unit="day")
            >>> c.interval
            16
            >>> c.unit
            'day'

            ```
        - A non-positive interval is rejected:
            ```python
            >>> Cadence(interval=0, unit="day")  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
                ...
            pydantic_core._pydantic_core.ValidationError: 1 validation error for Cadence

            ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    interval: int = Field(gt=0)
    unit: Literal["minute", "hour", "day", "pentad", "dekad", "month", "year"]


class Band(BaseModel):
    """Per-band metadata for one band of an Earth Engine dataset.

    A frozen value object; the band id is injected from the YAML
    mapping key at load time, so the YAML body does not repeat it.

    Attributes:
        id: The Earth Engine band id (e.g. `"SR_B4"`, `"precipitation"`).
        description: Human description of the band, or `None` when only
            the band id is known. The catalog drops bare `"Band <id>"`
            stub descriptions (which carry no information beyond the id)
            at load time — read the id if you just need a label.
        units: Physical unit string, or `None` (common for reflectance
            and indices).
        scale: Multiply the raw DN by this to get physical units, or
            `None` if no scaling applies.
        offset: Add this after scaling, or `None`.
        wavelength: Centre wavelength in micrometres for optical bands,
            or `None`.
        min: Typical / valid minimum DN, or `None`.
        max: Typical / valid maximum DN, or `None`.
        estimated_range: `True` if `min`/`max` are sample-based
            estimates rather than hard bounds.

    Examples:
        - Build a reflectance band and read its scaling:
            ```python
            >>> b = Band(id="SR_B4", description="Red surface reflectance", scale=2.75e-05, offset=-0.2)
            >>> b.id
            'SR_B4'
            >>> b.scale
            2.75e-05
            >>> b.units is None
            True

            ```
        - A band with only an id is fine — `description` is optional:
            ```python
            >>> Band(id="b1").description is None
            True

            ```
        - An unknown field is rejected:
            ```python
            >>> Band(id="x", description="d", colour="red")  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
                ...
            pydantic_core._pydantic_core.ValidationError: 1 validation error for Band

            ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    description: str | None = None
    units: str | None = None
    scale: float | None = None
    offset: float | None = None
    wavelength: float | None = None
    min: float | None = None
    max: float | None = None
    estimated_range: bool = False


class Extent(BaseModel):
    """Spatial/temporal coverage of an Earth Engine dataset.

    Attributes:
        start_date: First available date, `YYYY-MM-DD`.
        end_date: Last available date (`YYYY-MM-DD`), or `None` for a
            continuously updated collection.
        bbox: Spatial bounding box as `[west, south, east, north]` in
            EPSG:4326, or `None` for global coverage.

    Examples:
        - A bounded, completed dataset (e.g. SRTM):
            ```python
            >>> e = Extent(start_date="2000-02-11", end_date="2000-02-22")
            >>> e.start_date
            '2000-02-11'
            >>> e.end_date
            '2000-02-22'
            >>> e.bbox is None
            True

            ```
        - A continuously updated, regionally bounded dataset (e.g. CHIRPS):
            ```python
            >>> e = Extent(start_date="1981-01-01", bbox=(-180.0, -50.0, 180.0, 50.0))
            >>> e.end_date is None
            True
            >>> e.bbox
            (-180.0, -50.0, 180.0, 50.0)

            ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start_date: str
    end_date: str | None = None
    bbox: tuple[float, float, float, float] | None = None


class Dataset(BaseModel):
    """One Earth Engine collection/image with curated metadata.

    A frozen value object; the asset id is injected from the YAML
    mapping key at load time.

    Attributes:
        id: The Earth Engine asset id (e.g. `"COPERNICUS/S2_SR_HARMONIZED"`).
        title: Human title.
        provider: Canonical provider slug (e.g. `"nasa-lp-daac"`,
            `"copernicus"`), or `None`. The catalog validates that
            every non-`None` slug is registered in `providers.yaml`;
            resolve to a display name via `Catalog.get_provider(slug)`.
        ee_type: `"image"` (a single static raster), `"image_collection"`
            (a time series), `"table"` (a `FeatureCollection` — out of
            scope for the raster backend), `"table_collection"` (a
            collection of FeatureCollections, e.g. GEDI footprint shots),
            or `"bigquery_table"` (a BigQuery-backed table — also out of
            scope for the raster backend; included for catalog completeness).
        cadence: Native temporal step, or `None` for static images.
        spatial_resolution: Nominal pixel size in metres, or `None`.
        extent: Spatial/temporal coverage.
        default_reducer: Earth Engine reducer name used to collapse a
            temporal composite. One of `"mean"` (continuous fields /
            rates), `"median"` (cloud-screened optical scenes),
            `"mosaic"` (tiled / annual static maps), `"min"`, `"max"`,
            `"mode"`, or `"sum"`. Constrained by `Literal` to catch
            typos at YAML-load time.
        license: SPDX identifier (`"CC-BY-4.0"`, `"CC-BY-SA-4.0"`,
            `"CC-BY-NC-SA-4.0"`, `"CC0-1.0"`, `"ODbL-1.0"`, …) or one of
            the conventional values `"public-domain"`, `"proprietary"`
            (publisher-specific terms-of-service), or `"unknown"`. `None`
            for stanzas that pre-date the licence-normalisation pass.
        terms_note: Free-text note that doesn't fit the SPDX id —
            attribution requirements, custom commercial clauses, links
            to publisher terms-of-use pages, etc. `None` when the
            `license` field alone conveys everything.
        source: Where the asset originated, used to disambiguate the
            three publication paths Earth Engine exposes. One of:

            * `"ee_native"` — first-party Earth Engine catalog entry
              published by the data provider directly (the default;
              the vast majority of assets).
            * `"republished"` — a copy of an external dataset that
              Google or a partner re-hosts in the EE catalog under
              an organisation-style asset id.
            * `"community"` — a user-uploaded asset whose path starts
              with `projects/...`. Often documented less rigorously
              than the first two.

            Replaces the older ambiguous `user_uploaded: bool` flag
            (L2 in the catalog architecture review).
        bands: Band id → :class:`Band`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    provider: str | None = None
    ee_type: Literal["image", "image_collection", "table", "table_collection", "bigquery_table"] = "image_collection"
    cadence: Cadence | None = None
    spatial_resolution: float | None = None
    extent: Extent
    default_reducer: Literal["mean", "median", "mosaic", "min", "max", "mode", "sum"] = "median"
    license: str | None = None
    terms_note: str | None = None
    source: Literal["ee_native", "republished", "community"] = "ee_native"
    bands: dict[str, Band] = Field(default_factory=dict)

    @property
    def is_raster(self) -> bool:
        """Whether the asset is a raster (image or image_collection).

        Returns:
            `True` if :attr:`ee_type` is `"image"` or `"image_collection"`,
            else `False`. Use this to gate "can the backend download
            this?" decisions — :attr:`is_image_collection` excludes
            static `Image` assets (e.g. SRTM) and is therefore misleading
            for that question.

        Examples:
            - Both `image` and `image_collection` are rasters:
                ```python
                >>> from earthlens.gee.catalog import Catalog
                >>> cat = Catalog()
                >>> cat.get_dataset("USGS/SRTMGL1_003").is_raster
                True
                >>> cat.get_dataset("LANDSAT/LC09/C02/T1_L2").is_raster
                True

                ```
        """
        return self.ee_type in {"image", "image_collection"}

    @property
    def is_tabular(self) -> bool:
        """Whether the asset is tabular (FeatureCollection / table / BigQuery).

        Returns:
            `True` if :attr:`ee_type` is `"table"`, `"table_collection"`,
            or `"bigquery_table"`, else `False`. Tabular assets are out
            of scope for the raster backend.
        """
        return self.ee_type in {"table", "table_collection", "bigquery_table"}

    @property
    def is_image_collection(self) -> bool:
        """Whether the asset is an `ImageCollection` (vs. a single `Image`).

        Returns:
            `True` if :attr:`ee_type` is `"image_collection"`, else `False`.

        .. deprecated::
            Prefer :attr:`is_raster` for the "can the backend download
            this?" question — it correctly includes static `image`
            assets like SRTM. This narrower property is kept for
            back-compat with older callers.

        Examples:
            - SRTM is a single static image; Landsat 9 is a collection:
                ```python
                >>> from earthlens.gee.catalog import Catalog
                >>> cat = Catalog()
                >>> cat.get_dataset("USGS/SRTMGL1_003").is_image_collection
                False
                >>> cat.get_dataset("LANDSAT/LC09/C02/T1_L2").is_image_collection
                True

                ```
        """
        return self.ee_type == "image_collection"

    def get_band(self, band_id: str) -> Band:
        """Return the :class:`Band` for `band_id`.

        Args:
            band_id: The Earth Engine band id.

        Returns:
            The matching :class:`Band`.

        Raises:
            ValueError: If `band_id` is not a band of this dataset; the
                message suggests the closest known band id.

        Examples:
            - Look up a Landsat band and read its centre wavelength:
                ```python
                >>> from earthlens.gee.catalog import Catalog
                >>> ds = Catalog().get_dataset("LANDSAT/LC09/C02/T1_L2")
                >>> ds.get_band("SR_B4").description
                'Band 4 (red) surface reflectance'
                >>> ds.get_band("SR_B4").wavelength
                0.655

                ```
            - A misspelt band raises with a suggestion:
                ```python
                >>> from earthlens.gee.catalog import Catalog
                >>> Catalog().get_dataset("USGS/SRTMGL1_003").get_band("elevashun")  # doctest: +ELLIPSIS
                Traceback (most recent call last):
                    ...
                ValueError: 'elevashun' is not a band of 'USGS/SRTMGL1_003'. ... Did you mean 'elevation'?

                ```
        """
        try:
            return self.bands[band_id]
        except KeyError:
            close = difflib.get_close_matches(band_id, self.bands, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise ValueError(
                f"{band_id!r} is not a band of {self.id!r}. "
                f"Known bands: {sorted(self.bands)}.{hint}"
            ) from None


class Provider(BaseModel):
    """One canonical data provider — a slug-id with a display name and parent.

    Frozen value object loaded from `providers.yaml` (M1). Datasets in
    `catalog/*.yaml` reference providers by slug via the `provider:`
    field; the loader validates that every referenced slug is registered.

    Attributes:
        slug: Stable kebab-case identifier (e.g. `"nasa-lp-daac"`,
            `"copernicus-marine"`); injected from the YAML mapping key.
        display_name: Human-readable name to render in docs and UIs.
        parent: Slug of the parent provider, or `None` for top-level
            organisations. Used to group e.g. all NASA DAACs under the
            `"nasa"` umbrella.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    display_name: str
    parent: str | None = None


_PROVIDERS_CACHE: dict[tuple[str, int], dict[str, "Provider"]] = {}


def _load_providers(path: Path) -> dict[str, "Provider"]:
    """Parse + cache `providers.yaml`, keyed on `(path, mtime_ns)`."""
    resolved = str(path.resolve())
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise ValueError(
            f"providers registry not found at {path}; M1 (provider normalisation) "
            "expects this file alongside the catalog directory."
        ) from exc
    key = (resolved, mtime_ns)
    cached = _PROVIDERS_CACHE.get(key)
    if cached is not None:
        return cached

    data = load_yaml_strict(path) or {}
    raw = data.get("providers") or {}
    out: dict[str, Provider] = {}
    for slug, body in raw.items():
        try:
            out[slug] = Provider(slug=slug, **dict(body or {}))
        except ValidationError as exc:
            raise ValueError(f"invalid provider {slug!r} in {path}: {exc}") from exc
    # Sanity-check parent references.
    for slug, p in out.items():
        if p.parent is not None and p.parent not in out:
            raise ValueError(
                f"provider {slug!r} declares parent={p.parent!r}, "
                f"which is not a known provider slug in {path}"
            )
    _PROVIDERS_CACHE[key] = out
    return out


class Catalog(AbstractCatalog):
    """YAML-backed catalog of Earth Engine datasets for the GEE backend.

    Reads every `*.yaml` file under :data:`CATALOG_PATH` (the
    per-category `catalog/` directory shipped with the package) on
    construction plus the canonical provider registry at
    :data:`PROVIDERS_PATH`, merging them into one logical catalog and
    validating every entry into typed :class:`Dataset` / :class:`Band`
    / :class:`Provider` models. A duplicate dataset/band key in the
    YAML (within a file or across files), an unknown band field, a
    curated dataset not listed in `available_datasets`, or a
    `provider:` slug that is missing from `providers.yaml` is a
    load-time error.

    Attributes:
        available_datasets: Informational list of every Earth Engine
            asset id the package knows about.
        datasets: Curated asset id → :class:`Dataset`.
        providers: Canonical provider slug → :class:`Provider`.

    Examples:
        - Construct the catalog and look at what it holds:
            ```python
            >>> cat = Catalog()
            >>> "USGS/SRTMGL1_003" in cat.datasets
            True
            >>> "USGS/SRTMGL1_003" in cat.available_datasets
            True
            >>> cat.get_dataset("UCSB-CHG/CHIRPS/DAILY").default_reducer
            'mean'

            ```
        - Reach a band's metadata in one call:
            ```python
            >>> Catalog().get_band("MODIS/061/MOD11A1", "LST_Day_1km").scale
            0.02

            ```
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    available_datasets: list[str] = Field(default_factory=list)
    datasets: dict[str, Dataset] = Field(default_factory=dict)
    providers: dict[str, Provider] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Populate :attr:`available_datasets` / :attr:`datasets` / :attr:`providers`.

        Delegates dataset parsing to :func:`_load_catalog_data` and
        provider parsing to :func:`_load_providers`, both cached on
        `(path, mtime_ns)`. Validates that every `Dataset.provider`
        slug is present in the registry.

        Raises:
            ValueError: If the YAML is missing, has no `datasets:`
                block, declares the same key twice, contains an unknown
                band field, lists a curated dataset that is absent from
                `available_datasets`, or references an unregistered
                provider slug.
        """
        available_datasets, datasets = _load_catalog_data(CATALOG_PATH)
        providers = _load_providers(PROVIDERS_PATH)
        unknown = sorted(
            {d.provider for d in datasets.values() if d.provider and d.provider not in providers}
        )
        if unknown:
            raise ValueError(
                f"the following provider slugs are referenced by "
                f"`catalog/*.yaml` but missing from {PROVIDERS_PATH}: {unknown}. "
                "Add them to providers.yaml or fix the typo."
            )
        self.available_datasets = list(available_datasets)
        self.datasets = dict(datasets)
        self.providers = dict(providers)
        super().model_post_init(__context)

    def get_provider(self, slug: str) -> Provider:
        """Return the :class:`Provider` for `slug`.

        Args:
            slug: A registered provider slug (e.g. `"nasa-lp-daac"`).

        Returns:
            The matching :class:`Provider`.

        Raises:
            ValueError: If `slug` is not a registered provider; the
                message suggests the closest known slug.
        """
        try:
            return self.providers[slug]
        except KeyError:
            close = difflib.get_close_matches(slug, self.providers, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise ValueError(
                f"{slug!r} is not a registered provider. "
                f"Known providers: {sorted(self.providers)}.{hint}"
            ) from None

    def get_catalog(self) -> dict[str, Dataset]:
        """Return the curated dataset map (asset id → :class:`Dataset`).

        Returns:
            The :attr:`datasets` mapping.

        Examples:
            - The map is keyed by Earth Engine asset id:
                ```python
                >>> cat = Catalog()
                >>> "ESA/WorldCover/v200" in cat.get_catalog()
                True
                >>> cat.get_catalog()["ESA/WorldCover/v200"].title
                'ESA WorldCover 10m v200 (2021)'

                ```
        """
        return self.datasets

    def get_dataset(self, dataset_id: str) -> Dataset:
        """Return the :class:`Dataset` for `dataset_id`.

        Args:
            dataset_id: The Earth Engine asset id.

        Returns:
            The matching :class:`Dataset`.

        Raises:
            ValueError: If `dataset_id` is not in the curated catalog;
                the message suggests the closest known asset id.

        Examples:
            - Fetch a dataset and read its metadata:
                ```python
                >>> ds = Catalog().get_dataset("USGS/SRTMGL1_003")
                >>> ds.title
                'NASA SRTM Digital Elevation 30m'
                >>> ds.ee_type
                'image'
                >>> ds.spatial_resolution
                30.0

                ```
            - An unknown id raises with a suggestion:
                ```python
                >>> Catalog().get_dataset("USGS/SRTMGL1_004")  # doctest: +ELLIPSIS
                Traceback (most recent call last):
                    ...
                ValueError: 'USGS/SRTMGL1_004' is not in the GEE catalog. ...

                ```
        """
        try:
            return self.datasets[dataset_id]
        except KeyError:
            close = difflib.get_close_matches(dataset_id, self.datasets, n=1)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise ValueError(
                f"{dataset_id!r} is not in the GEE catalog. "
                f"Known datasets: {sorted(self.datasets)}.{hint}"
            ) from None

    def get_band(self, dataset_id: str, band_id: str) -> Band:
        """Return the :class:`Band` for `(dataset_id, band_id)`.

        Args:
            dataset_id: The Earth Engine asset id.
            band_id: The band id within that dataset.

        Returns:
            The matching :class:`Band`.

        Raises:
            ValueError: If the dataset or the band is unknown.

        Examples:
            - Read a precipitation band's unit:
                ```python
                >>> Catalog().get_band("UCSB-CHG/CHIRPS/DAILY", "precipitation").units
                'mm/d'

                ```
            - Read a Sentinel-2 band's centre wavelength:
                ```python
                >>> Catalog().get_band("COPERNICUS/S2_SR_HARMONIZED", "B4").wavelength
                0.6645

                ```

        See Also:
            get_variable: Identical; provided for naming parity with
                `earthlens.ecmwf.Catalog.get_variable`.
        """
        return self.get_dataset(dataset_id).get_band(band_id)

    def get_variable(self, dataset_id: str, band_id: str) -> Band:
        """Alias of :meth:`get_band` (name parity with the ECMWF catalog).

        Args:
            dataset_id: The Earth Engine asset id.
            band_id: The band id within that dataset.

        Returns:
            The matching :class:`Band`.

        Examples:
            - Same result as :meth:`get_band`:
                ```python
                >>> Catalog().get_variable("USGS/SRTMGL1_003", "elevation").units
                'm'

                ```
        """
        return self.get_band(dataset_id, band_id)

    # -- dict-like access over the curated `datasets:` map ---------------------

    def __getitem__(self, dataset_id: str) -> Dataset:
        """Dict-style lookup of a curated dataset (raises `KeyError` on miss).

        Equivalent to :meth:`get_dataset` but follows Python's mapping
        protocol — an unknown id yields `KeyError` (with the close-match
        hint from `get_dataset` preserved as the cause).

        Examples:
            - Look up a dataset by id:
                ```python
                >>> Catalog()["USGS/SRTMGL1_003"].title
                'NASA SRTM Digital Elevation 30m'

                ```
        """
        try:
            return self.get_dataset(dataset_id)
        except ValueError as exc:
            raise KeyError(dataset_id) from exc

    def __contains__(self, dataset_id: object) -> bool:
        """`asset_id in cat` — True when `asset_id` is a curated dataset."""
        return dataset_id in self.datasets

    def __iter__(self):
        """Iterate over the curated dataset asset ids."""
        return iter(self.datasets)

    def __len__(self) -> int:
        """Number of curated datasets in the catalog."""
        return len(self.datasets)

    def __repr__(self) -> str:
        """Compact developer repr — counts, not contents.

        Use `str(cat)` for the human-readable YAML dump of the curated
        datasets.

        Examples:
            - The repr summarises the catalog's size:
                ```python
                >>> repr(Catalog()).startswith("Catalog(datasets=12, available_datasets=")
                True

                ```
        """
        return (
            f"Catalog(datasets={len(self.datasets)}, "
            f"available_datasets={len(self.available_datasets)})"
        )

    def __str__(self) -> str:
        """Pretty-print the curated `datasets:` map as YAML.

        `None`-valued fields are omitted so the output stays readable;
        the ordering of `datasets:` keys follows insertion (which mirrors
        the YAML file).

        Examples:
            - The YAML dump starts with the first curated dataset's id:
                ```python
                >>> str(Catalog()).splitlines()[0]
                'USGS/SRTMGL1_003:'

                ```
        """
        import yaml

        body = {
            asset_id: dataset.model_dump(exclude_none=True)
            for asset_id, dataset in self.datasets.items()
        }
        return yaml.safe_dump(
            body, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
