"""Variable-catalog loader for the CDS-backed ECMWF data source.

Hosts :class:`Catalog`, the pydantic-backed reader for
`cds_data_catalog.yaml`. Split out of :mod:`earthly.ecmwf.backend`
so the request / download machinery and the catalog file-IO live in
separate modules.

The YAML's three top-level sections each map to a typed field on
:class:`Catalog`:

* `available_datasets` (informational list of CDS dataset names)
  → :attr:`Catalog.available_datasets`
* `datasets` (structural map of CDS datasets, each carrying a
  monthly variant and a per-variable map) → :attr:`Catalog.datasets`,
  with each value a :class:`Dataset`
* the flattened per-variable view → :attr:`Catalog.catalog`, kept
  as a convenience for the `cat.get_variable(code)` lookup pattern.
  When the same short code appears in more than one dataset (e.g.
  `"2m-temperature"` lives in both `reanalysis-era5-single-levels`
  and `reanalysis-era5-land`), the first dataset to declare it in
  YAML order wins the flat slot; callers needing a different
  dataset must pass `dataset=` to :meth:`Catalog.get_variable`.

The flat and structural views share the same :class:`Variable`
instances (one allocation per row, two references). The path to the
bundled YAML lives at :data:`CATALOG_PATH`; tests can monkey-patch
that module attribute to redirect the loader at a temporary file.

Examples:
    - Construct the catalog and reach into both views:

        ```python
        >>> from earthly.ecmwf import Catalog
        >>> cat = Catalog()
        >>> cat.get_variable("2m-temperature").nc_variable
        't2m'
        >>> cat.get_dataset("reanalysis-era5-pressure-levels").pressure_level
        ['1000']

        ```
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from earthly.base import AbstractCatalog
from earthly.ecmwf.backend import Variable

CATALOG_PATH: Path = Path(__file__).parent / "cds_data_catalog.yaml"


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
    extras: dict[str, Any] = Field(default_factory=dict)
    request_kind: str = "form"
    variables: dict[str, Variable] = Field(default_factory=dict)


class Catalog(AbstractCatalog):
    """Variable catalog for the CDS-backed ECMWF data source.

    Reads `cds_data_catalog.yaml` (shipped as package data) and
    exposes its three top-level sections as typed pydantic fields.
    Instantiate with no arguments (`Catalog()`) — :func:`model_post_init`
    parses the YAML and populates every field in one pass.

    Attributes:
        available_datasets: Informational list of every CDS dataset
            short name. Mirrors the `available_datasets:` block in
            the YAML; runtime code does not consume it.
        datasets: Structural map keyed by CDS dataset short name. Each
            value is a :class:`Dataset` carrying that dataset's
            monthly-aggregate variant and its per-variable map. Use
            this when you want to iterate variables grouped by
            dataset.
        catalog: Flat map from a variable's short code (e.g.
            `"2m-temperature"`) to its :class:`Variable`. Populated
            with first-wins precedence: when a short code appears in
            more than one dataset, the dataset that declares it first
            in YAML order owns the flat slot. The :attr:`duplicates`
            map records every code that lost the race so callers can
            still reach the alternate definitions through
            :meth:`get_variable` with `dataset=`.
        duplicates: Audit map of short codes that appear in more than
            one dataset, listing every dataset that declares each one
            (in YAML order). Empty when the catalog has no
            collisions.

    Examples:
        - Look up a single variable by short code (flat):

            ```python
            >>> from earthly.ecmwf import Catalog
            >>> spec = Catalog().get_variable("2m-temperature")
            >>> spec.cds_dataset
            'reanalysis-era5-single-levels'
            >>> spec.nc_variable
            't2m'

            ```
        - Reach an alternate definition explicitly when a code is
          shared across datasets:

            ```python
            >>> from earthly.ecmwf import Catalog
            >>> cat = Catalog()
            >>> cat.get_variable(
            ...     "2m-temperature", dataset="reanalysis-era5-land"
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
    catalog: dict[str, Variable] = Field(default_factory=dict)
    duplicates: dict[str, list[str]] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        """Parse `cds_data_catalog.yaml` into the three exposed fields.

        Overrides :func:`AbstractCatalog.model_post_init` to do all
        three parses in one pass instead of going through
        :meth:`get_catalog`. The flat :attr:`catalog` is built from
        the same :class:`Variable` instances that populate
        :attr:`datasets` so the two views stay consistent.

        Raises:
            ValueError: If the YAML is missing or has an empty
                `datasets:` block, or if no variables appear under
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
        owner_of: dict[str, list[str]] = {}
        for ds_name, ds_body in datasets_yaml.items():
            monthly = ds_body.get("monthly")
            pressure_level = ds_body.get("pressure_level")
            ds_extras = dict(ds_body.get("extras") or {})
            ds_request_kind = ds_body.get("request_kind", "form")
            ds_vars: dict[str, Variable] = {}
            for code, entry in (ds_body.get("variables") or {}).items():
                merged = dict(entry)
                merged["cds_dataset"] = ds_name
                if monthly is not None:
                    merged["cds_dataset_monthly"] = monthly
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
                # Merge parent-level extras under per-row overrides:
                # row-level keys win on collision so a variable can
                # diverge from the family defaults (e.g. one CARRA row
                # carrying a different leadtime than the rest).
                row_extras = dict(merged.get("extras") or {})
                merged["extras"] = {**ds_extras, **row_extras}
                merged.setdefault("request_kind", ds_request_kind)
                var = Variable.from_dict(code, merged)
                ds_vars[code] = var
                # First-wins: keep the earliest dataset's Variable in
                # the flat slot so a stale duplicate cannot silently
                # change the resolved dataset for an existing caller.
                # Every dataset that declared the code is recorded in
                # `owner_of` so :attr:`duplicates` can surface the
                # collision and :meth:`get_variable` can route around
                # it via the `dataset=` argument.
                owner_of.setdefault(code, []).append(ds_name)
                if code not in flat:
                    flat[code] = var
            structural[ds_name] = Dataset(
                monthly=monthly,
                pressure_level=pressure_level,
                extras=ds_extras,
                request_kind=ds_request_kind,
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
        self.duplicates = {
            code: owners for code, owners in owner_of.items() if len(owners) > 1
        }

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
                >>> from earthly.ecmwf import Catalog
                >>> mapping = Catalog().get_catalog()
                >>> "2m-temperature" in mapping
                True
                >>> mapping["2m-temperature"].nc_variable
                't2m'

                ```
        """
        return self.catalog

    def get_variable(self, code: str, dataset: str | None = None) -> Variable:
        """Return the :class:`Variable` for a short variable code.

        Args:
            code: Short variable code as it appears as a YAML key
                (e.g. `"2m-temperature"` or `"total-precipitation"`).
            dataset: Optional CDS dataset short name to scope the
                lookup to. Required when `code` appears in more than
                one dataset (see :attr:`duplicates`); otherwise
                optional. When `None`, the first dataset that
                declared `code` in YAML order wins.

        Returns:
            Variable: Per-variable metadata loaded from
            `cds_data_catalog.yaml`.

        Raises:
            KeyError: If `code` is not in the catalog, or if
                `dataset` is provided but does not declare `code`.

        Examples:
            - Look up a single-level ERA5 variable and read its CDS
              dataset and NetCDF short name:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> spec = Catalog().get_variable("2m-temperature")
                >>> spec.cds_dataset
                'reanalysis-era5-single-levels'
                >>> spec.nc_variable, spec.units
                ('t2m', 'K')

                ```
            - Pressure-level variables expose `cds_pressure_level`:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> spec = Catalog().get_variable("temperature")
                >>> spec.cds_pressure_level
                ['1000']

                ```
            - Disambiguate a code that lives in multiple datasets:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> Catalog().get_variable(
                ...     "2m-temperature", dataset="reanalysis-era5-land"
                ... ).cds_dataset
                'reanalysis-era5-land'

                ```
            - Unknown codes raise `KeyError`:

                ```python
                >>> from earthly.ecmwf import Catalog
                >>> Catalog().get_variable("not-a-real-variable")
                Traceback (most recent call last):
                    ...
                KeyError: 'not-a-real-variable'

                ```
        """
        if dataset is not None:
            return self.datasets[dataset].variables[code]
        return self.catalog[code]

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
                now
                - datetime.datetime.fromisoformat(created.replace("Z", ""))
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
        import requests
        import urllib.request

        cfg = _read_cdsapirc()
        target_path = Path(target)
        if target_path.exists() and target_path.stat().st_size > 0:
            return target_path
        rurl = cfg["url"].rstrip("/") + f"/retrieve/v1/jobs/{job_id}/results"
        resp = requests.get(rurl, headers={"PRIVATE-TOKEN": cfg["key"]})
        resp.raise_for_status()
        href = resp.json().get("asset", {}).get("value", {}).get("href")
        if not href:
            raise ValueError(
                f"job {job_id!r} has no downloadable asset href in its "
                "results record"
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(href, timeout=60) as src, open(
            target_path, "wb"
        ) as out:
            while chunk := src.read(chunk_size):
                out.write(chunk)
        return target_path

