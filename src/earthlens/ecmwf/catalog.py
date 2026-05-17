"""Variable-catalog loader for the CDS-backed ECMWF data source.

Hosts :class:`Catalog`, the pydantic-backed reader for
`cds_data_catalog.yaml`. Split out of :mod:`earthlens.ecmwf.backend`
so the request / download machinery and the catalog file-IO live in
separate modules.

The YAML's two consumed top-level sections each map to a typed
field on :class:`Catalog`:

* `available_datasets` (informational list of CDS dataset names)
  → :attr:`Catalog.available_datasets`
* `datasets` (structural map of CDS datasets, each carrying a
  monthly variant and a per-variable map) → :attr:`Catalog.datasets`,
  with each value a :class:`Dataset`

The catalog has no flat per-variable view: variables are addressed
by the `(dataset_name, variable_name)` pair via
:meth:`Catalog.get_variable`. The same short code can legitimately
appear under more than one dataset (e.g. `"2m-temperature"` lives
in both `reanalysis-era5-single-levels` and
`reanalysis-era5-land`), so the dataset name is part of the
identity.

The path to the bundled YAML lives at :data:`CATALOG_PATH`; tests
can monkey-patch that module attribute to redirect the loader at a
temporary file.

Examples:
    - Construct the catalog and reach the structural map:

        ```python
        >>> from earthlens.ecmwf import Catalog
        >>> cat = Catalog()
        >>> cat.get_variable(
        ...     "reanalysis-era5-single-levels", "2m-temperature"
        ... ).nc_variable
        't2m'
        >>> cat.get_dataset("reanalysis-era5-pressure-levels").pressure_level
        ['1000']

        ```
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from earthlens.base import AbstractCatalog, FluxableLeaf, Provider
from earthlens.base.providers import (
    clear_providers_cache as _clear_providers_cache_base,
    load_providers,
)
from earthlens.base.yaml_loader import load_yaml_strict
from earthlens.ecmwf.constraints import fetch_constraints

_LEGACY_MARS_KEYS: frozenset[str] = frozenset(
    {"number_para", "download type", "var_name"}
)

CATALOG_PATH: Path = Path(__file__).parent / "cds_data_catalog.yaml"
PROVIDERS_PATH: Path = Path(__file__).parent / "providers.yaml"

# Module-level cache of parsed catalog data, keyed on
# `(resolved_path, mtime_ns)` so any real file mutation (`vim`-save,
# script append) invalidates the entry naturally. Mirrors the GEE
# pattern (H1 / M2) so repeated `Catalog()` construction is ~1 ms
# instead of paying the YAML parse + pydantic validation each time.
_CATALOG_CACHE: dict[tuple[str, int], tuple[list[str], dict[str, "Dataset"]]] = {}


def clear_catalog_cache() -> None:
    """Empty the module-level catalog + providers parse caches.

    Useful in tests that rewrite the catalog on disk and want to
    force a re-parse. Production callers do not need this — the
    cache keys include `st_mtime_ns`, so any real file mutation
    invalidates the entry on its own.
    """
    _CATALOG_CACHE.clear()
    _clear_providers_cache_base()


def _load_catalog_data(path: Path) -> tuple[list[str], dict[str, "Dataset"]]:
    """Parse, validate, and cache the CDS catalog at `path`.

    Returns a `(available_datasets, datasets)` tuple of the same
    shape :class:`Catalog` exposes. Cached on
    `(resolved-path, mtime_ns)` so a second `Catalog()` on an
    unchanged file skips both YAML parsing and pydantic validation.

    Raises:
        ValueError: If the YAML is missing, has no `datasets:` block,
            has no variables under any dataset, or declares the same
            key twice anywhere.
    """
    resolved = str(path.resolve())
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        mtime_ns = 0
    key = (resolved, mtime_ns)
    cached = _CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    data = load_yaml_strict(path) or {}
    datasets_yaml = data.get("datasets")
    if not datasets_yaml:
        raise ValueError(
            f"{path} is missing or has an empty "
            "'datasets' key. The catalog must contain at least "
            "one dataset with one variable. See the schema header "
            "at the top of the file."
        )

    structural, total_vars = _build_dataset_map(datasets_yaml, path)
    _synthesize_monthly_entries(structural, datasets_yaml)

    if total_vars == 0:
        raise ValueError(
            f"{path} has no variables under any dataset. "
            "The catalog must contain at least one variable. "
            "See the schema header at the top of the file."
        )

    available = list(data.get("available_datasets") or [])
    _CATALOG_CACHE[key] = (available, structural)
    return _CATALOG_CACHE[key]


def _provider_for_dataset(ds_name: str) -> str:
    """Map a CDS dataset name to its canonical provider slug (L2).

    Pattern-matched at load time rather than carried in the YAML —
    CDS dataset names already encode their provider through their
    name prefixes (`reanalysis-carra-*`, `projections-cmip5-*`,
    `projections-cordex-*`, etc.).
    """
    if ds_name.startswith(("reanalysis-carra", "reanalysis-pan-carra")):
        return "carra-consortium"
    if ds_name.startswith("reanalysis-cerra"):
        return "cerra-consortium"
    if ds_name.startswith("projections-cmip5"):
        return "cmip5-modelling-centres"
    if ds_name.startswith("projections-cordex"):
        return "cordex-consortium"
    return "ecmwf"


def _build_dataset_map(
    datasets_yaml: dict[str, dict[str, Any]],
    catalog_path: Path,
) -> tuple[dict[str, "Dataset"], int]:
    """Build the structural per-dataset :class:`Dataset` map (N1).

    Walks every entry in `datasets_yaml`, validates each variable into
    a :class:`Variable`, and packs the per-dataset metadata (monthly
    cross-reference, pressure_level / product_type defaults, extras,
    request_kind) into a :class:`Dataset`. Returns the map plus the
    total variable count (used by the caller to fail loudly when a
    catalog declares zero variables).

    Args:
        datasets_yaml: Raw `datasets:` mapping from the YAML.
        catalog_path: Path of the YAML file (used in error messages).

    Returns:
        (`structural`, `total_vars`) — the dataset map and the count
        of variables built across all datasets.

    Raises:
        ValueError: If any variable fails :class:`Variable` validation.
    """
    structural: dict[str, Dataset] = {}
    total_vars = 0
    for ds_name, ds_body in datasets_yaml.items():
        monthly = ds_body.get("monthly")
        pressure_level = ds_body.get("pressure_level")
        ds_product_type = ds_body.get("product_type")
        ds_extras = dict(ds_body.get("extras") or {})
        ds_request_kind = ds_body.get("request_kind", "form")
        ds_vars: dict[str, Variable] = {}
        for code, entry in (ds_body.get("variables") or {}).items():
            merged = dict(entry)
            merged["cds_dataset"] = ds_name
            # Default cds_variable to the slug-with-underscores form
            # of the YAML key (e.g. "2m-temperature" -> "2m_temperature").
            # A per-variable row may set `cds_variable` explicitly
            # to override this when the request name does not match.
            merged.setdefault("cds_variable", code.replace("-", "_"))
            # Per-variable override wins; otherwise inherit the
            # dataset-level default. Only single-level datasets
            # leave both unset.
            if "cds_pressure_level" not in merged and pressure_level is not None:
                merged["cds_pressure_level"] = pressure_level
            # Same parent-default / per-row-override pattern for
            # product_type. Parent unset → Variable's own default
            # (`["reanalysis"]`) applies.
            if "product_type" not in merged and ds_product_type is not None:
                merged["product_type"] = ds_product_type
            # Merge parent-level extras under per-row overrides:
            # row-level keys win on collision so a variable can
            # diverge from the family defaults (e.g. one CARRA row
            # carrying a different leadtime than the rest).
            row_extras = dict(merged.get("extras") or {})
            merged["extras"] = {**ds_extras, **row_extras}
            merged.setdefault("request_kind", ds_request_kind)
            try:
                ds_vars[code] = Variable(**merged)
            except ValidationError as exc:
                raise ValueError(
                    f"{catalog_path} entry {code!r} failed "
                    f"validation:\n{exc}"
                ) from exc
            total_vars += 1
        structural[ds_name] = Dataset(
            monthly=monthly,
            pressure_level=pressure_level,
            product_type=ds_product_type,
            extras=ds_extras,
            request_kind=ds_request_kind,
            provider=_provider_for_dataset(ds_name),
            variables=ds_vars,
        )
    return structural, total_vars


def _synthesize_monthly_entries(
    structural: dict[str, "Dataset"],
    datasets_yaml: dict[str, dict[str, Any]],
) -> None:
    """Mutate `structural` to add an auto-synthesised entry per `monthly:` xref (N1).

    The YAML keeps `monthly: <name>` on the parent dataset (compact); the
    catalog presents both names as queryable datasets with the same
    variable set, so users can name either form in their `variables`
    dict. The synthesised entry rebrands each variable's `cds_dataset`
    to the monthly name; everything else (variable code, units,
    nc_variable, extras) is shared. The monthly entry needs its own
    product_type — there is no hardcoded fallback; the parent must
    declare `monthly_product_type:` alongside `monthly:`.

    Raises:
        ValueError: If a dataset declares `monthly:` but is missing
            `monthly_product_type:`.
    """
    for ds_name, ds_body in datasets_yaml.items():
        ds = structural[ds_name]
        if not ds.monthly or ds.monthly in structural:
            continue
        monthly_pt = ds_body.get("monthly_product_type")
        if monthly_pt is None:
            raise ValueError(
                f"dataset {ds_name!r} declares `monthly: "
                f"{ds.monthly!r}` but no `monthly_product_type:`. "
                "Auto-synthesis of the monthly-means catalog "
                "entry needs an explicit product_type for the "
                "synthesized variables (e.g. "
                "`monthly_product_type: [monthly_averaged_reanalysis]`)."
            )
        rebranded = {
            code: var.model_copy(
                update={"cds_dataset": ds.monthly, "product_type": monthly_pt}
            )
            for code, var in ds.variables.items()
        }
        structural[ds.monthly] = Dataset(
            monthly=None,
            pressure_level=ds.pressure_level,
            product_type=monthly_pt,
            extras=dict(ds.extras),
            request_kind=ds.request_kind,
            provider=_provider_for_dataset(ds.monthly),
            variables=rebranded,
        )


# `_read_cdsapirc`, `list_recent_jobs`, `download_job` moved to
# `earthlens.ecmwf.jobs` (N3 in
# planning/catalog-cross-backend-comparison.md). Re-imported below as
# `_read_cdsapirc` so any external caller using `from
# earthlens.ecmwf.catalog import _read_cdsapirc` keeps working.
from earthlens.ecmwf.jobs import (  # noqa: E402 — re-export for back-compat
    download_job as _download_job_impl,
    list_recent_jobs as _list_recent_jobs_impl,
    read_cdsapirc as _read_cdsapirc,
)


class Variable(FluxableLeaf):
    """Per-variable catalog entry consumed by :class:`ECMWF`.

    A frozen pydantic model carrying the metadata for one row in
    `cds_data_catalog.yaml`. Loaded through :class:`Catalog`, which
    rewraps any :class:`pydantic.ValidationError` with the offending
    row's catalog key so a typo in the file (e.g. `cd_dataset` vs
    `cds_dataset`) surfaces at import time, not mid-download.
    Inherits `types` + `is_flux` from
    :class:`earthlens.base.FluxableLeaf`.

    Attributes:
        cds_dataset: CDS dataset short name used for daily / sub-daily
            requests, e.g. `"reanalysis-era5-single-levels"`.
        cds_variable: CDS variable name passed in the retrieve()
            request, e.g. `"2m_temperature"`.
        nc_variable: Short variable name inside the CDS NetCDF
            (e.g. `"t2m"`); used by post-processing scripts to
            index `fh.variables[...]`. See
            `examples/post_process_ecmwf_netcdf.py`.
        units: Raw ERA5 unit string emitted by CDS for this variable
            (used in the output filename). The package returns values
            in their native ERA5 units; downstream code is responsible
            for any unit conversion. See `docs/examples/catalog.md`
            for the conversion factors typical ERA5 workflows apply.
        cds_pressure_level: Optional list of pressure levels (as
            strings, e.g. `["1000"]`) for pressure-level datasets.
        product_type: CDS `product_type` request parameter. Picks
            the data flavor within a dataset (e.g. `["reanalysis"]`
            vs `["ensemble_mean"]` for ERA5; `["analysis"]` vs
            `["forecast_based"]` for CARRA). Default
            `["reanalysis"]` matches vanilla ERA5; auto-synthesized
            monthly-means entries override to
            `["monthly_averaged_reanalysis"]`. Per-dataset and
            per-variable overrides land here via the catalog
            loader's merge.
        types: Optional `"flux"` or `"state"` marker. Flux values
            are accumulated per timestep on CDS so monthly
            aggregation multiplies by the number of days in the
            month; state values are instantaneous.
        extras: Free-form bag of additional CDS request parameters
            forwarded verbatim to `client.retrieve()`. Holds the
            non-ERA5 request fields that newer CDS dataset families
            require — e.g. `{"domain": "east", "leadtime_hour": "1"}`
            for CARRA, `{"experiment": "ssp585", "model": "ec_earth3"}`
            for CMIP6. Keys not enumerated in this model are not
            silently dropped: they live here and reach the server.
    """

    # `model_config` (frozen=True, extra="forbid") and the `types` field
    # + `is_flux` property are inherited from `FluxableLeaf`.

    cds_dataset: str
    cds_variable: str
    nc_variable: str
    units: str
    product_type: list[str]
    cds_pressure_level: list[str] | None = None
    extras: dict[str, Any] = Field(default_factory=dict)
    request_kind: str = "form"

    @field_validator("extras", mode="before")
    @classmethod
    def _reject_legacy_mars_keys(cls, value: Any) -> Any:
        """Forbid the pre-cdsapi MARS keys from leaking back via `extras`.

        `number_para` / `download type` / `var_name` were the
        request-shape keys of the legacy MARS-ECMWFAPI flow. They are
        meaningless under cdsapi and would silently corrupt requests
        if they reached :meth:`ECMWF._api`; reject them at load time so
        a stale catalog row fails loud instead of mid-download.
        """
        if not isinstance(value, dict):
            return value
        offending = _LEGACY_MARS_KEYS & set(value)
        if offending:
            raise ValueError(
                f"extras carries legacy MARS keys {sorted(offending)!r}; "
                "these are not valid under cdsapi"
            )
        return value

    # `is_flux` property is inherited from `FluxableLeaf` (N1 in
    # planning/catalog-cross-backend-comparison.md).


class Dataset(BaseModel):
    """One CDS dataset's section in the catalog.

    Mirrors the shape of a single `datasets.<name>:` block in
    `cds_data_catalog.yaml` — the monthly-aggregate variant of the
    dataset, the default pressure levels (for pressure-level
    datasets), and the per-variable map. Same dataset name is used
    as the parent key in :attr:`Catalog.datasets`; it is not stored
    again here.

    Attributes:
        monthly: CDS dataset short name to use when
            `temporal_resolution == "monthly"`. `None` when the
            dataset has no monthly-aggregate variant.
        pressure_level: Default list of pressure levels (as strings,
            e.g. `["1000"]`) for pressure-level datasets. `None`
            for single-level datasets. Propagated to each variable's
            `cds_pressure_level` at load time.
        extras: Default extra CDS request parameters propagated into
            each child :class:`Variable`'s `extras` map. Per-row
            `extras:` overrides win over these defaults. Carries
            the family-wide selectors (e.g. `domain`, `leadtime_hour`,
            `experiment`, `model`) that the dataset's request shape
            requires beyond the ERA5 standard set.
        variables: Per-variable map keyed by the slugified short code
            (e.g. `"2m-temperature"`).

    Examples:
        - Inspect a single-level dataset entry:

            ```python
            >>> from earthlens.ecmwf import Catalog
            >>> cat = Catalog()
            >>> single = cat.datasets["reanalysis-era5-single-levels"]
            >>> single.monthly
            'reanalysis-era5-single-levels-monthly-means'
            >>> single.pressure_level is None
            True
            >>> "2m-temperature" in single.variables
            True

            ```
        - Pressure-level datasets carry the default level list:

            ```python
            >>> from earthlens.ecmwf import Catalog
            >>> cat = Catalog()
            >>> press = cat.datasets["reanalysis-era5-pressure-levels"]
            >>> press.pressure_level
            ['1000']
            >>> press.variables["temperature"].cds_pressure_level
            ['1000']

            ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    monthly: str | None = None
    pressure_level: list[str] | None = None
    product_type: list[str] | None = None
    extras: dict[str, Any] = Field(default_factory=dict)
    request_kind: str = "form"
    provider: str | None = None
    variables: dict[str, Variable] = Field(default_factory=dict)


class Catalog(AbstractCatalog):
    """Variable catalog for the CDS-backed ECMWF data source.

    Reads `cds_data_catalog.yaml` (shipped as package data) and
    exposes its consumed top-level sections as typed pydantic fields.
    Instantiate with no arguments (`Catalog()`) — :func:`model_post_init`
    parses the YAML and populates every field in one pass.

    Variables are addressed by the `(dataset_name, variable_name)`
    pair via :meth:`get_variable`; there is no flat per-code lookup.
    The same short code can legitimately appear under more than one
    dataset (e.g. `"2m-temperature"` lives in both
    `reanalysis-era5-single-levels` and `reanalysis-era5-land`), so
    the dataset name is part of the identity.

    Attributes:
        available_datasets: Informational list of every CDS dataset
            short name. Mirrors the `available_datasets:` block in
            the YAML; runtime code does not consume it.
        datasets: Structural map keyed by CDS dataset short name. Each
            value is a :class:`Dataset` carrying that dataset's
            monthly-aggregate variant and its per-variable map. The
            authoritative store: every catalog lookup goes through
            it.

    Examples:
        - Look up a variable by `(dataset_name, variable_name)`:

            ```python
            >>> from earthlens.ecmwf import Catalog
            >>> spec = Catalog().get_variable(
            ...     "reanalysis-era5-single-levels", "2m-temperature"
            ... )
            >>> spec.cds_dataset
            'reanalysis-era5-single-levels'
            >>> spec.nc_variable
            't2m'

            ```
        - The same short code under a different dataset is a
          different :class:`Variable`:

            ```python
            >>> from earthlens.ecmwf import Catalog
            >>> Catalog().get_variable(
            ...     "reanalysis-era5-land", "2m-temperature"
            ... ).cds_dataset
            'reanalysis-era5-land'

            ```
        - Iterate variables grouped by dataset (structural):

            ```python
            >>> from earthlens.ecmwf import Catalog
            >>> cat = Catalog()
            >>> cat.get_dataset("reanalysis-era5-pressure-levels").monthly
            'reanalysis-era5-pressure-levels-monthly-means'
            >>> sorted(cat.get_dataset("reanalysis-era5-pressure-levels").variables)[:3]
            ['divergence', 'fraction-of-cloud-cover', 'geopotential']

            ```
        - Inspect what CDS hosts overall:

            ```python
            >>> from earthlens.ecmwf import Catalog
            >>> len(Catalog().available_datasets)
            134

            ```
    """

    _catalog_kind: str = "CDS catalog"

    available_datasets: list[str] = Field(default_factory=list)
    datasets: dict[str, Dataset] = Field(default_factory=dict)
    providers: dict[str, Provider] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Auto-load `cds_data_catalog.yaml` when the user didn't supply one.

        `Catalog()` with no args is sugar for `Catalog.load()` — it
        reads the bundled YAML through the `(path, mtime_ns)`-keyed
        cache so repeated construction is ~1 ms. If the caller passed
        `datasets=...`, the disk read is skipped (test path; see
        :meth:`load` for the heavy-lifting classmethod).

        Raises:
            ValueError: When auto-loading, propagates the same errors
                as :meth:`load`.
        """
        if self.datasets:
            return
        loaded = Catalog.load()
        self.available_datasets = loaded.available_datasets
        self.datasets = loaded.datasets
        self.providers = loaded.providers

    @classmethod
    def load(
        cls,
        catalog_path: Path | None = None,
        providers_path: Path | None = None,
    ) -> Catalog:
        """Read the CDS catalog + providers registry from disk (cached).

        Mirrors :meth:`earthlens.gee.Catalog.load` so the two backends
        feel identical. Validates that every `Dataset.provider` slug
        is in the registry; an unregistered slug is a load-time error.

        Args:
            catalog_path: Path to a `cds_data_catalog.yaml`-shaped
                file. Defaults to module-level :data:`CATALOG_PATH`.
            providers_path: Path to `providers.yaml`. Defaults to
                module-level :data:`PROVIDERS_PATH`.

        Returns:
            A fully-populated :class:`Catalog`.

        Raises:
            ValueError: Propagated from :func:`_load_catalog_data` or
                :func:`earthlens.base.providers.load_providers`, plus
                an unregistered-slug error if the YAML references a
                provider not in the registry.
        """
        catalog_path = catalog_path if catalog_path is not None else CATALOG_PATH
        providers_path = providers_path if providers_path is not None else PROVIDERS_PATH
        available_datasets, datasets = _load_catalog_data(catalog_path)
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
            datasets=dict(datasets),
            providers=dict(providers),
        )

    def get_catalog(self) -> dict[str, Dataset]:
        """Return the structural per-dataset map.

        Satisfies the abstract base's contract; the actual parsing
        is done in :func:`model_post_init`.

        Returns:
            dict[str, Dataset]: One entry per CDS dataset. Same
            object as :attr:`datasets`.

        Examples:
            - Inspect the dataset count and a sample:

                ```python
                >>> from earthlens.ecmwf import Catalog
                >>> mapping = Catalog().get_catalog()
                >>> "reanalysis-era5-single-levels" in mapping
                True
                >>> mapping["reanalysis-era5-single-levels"].monthly
                'reanalysis-era5-single-levels-monthly-means'

                ```
        """
        return self.datasets

    def get_variable(self, dataset_name: str, variable_name: str) -> Variable:
        """Return the :class:`Variable` for a `(dataset, code)` pair.

        Args:
            dataset_name: CDS dataset short name as it appears as a
                key in :attr:`datasets` (e.g.
                `"reanalysis-era5-single-levels"`).
            variable_name: Short variable code as it appears as a
                YAML key under that dataset (e.g.
                `"2m-temperature"`, `"total-precipitation"`).

        Returns:
            Variable: Per-variable metadata loaded from
            `cds_data_catalog.yaml`.

        Raises:
            KeyError: If `dataset_name` is not curated, or if
                `variable_name` is not declared under that dataset.

        Examples:
            - Look up a single-level ERA5 variable and read its CDS
              dataset and NetCDF short name:

                ```python
                >>> from earthlens.ecmwf import Catalog
                >>> spec = Catalog().get_variable(
                ...     "reanalysis-era5-single-levels", "2m-temperature"
                ... )
                >>> spec.cds_dataset
                'reanalysis-era5-single-levels'
                >>> spec.nc_variable, spec.units
                ('t2m', 'K')

                ```
            - Pressure-level variables expose `cds_pressure_level`:

                ```python
                >>> from earthlens.ecmwf import Catalog
                >>> spec = Catalog().get_variable(
                ...     "reanalysis-era5-pressure-levels", "temperature"
                ... )
                >>> spec.cds_pressure_level
                ['1000']

                ```
            - The same short code under a different dataset is a
              different Variable:

                ```python
                >>> from earthlens.ecmwf import Catalog
                >>> Catalog().get_variable(
                ...     "reanalysis-era5-land", "2m-temperature"
                ... ).cds_dataset
                'reanalysis-era5-land'

                ```
            - Unknown dataset or variable raises `KeyError`:

                ```python
                >>> from earthlens.ecmwf import Catalog
                >>> Catalog().get_variable(
                ...     "reanalysis-era5-single-levels", "not-a-variable"
                ... )
                Traceback (most recent call last):
                    ...
                KeyError: 'not-a-variable'

                ```
        """
        return self.datasets[dataset_name].variables[variable_name]

    # `get_dataset(name)` (with the did-you-mean hint) and the dict-like
    # `__getitem__` / `__contains__` / `__iter__` / `__len__` / `__repr__`
    # / `__str__` dunders are inherited from
    # :class:`earthlens.base.AbstractCatalog` (M1 in
    # planning/catalog-cross-backend-comparison.md).

    def health(self) -> dict[str, list[str]]:
        """Report structural hygiene issues across the loaded catalog (L1).

        Returns a mapping `check_name -> list of "<dataset>/<variable>"
        offenders`. An empty list means the check is currently passing;
        an empty dict means the catalog is clean. Most schema-level
        invariants (duplicate keys, unknown fields, missing required
        fields, legacy MARS keys in `extras`) are already enforced at
        load time — this method covers the residual data-quality checks
        that can't be expressed in the pydantic schema.

        Checks reported:

        * `variable_missing_nc_variable` — variables whose
          `nc_variable` is empty or whitespace-only (would break
          downstream NetCDF reads).
        * `dataset_without_variables` — datasets carrying zero
          curated variables. Should always be `[]` since the loader
          rejects these; included for defence in depth.
        """
        missing_nc: list[str] = []
        empty_dataset: list[str] = []
        unregistered_provider: list[str] = []
        used_providers: set[str] = set()
        for ds_name, ds in self.datasets.items():
            if not ds.variables:
                empty_dataset.append(ds_name)
                continue
            for var_code, var in ds.variables.items():
                if not var.nc_variable or not var.nc_variable.strip():
                    missing_nc.append(f"{ds_name}/{var_code}")
            if ds.provider:
                used_providers.add(ds.provider)
                if ds.provider not in self.providers:
                    unregistered_provider.append(ds_name)
        unused_provider = sorted(set(self.providers) - used_providers)
        return {
            "variable_missing_nc_variable": sorted(missing_nc),
            "dataset_without_variables": sorted(empty_dataset),
            "unregistered_provider": sorted(unregistered_provider),
            "unused_provider": unused_provider,
        }

    def describe(self, dataset_name: str) -> dict[str, Any]:
        """Return a structured introspection record for a CDS dataset.

        Useful for "what variables and extras does dataset X expose?"
        questions at runtime — the CLI / notebook caller can dump
        the result without needing to walk the YAML themselves.

        Args:
            dataset_name: CDS dataset short name as it appears as a
                key in :attr:`datasets` (e.g.
                `"reanalysis-era5-land"`).

        Returns:
            dict with keys `dataset` (the short name), `monthly`
            (the monthly-aggregate dataset name or `None`),
            `pressure_level` (the default level list or `None`),
            `extras` (the parent-level request defaults), and
            `variables` (sorted list of the variable short codes
            available under this dataset).

        Raises:
            KeyError: If `dataset_name` is not a curated dataset
                (i.e. not present in :attr:`datasets`).

        Examples:
            - Describe ERA5-Land at a glance:

                ```python
                >>> from earthlens.ecmwf import Catalog
                >>> info = Catalog().describe("reanalysis-era5-land")
                >>> info["dataset"]
                'reanalysis-era5-land'
                >>> info["monthly"]
                'reanalysis-era5-land-monthly-means'
                >>> len(info["variables"]) == 60
                True
                >>> "2m-temperature" in info["variables"]
                True

                ```
        """
        ds = self.get_dataset(dataset_name)
        return {
            "dataset": dataset_name,
            "monthly": ds.monthly,
            "pressure_level": ds.pressure_level,
            "extras": dict(ds.extras),
            "variables": sorted(ds.variables),
        }

    def minimal_valid_request(self, dataset_name: str) -> dict[str, Any]:
        """Return a known-valid minimal request for `dataset_name`.

        Walks the dataset's published `constraints.json` (cached
        per-process) and returns the first entry expanded into a
        request dict with one value per selector. Useful for:

        * verifying a CDS account is set up correctly (submit the
          returned dict via :meth:`cdsapi.Client.retrieve` and watch
          for a NetCDF rather than a 400),
        * seeing what a valid extras schema looks like for a new
          dataset before authoring catalog rows,
        * starting points for tests.

        The returned request always carries `data_format: netcdf`;
        the rest is whatever the first constraint entry enumerates.

        Args:
            dataset_name: CDS dataset short name. Does not need to be
                in :attr:`datasets` — the constraints endpoint is
                hit directly so any addressable dataset works.

        Returns:
            dict[str, Any]: A request dict ready to pass to
            :meth:`cdsapi.Client.retrieve`. Empty dict (besides
            `data_format`) when the dataset's constraints are
            empty / unreachable.

        Examples:
            - Inspect ECMWF's published shape for a new dataset
              before authoring rows. Marked `# doctest: +SKIP`
              because it requires network access:

                ```python
                >>> from earthlens.ecmwf import Catalog
                >>> req = Catalog().minimal_valid_request(  # doctest: +SKIP
                ...     "reanalysis-cerra-land",
                ... )
                >>> sorted(req.keys())  # doctest: +SKIP
                ['data_format', 'day', 'leadtime_hour', 'level_type', ...]

                ```
        """
        constraints = fetch_constraints(dataset_name)
        request: dict[str, Any] = {"data_format": "netcdf"}
        if not constraints:
            return request
        # Pick the first entry that has at least one variable —
        # entries with empty `variable` lists are dataset-form
        # placeholders that don't make a usable retrieve request.
        for entry in constraints:
            if entry.get("variable"):
                for key, value in entry.items():
                    if isinstance(value, list) and value:
                        request[key] = value[:1]
                    else:
                        request[key] = value
                return request
        # No entry had variables — fall back to the first one anyway
        # (some datasets identify the data column via an extra rather
        # than a `variable` list).
        first = constraints[0]
        for key, value in first.items():
            if isinstance(value, list) and value:
                request[key] = value[:1]
            else:
                request[key] = value
        return request

    def list_recent_jobs(
        self,
        status: str | None = None,
        max_age_min: int = 60,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the user's recent CDS retrieval jobs.

        Thin wrapper that delegates to
        :func:`earthlens.ecmwf.jobs.list_recent_jobs` (N3); see that
        for the full docstring. Kept on `Catalog` as a convenience so
        `Catalog().list_recent_jobs(...)` keeps working.
        """
        return _list_recent_jobs_impl(
            status=status, max_age_min=max_age_min, limit=limit
        )

    def download_job(
        self,
        job_id: str,
        target: Path | str,
        chunk_size: int = 1 << 20,
    ) -> Path:
        """Download the result asset of a successful CDS job.

        Thin wrapper that delegates to
        :func:`earthlens.ecmwf.jobs.download_job` (N3); see that for
        the full docstring.
        """
        return _download_job_impl(job_id, target, chunk_size=chunk_size)
