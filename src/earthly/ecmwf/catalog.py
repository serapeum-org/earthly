"""Variable-catalog loader for the CDS-backed ECMWF data source.

Hosts :class:`Catalog`, the pydantic-backed reader for
`cds_data_catalog.yaml`. Split out of :mod:`earthly.ecmwf.backend`
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
        >>> from earthly.ecmwf import Catalog
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

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from earthly.base import AbstractCatalog

_LEGACY_MARS_KEYS: frozenset[str] = frozenset(
    {"number_para", "download type", "var_name"}
)

CATALOG_PATH: Path = Path(__file__).parent / "cds_data_catalog.yaml"


class _StrictSafeLoader(yaml.SafeLoader):
    """:class:`yaml.SafeLoader` that rejects duplicate keys in any mapping.

    PyYAML's default behaviour silently merges duplicate mapping keys
    (last wins), which would let a copy-paste typo in
    `cds_data_catalog.yaml` like two `"2m-temperature":` blocks under
    the same dataset go undetected — the second silently shadows the
    first. This loader fails loud at parse time with a `ValueError`
    naming the offending line, so the YAML author sees the mistake
    on the first `Catalog()` instantiation.
    """


def _construct_mapping_no_duplicates(
    loader: _StrictSafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """Build a dict from a YAML mapping node, rejecting duplicate keys.

    Replaces :meth:`yaml.SafeLoader.construct_mapping` for
    :class:`_StrictSafeLoader` so every YAML mapping in
    `cds_data_catalog.yaml` (the dataset map, each dataset's
    `variables:` block, every `extras:` map, etc.) is required to
    have unique keys.
    """
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            mark = key_node.start_mark
            raise ValueError(
                f"duplicate YAML key {key!r} at line {mark.line + 1}, "
                f"column {mark.column + 1} of {mark.name}: every key in "
                "a YAML mapping must be unique (in particular, every "
                "variable code must be unique within its dataset's "
                "`variables:` block)"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


def _read_cdsapirc() -> dict[str, str]:
    """Parse `~/.cdsapirc` into a {url, key} dict.

    Used by :meth:`Catalog.list_recent_jobs` and
    :meth:`Catalog.download_job` to authenticate the bare HTTP
    calls without spinning up a full :class:`cdsapi.Client`.
    """
    cfg: dict[str, str] = {}
    for line in (Path.home() / ".cdsapirc").read_text().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            cfg[key.strip()] = value.strip()
    return cfg


class Variable(BaseModel):
    """Per-variable catalog entry consumed by :class:`ECMWF`.

    A frozen pydantic model carrying the metadata for one row in
    `cds_data_catalog.yaml`. Loading the YAML through
    :meth:`from_dict` validates required fields up front so a typo
    in the file (e.g. `cd_dataset` vs `cds_dataset`) surfaces at
    import time, not mid-download.

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

    model_config = ConfigDict(frozen=True, extra="forbid")

    cds_dataset: str
    cds_variable: str
    nc_variable: str
    units: str
    product_type: list[str]
    cds_pressure_level: list[str] | None = None
    types: str | None = None
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

    @classmethod
    def from_dict(cls, code: str, data: dict[str, Any]) -> Variable:
        """Build a :class:`Variable` from a raw catalog entry.

        Wraps :class:`pydantic.ValidationError` so the message names
        the catalog row that failed.

        Implementation note (L4 in `planning/cdsapi/backend-review.md`):
        The proposal was to collapse this method into a
        `model_validator(mode='wrap')` on the model itself. Empirically
        that does not pay off in pydantic v2: any `ValueError` raised
        from inside a wrap validator gets re-wrapped into
        :class:`pydantic.ValidationError` at the `model_validate`
        boundary, so the row-naming exception type is lost. Keeping
        the rewrap in this classmethod is the cleanest way to surface
        a `ValueError` whose message names the offending YAML row.

        Args:
            code: Catalog key (e.g. `"2m-temperature"`) — used only in the
                error message so the user can see which row is broken.
            data: The dict loaded from the YAML for `code`.

        Returns:
            Variable: The validated, frozen instance.

        Raises:
            ValueError: If a required key is missing or an unknown
                key is present (catches typos like `cd_dataset`
                vs `cds_dataset`).

        Examples:
            - Build a Variable from a complete entry and inspect it:

                ```python
                >>> from earthly.ecmwf import Variable
                >>> spec = Variable.from_dict("2m-temperature", {
                ...     "cds_dataset": "reanalysis-era5-single-levels",
                ...     "cds_variable": "2m_temperature",
                ...     "nc_variable": "t2m",
                ...     "units": "K",
                ...     "product_type": ["reanalysis"],
                ...     "types": "state",
                ... })
                >>> spec.cds_variable, spec.nc_variable, spec.units
                ('2m_temperature', 't2m', 'K')

                ```
            - A typo in a key name is caught at construction time —
              the wrapped pydantic error names the offending row:

                ```python
                >>> from earthly.ecmwf import Variable
                >>> try:
                ...     Variable.from_dict("2m-temperature", {
                ...         "cd_dataset": "reanalysis-era5-single-levels",
                ...         "cds_variable": "2m_temperature",
                ...         "nc_variable": "t2m",
                ...         "units": "K",
                ...         "product_type": ["reanalysis"],
                ...     })
                ... except ValueError as exc:
                ...     str(exc).splitlines()[0]
                "cds_data_catalog.yaml entry '2m-temperature' failed validation:"

                ```
        """
        try:
            return cls(**data)
        except ValidationError as exc:
            raise ValueError(
                f"cds_data_catalog.yaml entry {code!r} failed validation:\n{exc}"
            ) from exc

    @property
    def is_flux(self) -> bool:
        """Whether this variable is a flux (drives monthly accumulation scaling).

        Returns:
            bool: `True` when `types == "flux"` — flux values are
            accumulated per timestep on CDS, so monthly aggregation
            multiplies by the number of days in the month. `False`
            for state variables (instantaneous samples) and when
            `types` is unset.
        """
        return self.types == "flux"


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
            >>> from earthly.ecmwf import Catalog
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
            >>> from earthly.ecmwf import Catalog
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
            >>> from earthly.ecmwf import Catalog
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
            >>> from earthly.ecmwf import Catalog
            >>> Catalog().get_variable(
            ...     "reanalysis-era5-land", "2m-temperature"
            ... ).cds_dataset
            'reanalysis-era5-land'

            ```
        - Iterate variables grouped by dataset (structural):

            ```python
            >>> from earthly.ecmwf import Catalog
            >>> cat = Catalog()
            >>> cat.get_dataset("reanalysis-era5-pressure-levels").monthly
            'reanalysis-era5-pressure-levels-monthly-means'
            >>> sorted(cat.get_dataset("reanalysis-era5-pressure-levels").variables)[:3]
            ['divergence', 'fraction-of-cloud-cover', 'geopotential']

            ```
        - Inspect what CDS hosts overall:

            ```python
            >>> from earthly.ecmwf import Catalog
            >>> len(Catalog().available_datasets)
            134

            ```
    """

    available_datasets: list[str] = Field(default_factory=list)
    datasets: dict[str, Dataset] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Parse `cds_data_catalog.yaml` into the exposed fields.

        Overrides :func:`AbstractCatalog.model_post_init` to populate
        :attr:`available_datasets` and :attr:`datasets` directly,
        bypassing the flat-`get_catalog` path the base class assumes.

        Raises:
            ValueError: If the YAML is missing or has an empty
                `datasets:` block, if no variables appear under any
                dataset, or if any YAML mapping (dataset block,
                `variables:` map, `extras:` map, ...) declares the
                same key twice.
        """
        catalog_path = CATALOG_PATH
        with open(catalog_path, encoding="utf-8") as stream:
            # `_StrictSafeLoader` subclasses `yaml.SafeLoader`; it is
            # safe (no arbitrary object instantiation). bandit's B506
            # pattern flags any `yaml.load` regardless of the loader.
            data = yaml.load(stream, Loader=_StrictSafeLoader) or {}  # nosec B506
        datasets_yaml = data.get("datasets")
        if not datasets_yaml:
            raise ValueError(
                f"{catalog_path} is missing or has an empty "
                "'datasets' key. The catalog must contain at least "
                "one dataset with one variable. See the schema header "
                "at the top of the file."
            )

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
                ds_vars[code] = Variable.from_dict(code, merged)
                total_vars += 1
            structural[ds_name] = Dataset(
                monthly=monthly,
                pressure_level=pressure_level,
                product_type=ds_product_type,
                extras=ds_extras,
                request_kind=ds_request_kind,
                variables=ds_vars,
            )

        # Auto-synthesize a first-class entry for each `monthly:`
        # cross-reference. The YAML keeps `monthly: <name>` on the
        # parent dataset (compact); the catalog presents both names
        # as queryable datasets with the same variable set, so users
        # can name either form in their `variables` dict. The
        # synthesized entry rebrands each variable's `cds_dataset` to
        # the monthly name; everything else (variable code, units,
        # nc_variable, extras) is shared.
        # The synthesized monthly-means entry needs its own
        # product_type — it cannot be inferred. The parent must
        # declare `monthly_product_type:` alongside `monthly:`. No
        # hardcoded fallback.
        for ds_name, ds_body in datasets_yaml.items():
            ds = structural[ds_name]
            if ds.monthly and ds.monthly not in structural:
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
                        update={
                            "cds_dataset": ds.monthly,
                            "product_type": monthly_pt,
                        }
                    )
                    for code, var in ds.variables.items()
                }
                structural[ds.monthly] = Dataset(
                    monthly=None,
                    pressure_level=ds.pressure_level,
                    product_type=monthly_pt,
                    extras=dict(ds.extras),
                    request_kind=ds.request_kind,
                    variables=rebranded,
                )

        if total_vars == 0:
            raise ValueError(
                f"{catalog_path} has no variables under any dataset. "
                "The catalog must contain at least one variable. "
                "See the schema header at the top of the file."
            )

        self.available_datasets = list(data.get("available_datasets") or [])
        self.datasets = structural

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
                >>> from earthly.ecmwf import Catalog
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
                >>> from earthly.ecmwf import Catalog
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
                >>> from earthly.ecmwf import Catalog
                >>> spec = Catalog().get_variable(
                ...     "reanalysis-era5-pressure-levels", "temperature"
                ... )
                >>> spec.cds_pressure_level
                ['1000']

                ```
            - The same short code under a different dataset is a
              different Variable:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> Catalog().get_variable(
                ...     "reanalysis-era5-land", "2m-temperature"
                ... ).cds_dataset
                'reanalysis-era5-land'

                ```
            - Unknown dataset or variable raises `KeyError`:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> Catalog().get_variable(
                ...     "reanalysis-era5-single-levels", "not-a-variable"
                ... )
                Traceback (most recent call last):
                    ...
                KeyError: 'not-a-variable'

                ```
        """
        return self.datasets[dataset_name].variables[variable_name]

    def get_dataset(self, name: str) -> Dataset:
        """Return the :class:`Dataset` record for a CDS dataset short name.

        Args:
            name: CDS dataset short name as it appears as a key in
                :attr:`datasets` (e.g. `"reanalysis-era5-land"`).

        Returns:
            Dataset: Structural record carrying the dataset's
            monthly-aggregate variant, default pressure levels,
            parent-level extras, request kind, and per-variable map.

        Raises:
            KeyError: If `name` is not a curated dataset.

        Examples:
            - Read a dataset's monthly variant and variable count:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> ds = Catalog().get_dataset("reanalysis-era5-pressure-levels")
                >>> ds.monthly
                'reanalysis-era5-pressure-levels-monthly-means'
                >>> sorted(ds.variables)[:3]
                ['divergence', 'fraction-of-cloud-cover', 'geopotential']

                ```
        """
        return self.datasets[name]

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
                >>> from earthly.ecmwf import Catalog
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
                >>> from earthly.ecmwf import Catalog
                >>> req = Catalog().minimal_valid_request(  # doctest: +SKIP
                ...     "reanalysis-cerra-land",
                ... )
                >>> sorted(req.keys())  # doctest: +SKIP
                ['data_format', 'day', 'leadtime_hour', 'level_type', ...]

                ```
        """
        from earthly.ecmwf.constraints import fetch_constraints

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

        Wraps `GET /retrieve/v1/jobs` with the same Personal
        Access Token cdsapi uses (read from `~/.cdsapirc`).
        Useful for resuming downloads after a script crash, or
        inspecting which probes have completed without rerunning
        them.

        Args:
            status: Optional filter — one of `"accepted"`,
                `"running"`, `"successful"`, `"failed"`,
                `"rejected"`. `None` returns every status.
            max_age_min: Drop entries older than this many minutes
                (CDS retains job records for a few weeks). Defaults
                to `60`.
            limit: Hard cap on returned entries, sent as the
                `limit` query param. Defaults to `50`.

        Returns:
            list[dict[str, Any]]: Each entry has at least
            `jobID` / `processID` (= dataset name) / `status` /
            `created`. See the CDS OGC API processes spec for the
            full schema.

        Examples:
            - List successful retrievals from the last hour
              (`# doctest: +SKIP` — needs a configured
              `~/.cdsapirc`):

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> cat = Catalog()
                >>> jobs = cat.list_recent_jobs(  # doctest: +SKIP
                ...     status="successful", max_age_min=60,
                ... )
                >>> for j in jobs:  # doctest: +SKIP
                ...     print(j["processID"], j["jobID"][:8])

                ```
        """
        import datetime

        import requests

        cfg = _read_cdsapirc()
        url = cfg["url"].rstrip("/") + "/retrieve/v1/jobs"
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        resp = requests.get(
            url,
            headers={"PRIVATE-TOKEN": cfg["key"]},
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        out: list[dict[str, Any]] = []
        for job in resp.json().get("jobs", []):
            created = job.get("created", "")
            if not created:
                continue
            ago = (
                now - datetime.datetime.fromisoformat(created.replace("Z", ""))
            ).total_seconds() / 60
            if ago <= max_age_min:
                out.append(job)
        return out

    def download_job(
        self,
        job_id: str,
        target: Path | str,
        chunk_size: int = 1 << 20,
    ) -> Path:
        """Download the result asset of a successful CDS job.

        Looks up `job_id` via `GET /retrieve/v1/jobs/<id>/results`,
        follows the asset's `href`, and streams the body into
        `target`. Idempotent — if `target` already exists with a
        non-zero size the download is skipped.

        Args:
            job_id: CDS job identifier (e.g. as returned by
                :meth:`list_recent_jobs`).
            target: Destination path. Parents are created.
            chunk_size: Streaming chunk size in bytes. Defaults to
                1 MiB.

        Returns:
            pathlib.Path: `target`, after the download completes.

        Raises:
            requests.HTTPError: If the job does not exist or its
                result has expired.
            ValueError: If the job's results record contains no
                downloadable asset href.
        """
        import urllib.request

        import requests

        cfg = _read_cdsapirc()
        target_path = Path(target)
        if target_path.exists() and target_path.stat().st_size > 0:
            return target_path
        rurl = cfg["url"].rstrip("/") + f"/retrieve/v1/jobs/{job_id}/results"
        resp = requests.get(rurl, headers={"PRIVATE-TOKEN": cfg["key"]}, timeout=30)
        resp.raise_for_status()
        href = resp.json().get("asset", {}).get("value", {}).get("href")
        if not href:
            raise ValueError(
                f"job {job_id!r} has no downloadable asset href in its "
                "results record"
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        # `href` comes from CDS server JSON; reject anything that
        # is not http(s) so a malicious / corrupted response can't
        # coerce us into reading a local file via `file://`.
        if not href.startswith(("https://", "http://")):
            raise ValueError(f"refusing to download from non-http(s) href: {href!r}")
        with (
            # Scheme validated above — bandit B310 does not apply.
            urllib.request.urlopen(href, timeout=60) as src,  # nosec B310
            open(target_path, "wb") as out,
        ):
            while chunk := src.read(chunk_size):
                out.write(chunk)
        return target_path
