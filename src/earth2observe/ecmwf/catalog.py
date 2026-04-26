"""Variable-catalog loader for the CDS-backed ECMWF data source.

Hosts :class:`Catalog`, the pydantic-backed reader for
``cds_data_catalog.yaml``. Split out of :mod:`earth2observe.ecmwf.backend`
so the request / download machinery and the catalog file-IO live in
separate modules.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from earth2observe.base import AbstractCatalog
from earth2observe.ecmwf.backend import Variable

CATALOG_PATH: Path = Path(__file__).parent / "cds_data_catalog.yaml"


class Catalog(AbstractCatalog):
    """Variable catalog for the CDS-backed ECMWF data source.

    Reads ``cds_data_catalog.yaml`` (shipped as package data) and exposes
    the per-variable metadata that :class:`ECMWF` consumes when building
    a CDS retrieve request. Inherits pydantic ``BaseModel`` semantics
    via :class:`AbstractCatalog`: instantiate with no arguments
    (``Catalog()``) and the post-init hook populates ``catalog`` with
    the parsed YAML contents.

    Attributes:
        catalog: Mapping from a user-friendly variable code (e.g.
            ``"2T"``) to a typed :class:`Variable` instance. Set
            by :func:`AbstractCatalog.model_post_init` from the YAML
            shipped at ``CATALOG_PATH``.

    Examples:
        - Look up a single-level ERA5 variable:

            ```python
            >>> from earth2observe.ecmwf import Catalog
            >>> spec = Catalog().get_dataset("2T")
            >>> spec.cds_dataset
            'reanalysis-era5-single-levels'
            >>> spec.cds_variable
            '2m_temperature'
            >>> spec.nc_variable
            't2m'

            ```
        - Pressure-level variables include a ``cds_pressure_level``
          attribute that ``ECMWF.api`` forwards to CDS:

            ```python
            >>> from earth2observe.ecmwf import Catalog
            >>> spec = Catalog().get_dataset("T")
            >>> spec.cds_dataset
            'reanalysis-era5-pressure-levels'
            >>> spec.cds_pressure_level
            ['1000']

            ```
    """

    catalog: dict[str, Variable] = Field(default_factory=dict)

    def get_catalog(self):
        """Read ``cds_data_catalog.yaml`` and return the per-variable map.

        Returns:
            dict: The non-empty per-variable map loaded from the
            YAML file's top-level ``variables`` key.

        Raises:
            ValueError: If the file is missing the ``variables`` key,
                or it is present but empty / null. Pre-fix, this
                returned ``{}`` silently and every subsequent
                ``get_dataset(code)`` call raised ``KeyError`` —
                misleading the user about which file is broken.
        """
        catalog_path = CATALOG_PATH
        with open(catalog_path, "r", encoding="utf-8") as stream:
            data = yaml.safe_load(stream) or {}
        variables = data.get("variables")
        if not variables:
            raise ValueError(
                f"{catalog_path} is missing or has an empty "
                "'variables' key. The catalog must contain at least "
                "one variable definition. See the schema header at "
                "the top of the file."
            )
        return {
            code: Variable.from_dict(code, entry)
            for code, entry in variables.items()
        }

    def get_dataset(self, var_name):
        """Return the metadata dict for ``var_name``.

        Args:
            var_name: Short user-friendly variable code (e.g. ``"2T"``).

        Returns:
            Variable: Per-variable metadata loaded from
            ``cds_data_catalog.yaml``.

        Raises:
            KeyError: If ``var_name`` is not in the catalog.
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
            Variable: Per-variable metadata. See :meth:`get_dataset`.
        """
        return self.get_dataset(var_name)
