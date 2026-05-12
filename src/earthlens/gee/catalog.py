"""Dataset/band catalog loader for the Google Earth Engine backend.

Hosts :class:`Catalog`, the pydantic-backed reader for
`gee_data_catalog.yaml` — the GEE analogue of
`earthlens.ecmwf.Catalog` / `cds_data_catalog.yaml`. The YAML's two
top-level sections each map to a typed field on :class:`Catalog`:

* `available_datasets` (informational list of Earth Engine asset ids)
  → :attr:`Catalog.available_datasets`
* `datasets` (curated map of collections, each with band + aggregation
  metadata) → :attr:`Catalog.datasets`, with each value a
  :class:`Dataset` and each band a :class:`Band`.

Datasets are addressed by their Earth Engine asset id (e.g.
`"USGS/SRTMGL1_003"`, `"COPERNICUS/S2_SR_HARMONIZED"`); bands by
`(asset_id, band_id)` via :meth:`Catalog.get_band` (aliased as
:meth:`Catalog.get_variable` for parity with the ECMWF catalog).

The path to the bundled YAML lives at :data:`CATALOG_PATH`; tests can
monkey-patch that module attribute to redirect the loader at a
temporary file.

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

CATALOG_PATH: Path = Path(__file__).parent / "gee_data_catalog.yaml"


class Cadence(BaseModel):
    """Native temporal step of an Earth Engine collection.

    A frozen value object derived from the STAC `gee:interval` field
    (which is sometimes inaccurate — the catalog YAML hand-corrects
    known cases).

    Attributes:
        interval: Number of `unit` periods between successive images
            (e.g. `16` for a 16-day composite).
        unit: The period unit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    interval: int = Field(gt=0)
    unit: Literal["minute", "hour", "day", "month", "year"]


class Band(BaseModel):
    """Per-band metadata for one band of an Earth Engine dataset.

    A frozen value object; the band id is injected from the YAML
    mapping key at load time, so the YAML body does not repeat it.

    Attributes:
        id: The Earth Engine band id (e.g. `"SR_B4"`, `"precipitation"`).
        description: Human description of the band.
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
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    description: str
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
        provider: Primary data provider, or `None`.
        ee_type: `"image"` (a single static raster), `"image_collection"`
            (a time series), or `"table"` (a `FeatureCollection` — out of
            scope for the raster backend).
        cadence: Native temporal step, or `None` for static images.
        spatial_resolution: Nominal pixel size in metres, or `None`.
        extent: Spatial/temporal coverage.
        default_reducer: Earth Engine reducer name used to collapse a
            temporal composite (`"median"` for cloud-screened optical
            scenes, `"mean"` for continuous fields / rates, `"mosaic"`
            for tiled or annual static maps).
        terms: Short licence / attribution note, or `None`.
        user_uploaded: `True` for community-uploaded assets.
        extras: Passthrough kwargs for the request builder.
        bands: Band id → :class:`Band`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    provider: str | None = None
    ee_type: Literal["image", "image_collection", "table"] = "image_collection"
    cadence: Cadence | None = None
    spatial_resolution: float | None = None
    extent: Extent
    default_reducer: str = "median"
    terms: str | None = None
    user_uploaded: bool = False
    extras: dict[str, Any] = Field(default_factory=dict)
    bands: dict[str, Band] = Field(default_factory=dict)

    @property
    def is_image_collection(self) -> bool:
        """Whether the asset is an `ImageCollection` (vs. a single `Image`)."""
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


class Catalog(AbstractCatalog):
    """YAML-backed catalog of Earth Engine datasets for the GEE backend.

    Reads `gee_data_catalog.yaml` (path in :data:`CATALOG_PATH`) on
    construction, validating every entry into typed :class:`Dataset` /
    :class:`Band` models. A duplicate dataset/band key in the YAML, an
    unknown band field, or a curated dataset not listed in
    `available_datasets` is a load-time error.

    Attributes:
        available_datasets: Informational list of every Earth Engine
            asset id the package knows about.
        datasets: Curated asset id → :class:`Dataset`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    available_datasets: list[str] = Field(default_factory=list)
    datasets: dict[str, Dataset] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Parse the bundled YAML into typed models after validation.

        Overrides :meth:`AbstractCatalog.model_post_init` to populate
        :attr:`available_datasets` and :attr:`datasets` directly.

        Raises:
            ValueError: If the YAML is missing, has no `datasets:`
                block, declares the same key twice, contains an unknown
                band field, or lists a curated dataset that is absent
                from `available_datasets`.
        """
        data = load_yaml_strict(CATALOG_PATH) or {}
        self.available_datasets = list(data.get("available_datasets") or [])

        datasets_yaml = data.get("datasets")
        if not datasets_yaml:
            raise ValueError(
                f"{CATALOG_PATH} is missing or has an empty 'datasets:' "
                "block. The catalog must contain at least one curated dataset."
            )

        available = set(self.available_datasets)
        datasets: dict[str, Dataset] = {}
        for asset_id, body in datasets_yaml.items():
            body = dict(body or {})
            bands_yaml = dict(body.pop("bands", {}) or {})
            bands: dict[str, Band] = {}
            for band_id, band_body in bands_yaml.items():
                try:
                    bands[band_id] = Band(id=band_id, **dict(band_body or {}))
                except ValidationError as exc:
                    raise ValueError(
                        f"invalid band {band_id!r} under dataset {asset_id!r} "
                        f"in {CATALOG_PATH}: {exc}"
                    ) from exc
            try:
                datasets[asset_id] = Dataset(id=asset_id, bands=bands, **body)
            except ValidationError as exc:
                raise ValueError(
                    f"invalid dataset {asset_id!r} in {CATALOG_PATH}: {exc}"
                ) from exc
            if available and asset_id not in available:
                raise ValueError(
                    f"dataset {asset_id!r} is in 'datasets:' but missing from "
                    f"'available_datasets:' in {CATALOG_PATH}; add it there too."
                )

        self.datasets = datasets
        super().model_post_init(__context)

    def get_catalog(self) -> dict[str, Dataset]:
        """Return the curated dataset map (asset id → :class:`Dataset`)."""
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
        """
        return self.get_dataset(dataset_id).get_band(band_id)

    def get_variable(self, dataset_id: str, band_id: str) -> Band:
        """Alias of :meth:`get_band` (name parity with the ECMWF catalog)."""
        return self.get_band(dataset_id, band_id)
