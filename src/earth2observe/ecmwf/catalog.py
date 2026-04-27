"""Variable-catalog loader for the CDS-backed ECMWF data source.

Hosts :class:`Catalog`, the pydantic-backed reader for
``cds_data_catalog.yaml``. Split out of :mod:`earth2observe.ecmwf.backend`
so the request / download machinery and the catalog file-IO live in
separate modules.

The YAML's three top-level sections each map to a typed field on
:class:`Catalog`:

* ``available_datasets`` (informational list of CDS dataset names)
  → :attr:`Catalog.available_datasets`
* ``datasets`` (structural map of CDS datasets, each carrying a
  monthly variant and a per-variable map) → :attr:`Catalog.datasets`,
  with each value a :class:`Dataset`
* the flattened per-variable view → :attr:`Catalog.catalog`, kept
  as a convenience for the ``catalog.get_dataset(code)`` lookup
  pattern that pre-dates the structural view

The flat and structural views share the same :class:`Variable`
instances (one allocation per row, two references). The path to the
bundled YAML lives at :data:`CATALOG_PATH`; tests can monkey-patch
that module attribute to redirect the loader at a temporary file.

Examples:
    - Construct the catalog and reach into both views:

        ```python
        >>> from earth2observe.ecmwf import Catalog
        >>> cat = Catalog()
        >>> cat.get_dataset("2m-temperature").nc_variable
        't2m'
        >>> cat.datasets["reanalysis-era5-pressure-levels"].pressure_level
        ['1000']

        ```
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from earth2observe.base import AbstractCatalog
from earth2observe.ecmwf.backend import Variable

CATALOG_PATH: Path = Path(__file__).parent / "cds_data_catalog.yaml"


class Dataset(BaseModel):
    """One CDS dataset's section in the catalog.

    Mirrors the shape of a single ``datasets.<name>:`` block in
    ``cds_data_catalog.yaml`` — the monthly-aggregate variant of the
    dataset, the default pressure levels (for pressure-level
    datasets), and the per-variable map. Same dataset name is used
    as the parent key in :attr:`Catalog.datasets`; it is not stored
    again here.

    Attributes:
        monthly: CDS dataset short name to use when
            ``temporal_resolution == "monthly"``. ``None`` when the
            dataset has no monthly-aggregate variant.
        pressure_level: Default list of pressure levels (as strings,
            e.g. ``["1000"]``) for pressure-level datasets. ``None``
            for single-level datasets. Propagated to each variable's
            ``cds_pressure_level`` at load time.
        extras: Default extra CDS request parameters propagated into
            each child :class:`Variable`'s ``extras`` map. Per-row
            ``extras:`` overrides win over these defaults. Carries
            the family-wide selectors (e.g. ``domain``, ``leadtime_hour``,
            ``experiment``, ``model``) that the dataset's request shape
            requires beyond the ERA5 standard set.
        variables: Per-variable map keyed by the slugified short code
            (e.g. ``"2m-temperature"``).

    Examples:
        - Inspect a single-level dataset entry:

            ```python
            >>> from earth2observe.ecmwf import Catalog
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
            >>> from earth2observe.ecmwf import Catalog
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
    extras: dict[str, Any] = Field(default_factory=dict)
    variables: dict[str, Variable] = Field(default_factory=dict)


class Catalog(AbstractCatalog):
    """Variable catalog for the CDS-backed ECMWF data source.

    Reads ``cds_data_catalog.yaml`` (shipped as package data) and
    exposes its three top-level sections as typed pydantic fields.
    Instantiate with no arguments (``Catalog()``) — :func:`model_post_init`
    parses the YAML and populates every field in one pass.

    Attributes:
        available_datasets: Informational list of every CDS dataset
            short name. Mirrors the ``available_datasets:`` block in
            the YAML; runtime code does not consume it.
        datasets: Structural map keyed by CDS dataset short name. Each
            value is a :class:`Dataset` carrying that dataset's
            monthly-aggregate variant and its per-variable map. Use
            this when you want to iterate variables grouped by
            dataset.
        catalog: Flat map from a variable's short code (e.g.
            ``"2m-temperature"``) to its :class:`Variable`. The same
            objects appear under :attr:`datasets`. Provided as a
            convenience so existing call sites (``get_dataset(code)``)
            keep working without a two-level lookup.

    Examples:
        - Look up a single variable by short code (flat):

            ```python
            >>> from earth2observe.ecmwf import Catalog
            >>> spec = Catalog().get_dataset("2m-temperature")
            >>> spec.cds_dataset
            'reanalysis-era5-single-levels'
            >>> spec.nc_variable
            't2m'

            ```
        - Iterate variables grouped by dataset (structural):

            ```python
            >>> from earth2observe.ecmwf import Catalog
            >>> cat = Catalog()
            >>> cat.datasets["reanalysis-era5-pressure-levels"].monthly
            'reanalysis-era5-pressure-levels-monthly-means'
            >>> sorted(cat.datasets["reanalysis-era5-pressure-levels"].variables)[:3]
            ['divergence', 'fraction-of-cloud-cover', 'geopotential']

            ```
        - Inspect what CDS hosts overall:

            ```python
            >>> from earth2observe.ecmwf import Catalog
            >>> len(Catalog().available_datasets)
            134

            ```
    """

    available_datasets: list[str] = Field(default_factory=list)
    datasets: dict[str, Dataset] = Field(default_factory=dict)
    catalog: dict[str, Variable] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Parse ``cds_data_catalog.yaml`` into the three exposed fields.

        Overrides :func:`AbstractCatalog.model_post_init` to do all
        three parses in one pass instead of going through
        :meth:`get_catalog`. The flat :attr:`catalog` is built from
        the same :class:`Variable` instances that populate
        :attr:`datasets` so the two views stay consistent.

        Raises:
            ValueError: If the YAML is missing or has an empty
                ``datasets:`` block, or if no variables appear under
                any dataset.
        """
        catalog_path = CATALOG_PATH
        with open(catalog_path, "r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        datasets_yaml = data.get("datasets")
        if not datasets_yaml:
            raise ValueError(
                f"{catalog_path} is missing or has an empty "
                "'datasets' key. The catalog must contain at least "
                "one dataset with one variable. See the schema header "
                "at the top of the file."
            )

        structural: dict[str, Dataset] = {}
        flat: dict[str, Variable] = {}
        for ds_name, ds_body in datasets_yaml.items():
            monthly = ds_body.get("monthly")
            pressure_level = ds_body.get("pressure_level")
            ds_extras = dict(ds_body.get("extras") or {})
            ds_vars: dict[str, Variable] = {}
            for code, entry in (ds_body.get("variables") or {}).items():
                merged = dict(entry)
                merged["cds_dataset"] = ds_name
                if monthly is not None:
                    merged["cds_dataset_monthly"] = monthly
                # Default cds_variable to the slug-with-underscores form
                # of the YAML key (e.g. "2m-temperature" -> "2m_temperature").
                # A per-variable row may set ``cds_variable`` explicitly
                # to override this when the request name does not match.
                merged.setdefault("cds_variable", code.replace("-", "_"))
                # Per-variable override wins; otherwise inherit the
                # dataset-level default. Only single-level datasets
                # leave both unset.
                if "cds_pressure_level" not in merged and pressure_level is not None:
                    merged["cds_pressure_level"] = pressure_level
                # Merge parent-level extras under per-row overrides:
                # row-level keys win on collision so a variable can
                # diverge from the family defaults (e.g. one CARRA row
                # carrying a different leadtime than the rest).
                row_extras = dict(merged.get("extras") or {})
                merged["extras"] = {**ds_extras, **row_extras}
                var = Variable.from_dict(code, merged)
                ds_vars[code] = var
                flat[code] = var
            structural[ds_name] = Dataset(
                monthly=monthly,
                pressure_level=pressure_level,
                extras=ds_extras,
                variables=ds_vars,
            )

        if not flat:
            raise ValueError(
                f"{catalog_path} has no variables under any dataset. "
                "The catalog must contain at least one variable. "
                "See the schema header at the top of the file."
            )

        self.available_datasets = list(data.get("available_datasets") or [])
        self.datasets = structural
        self.catalog = flat

    def get_catalog(self):
        """Return the flat per-variable map populated by :func:`model_post_init`.

        Satisfies the abstract base's contract; the actual parsing is
        done in :func:`model_post_init` so all three fields can be
        built in one pass.

        Returns:
            dict[str, Variable]: One entry per variable across every
            dataset in the catalog. Same object as :attr:`catalog`.

        Examples:
            - Inspect the count and a sample of the loaded catalog:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> mapping = Catalog().get_catalog()
                >>> "2m-temperature" in mapping
                True
                >>> mapping["2m-temperature"].nc_variable
                't2m'

                ```
        """
        return self.catalog

    def get_dataset(self, var_name):
        """Return the :class:`Variable` for a short variable code.

        Args:
            var_name: Short variable code as it appears as a YAML key
                (e.g. ``"2m-temperature"`` or ``"total-precipitation"``).

        Returns:
            Variable: Per-variable metadata loaded from
            ``cds_data_catalog.yaml``.

        Raises:
            KeyError: If ``var_name`` is not in the catalog.

        Examples:
            - Look up a single-level ERA5 variable and read its CDS
              dataset and NetCDF short name:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> spec = Catalog().get_dataset("2m-temperature")
                >>> spec.cds_dataset
                'reanalysis-era5-single-levels'
                >>> spec.nc_variable, spec.units
                ('t2m', 'K')

                ```
            - Pressure-level variables expose ``cds_pressure_level``:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> spec = Catalog().get_dataset("temperature")
                >>> spec.cds_pressure_level
                ['1000']

                ```
            - Unknown codes raise ``KeyError``:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> Catalog().get_dataset("not-a-real-variable")
                Traceback (most recent call last):
                    ...
                KeyError: 'not-a-real-variable'

                ```
        """
        return self.catalog[var_name]

    def get_variable(self, var_name):
        """Alias for :meth:`get_dataset` satisfying the abstract base.

        :class:`AbstractCatalog` declares ``get_variable``; the legacy
        ECMWF call sites use ``get_dataset``. Both names return the
        same metadata so either path works.

        Args:
            var_name: Short user-friendly variable code.

        Returns:
            Variable: Per-variable metadata. Same object
            :meth:`get_dataset` returns.

        Examples:
            - The two methods return identical objects:

                ```python
                >>> from earth2observe.ecmwf import Catalog
                >>> cat = Catalog()
                >>> cat.get_variable("2m-temperature") is cat.get_dataset("2m-temperature")
                True

                ```
        """
        return self.get_dataset(var_name)
