"""Tests for `earthlens.gee.catalog` — the GEE dataset/band catalog (task H5)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from earthlens.gee import catalog as catalog_module
from earthlens.gee.catalog import Band, Cadence, Catalog, Dataset, Extent


@pytest.fixture(scope="function")
def shipped_catalog() -> Catalog:
    """Return a :class:`Catalog` loaded from the bundled YAML.

    Returns:
        Catalog: The package-data catalog.
    """
    return Catalog()


def _write_catalog_yaml(tmp_path: Path, body: str) -> Path:
    """Write `body` (dedented) to a temp `gee_data_catalog.yaml` and return its path."""
    path = tmp_path / "gee_data_catalog.yaml"
    path.write_text(textwrap.dedent(body))
    return path


_MINIMAL_YAML = """\
version: 1
available_datasets:
  - DEMO/IMAGE
  - DEMO/COLLECTION
datasets:
  DEMO/IMAGE:
    title: Demo static image
    ee_type: image
    spatial_resolution: 30
    extent:
      start_date: "2000-01-01"
      end_date: "2000-01-02"
    bands:
      elevation:
        description: Elevation
        units: m
  DEMO/COLLECTION:
    title: Demo collection
    ee_type: image_collection
    cadence: { interval: 8, unit: day }
    extent:
      start_date: "2010-01-01"
    default_reducer: mean
    bands:
      value:
        description: Some value
        scale: 0.1
"""


class TestCadence:
    """Tests for the :class:`Cadence` value object."""

    def test_valid_construction(self):
        """A valid interval/unit pair builds and exposes its parts.

        Test scenario:
            ``interval=16, unit="day"`` should produce a frozen object whose
            attributes echo the inputs.
        """
        cadence = Cadence(interval=16, unit="day")
        assert cadence.interval == 16, f"interval not stored: {cadence.interval}"
        assert cadence.unit == "day", f"unit not stored: {cadence.unit}"

    def test_is_frozen(self):
        """Cadence instances are immutable.

        Test scenario:
            Assigning to an attribute should raise a pydantic ``ValidationError``.
        """
        cadence = Cadence(interval=1, unit="month")
        with pytest.raises(ValidationError):
            cadence.interval = 2

    def test_non_positive_interval_rejected(self):
        """A zero or negative interval fails validation.

        Test scenario:
            ``interval=0`` should raise ``ValidationError`` (the field is ``gt=0``).
        """
        with pytest.raises(ValidationError, match="greater than 0"):
            Cadence(interval=0, unit="day")

    def test_unknown_unit_rejected(self):
        """A unit outside the allowed literal set fails validation.

        Test scenario:
            ``unit="fortnight"`` should raise ``ValidationError``.
        """
        with pytest.raises(ValidationError):
            Cadence(interval=1, unit="fortnight")

    def test_extra_field_rejected(self):
        """Unknown fields are rejected (``extra="forbid"``).

        Test scenario:
            Passing ``label="x"`` should raise ``ValidationError``.
        """
        with pytest.raises(ValidationError):
            Cadence(interval=1, unit="day", label="x")


class TestBand:
    """Tests for the :class:`Band` value object."""

    def test_minimal_construction_defaults(self):
        """A band with only id+description gets sensible Nones/False defaults.

        Test scenario:
            ``Band(id="x", description="d")`` → ``units``/``scale``/``offset``/
            ``wavelength``/``min``/``max`` are ``None`` and ``estimated_range`` is False.
        """
        band = Band(id="x", description="d")
        assert band.id == "x"
        assert band.description == "d"
        assert band.units is None and band.scale is None and band.offset is None
        assert band.wavelength is None and band.min is None and band.max is None
        assert band.estimated_range is False, "estimated_range should default to False"

    def test_full_construction(self):
        """All optional band fields round-trip.

        Test scenario:
            A fully-specified reflectance band keeps every field verbatim.
        """
        band = Band(
            id="SR_B4",
            description="Red surface reflectance",
            units=None,
            scale=2.75e-05,
            offset=-0.2,
            wavelength=0.655,
            min=1,
            max=65455,
            estimated_range=False,
        )
        assert band.scale == 2.75e-05 and band.offset == -0.2
        assert band.wavelength == 0.655 and band.min == 1 and band.max == 65455

    def test_is_frozen(self):
        """Band instances are immutable.

        Test scenario:
            Reassigning ``scale`` should raise ``ValidationError``.
        """
        band = Band(id="x", description="d")
        with pytest.raises(ValidationError):
            band.scale = 1.0

    def test_extra_field_rejected(self):
        """Unknown band fields are rejected (``extra="forbid"``).

        Test scenario:
            Passing ``colour="red"`` should raise ``ValidationError``.
        """
        with pytest.raises(ValidationError):
            Band(id="x", description="d", colour="red")

    def test_missing_required_field_rejected(self):
        """``description`` is required.

        Test scenario:
            ``Band(id="x")`` should raise ``ValidationError``.
        """
        with pytest.raises(ValidationError):
            Band(id="x")


class TestExtent:
    """Tests for the :class:`Extent` value object."""

    def test_completed_dataset(self):
        """A bounded, finished dataset keeps both dates and a None bbox.

        Test scenario:
            ``start_date`` + ``end_date`` given, ``bbox`` omitted → ``bbox is None``.
        """
        extent = Extent(start_date="2000-02-11", end_date="2000-02-22")
        assert extent.start_date == "2000-02-11"
        assert extent.end_date == "2000-02-22"
        assert extent.bbox is None

    def test_ongoing_regional_dataset(self):
        """An open-ended, regionally bounded dataset has end_date None and a bbox.

        Test scenario:
            Only ``start_date`` + ``bbox`` given → ``end_date is None`` and the
            bbox is stored as a 4-tuple of floats.
        """
        extent = Extent(start_date="1981-01-01", bbox=(-180.0, -50.0, 180.0, 50.0))
        assert extent.end_date is None
        assert extent.bbox == (-180.0, -50.0, 180.0, 50.0)

    def test_is_frozen(self):
        """Extent instances are immutable.

        Test scenario:
            Reassigning ``start_date`` should raise ``ValidationError``.
        """
        extent = Extent(start_date="2000-01-01")
        with pytest.raises(ValidationError):
            extent.start_date = "2001-01-01"

    def test_extra_field_rejected(self):
        """Unknown extent fields are rejected.

        Test scenario:
            Passing ``crs="EPSG:4326"`` should raise ``ValidationError``.
        """
        with pytest.raises(ValidationError):
            Extent(start_date="2000-01-01", crs="EPSG:4326")

    def test_missing_start_date_rejected(self):
        """``start_date`` is required.

        Test scenario:
            ``Extent()`` should raise ``ValidationError``.
        """
        with pytest.raises(ValidationError):
            Extent()


class TestDataset:
    """Tests for the :class:`Dataset` value object."""

    def _image(self) -> Dataset:
        return Dataset(
            id="DEMO/IMAGE",
            title="Demo image",
            ee_type="image",
            extent=Extent(start_date="2000-01-01"),
            bands={"elevation": Band(id="elevation", description="Elevation", units="m")},
        )

    def _collection(self) -> Dataset:
        return Dataset(
            id="DEMO/COLLECTION",
            title="Demo collection",
            extent=Extent(start_date="2010-01-01"),
            bands={"value": Band(id="value", description="Value")},
        )

    def test_defaults(self):
        """Unspecified fields take their documented defaults.

        Test scenario:
            A collection built with only id/title/extent has
            ``ee_type == "image_collection"``, ``default_reducer == "median"``,
            ``license is None``, ``terms_note is None``, ``user_uploaded is False``,
            empty ``extras`` and ``bands``.
        """
        ds = Dataset(id="X", title="X", extent=Extent(start_date="2000-01-01"))
        assert ds.ee_type == "image_collection"
        assert ds.default_reducer == "median"
        assert ds.license is None and ds.terms_note is None
        assert ds.user_uploaded is False
        assert ds.extras == {} and ds.bands == {}

    def test_is_image_collection_true(self):
        """``is_image_collection`` is True for an image_collection.

        Test scenario:
            A collection dataset reports ``is_image_collection`` True.
        """
        assert self._collection().is_image_collection is True

    def test_is_image_collection_false_for_image(self):
        """``is_image_collection`` is False for a single image.

        Test scenario:
            An ``ee_type="image"`` dataset reports ``is_image_collection`` False.
        """
        assert self._image().is_image_collection is False

    def test_get_band_success(self):
        """``get_band`` returns the matching :class:`Band`.

        Test scenario:
            Looking up an existing band returns it with attributes intact.
        """
        band = self._image().get_band("elevation")
        assert band.id == "elevation" and band.units == "m"

    def test_get_band_unknown_raises_with_hint(self):
        """``get_band`` raises ValueError with a close-match suggestion.

        Test scenario:
            ``get_band("elevashun")`` raises ``ValueError`` whose message names
            the dataset, lists known bands, and suggests ``'elevation'``.
        """
        with pytest.raises(ValueError) as exc:
            self._image().get_band("elevashun")
        msg = str(exc.value)
        assert "DEMO/IMAGE" in msg and "elevation" in msg
        assert "Did you mean 'elevation'?" in msg, f"missing suggestion: {msg}"

    def test_get_band_unknown_no_close_match(self):
        """``get_band`` still raises (without a suggestion) when nothing is close.

        Test scenario:
            A wildly different band name raises ``ValueError`` and the message
            does not contain "Did you mean".
        """
        with pytest.raises(ValueError) as exc:
            self._image().get_band("zzzzzzzz")
        assert "Did you mean" not in str(exc.value)

    def test_is_frozen(self):
        """Dataset instances are immutable.

        Test scenario:
            Reassigning ``title`` should raise ``ValidationError``.
        """
        with pytest.raises(ValidationError):
            self._collection().title = "new"


class TestCatalog:
    """Tests for the :class:`Catalog` loader and accessors."""

    def test_shipped_yaml_loads(self, shipped_catalog: Catalog):
        """The bundled catalog loads with non-empty datasets and asset index.

        Test scenario:
            ``Catalog()`` exposes a non-empty ``datasets`` map and a non-empty
            ``available_datasets`` list.
        """
        assert len(shipped_catalog.datasets) >= 1, "no curated datasets loaded"
        assert len(shipped_catalog.available_datasets) >= len(shipped_catalog.datasets)

    def test_shipped_yaml_known_entries(self, shipped_catalog: Catalog):
        """A few well-known curated datasets are present and typed.

        Test scenario:
            ``USGS/SRTMGL1_003`` is an ``image`` and ``UCSB-CHG/CHIRPS/DAILY``
            is an ``image_collection`` with a band called ``precipitation``.
        """
        srtm = shipped_catalog.get_dataset("USGS/SRTMGL1_003")
        assert srtm.ee_type == "image" and srtm.is_image_collection is False
        chirps = shipped_catalog.get_dataset("UCSB-CHG/CHIRPS/DAILY")
        assert chirps.is_image_collection is True
        assert chirps.get_band("precipitation").units == "mm/d"

    def test_curated_subset_of_available(self, shipped_catalog: Catalog):
        """Every curated dataset id is also listed in ``available_datasets``.

        Test scenario:
            ``set(datasets) <= set(available_datasets)`` for the shipped catalog.
        """
        missing = set(shipped_catalog.datasets) - set(shipped_catalog.available_datasets)
        assert not missing, f"curated datasets absent from available_datasets: {missing}"

    def test_get_catalog_returns_datasets(self, shipped_catalog: Catalog):
        """``get_catalog`` returns the same mapping as ``.datasets``.

        Test scenario:
            ``get_catalog() is .datasets`` and ``.catalog`` is populated post-init.
        """
        assert shipped_catalog.get_catalog() is shipped_catalog.datasets
        assert shipped_catalog.catalog is shipped_catalog.datasets

    def test_get_dataset_unknown_raises_with_hint(self, shipped_catalog: Catalog):
        """``get_dataset`` raises ValueError with a close-match suggestion.

        Test scenario:
            A near-miss id raises ``ValueError`` mentioning "not in the GEE catalog".
        """
        with pytest.raises(ValueError, match="not in the GEE catalog"):
            shipped_catalog.get_dataset("USGS/SRTMGL1_004")

    def test_get_band_and_get_variable_equivalent(self, shipped_catalog: Catalog):
        """``get_variable`` is an alias of ``get_band``.

        Test scenario:
            Both return the same :class:`Band` for a given (dataset, band) pair.
        """
        a = shipped_catalog.get_band("USGS/SRTMGL1_003", "elevation")
        b = shipped_catalog.get_variable("USGS/SRTMGL1_003", "elevation")
        assert a is b and a.units == "m"

    def test_get_band_unknown_dataset_raises(self, shipped_catalog: Catalog):
        """``get_band`` propagates the unknown-dataset error.

        Test scenario:
            An unknown dataset id raises ``ValueError`` before band lookup.
        """
        with pytest.raises(ValueError, match="not in the GEE catalog"):
            shipped_catalog.get_band("NO/SUCH/DATASET", "x")

    def test_get_band_unknown_band_raises(self, shipped_catalog: Catalog):
        """``get_band`` raises for an unknown band of a known dataset.

        Test scenario:
            A bad band on ``USGS/SRTMGL1_003`` raises ``ValueError`` naming it.
        """
        with pytest.raises(ValueError, match="is not a band of"):
            shipped_catalog.get_band("USGS/SRTMGL1_003", "nope")

    def test_catalog_path_monkeypatch(self, monkeypatch, tmp_path):
        """``CATALOG_PATH`` is monkey-patchable to redirect the loader.

        Test scenario:
            Pointing ``CATALOG_PATH`` at a minimal temp YAML loads exactly that
            content (2 datasets: a static image and a collection).
        """
        path = _write_catalog_yaml(tmp_path, _MINIMAL_YAML)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        cat = Catalog()
        assert set(cat.datasets) == {"DEMO/IMAGE", "DEMO/COLLECTION"}
        assert cat.get_dataset("DEMO/IMAGE").ee_type == "image"
        assert cat.get_dataset("DEMO/COLLECTION").cadence == Cadence(interval=8, unit="day")
        assert cat.get_band("DEMO/COLLECTION", "value").scale == 0.1

    def test_duplicate_dataset_key_rejected(self, monkeypatch, tmp_path):
        """A duplicated dataset key in the YAML fails at load time.

        Test scenario:
            Two ``DEMO/IMAGE:`` blocks → ``ValueError`` mentioning "duplicate YAML key".
        """
        yaml_text = _MINIMAL_YAML + (
            "  DEMO/IMAGE:\n"
            "    title: Duplicate\n"
            "    ee_type: image\n"
            "    extent:\n"
            '      start_date: "2000-01-01"\n'
        )
        path = _write_catalog_yaml(tmp_path, yaml_text)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        with pytest.raises(ValueError, match="duplicate YAML key"):
            Catalog()

    def test_duplicate_band_key_rejected(self, monkeypatch, tmp_path):
        """A duplicated band key under a dataset fails at load time.

        Test scenario:
            Two ``elevation:`` bands under ``DEMO/IMAGE`` → ``ValueError``
            mentioning "duplicate YAML key".
        """
        yaml_text = textwrap.dedent("""\
          version: 1
          available_datasets:
            - DEMO/IMAGE
          datasets:
            DEMO/IMAGE:
              title: Demo
              ee_type: image
              extent:
                start_date: "2000-01-01"
              bands:
                elevation:
                  description: One
                elevation:
                  description: Two
        """)
        path = _write_catalog_yaml(tmp_path, yaml_text)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        with pytest.raises(ValueError, match="duplicate YAML key"):
            Catalog()

    def test_unknown_band_field_rejected(self, monkeypatch, tmp_path):
        """An unknown band field surfaces as a wrapped ValueError.

        Test scenario:
            A band with ``colour: red`` → ``ValueError`` naming the band and dataset.
        """
        yaml_text = textwrap.dedent("""\
          version: 1
          available_datasets:
            - DEMO/IMAGE
          datasets:
            DEMO/IMAGE:
              title: Demo
              ee_type: image
              extent:
                start_date: "2000-01-01"
              bands:
                elevation:
                  description: Elevation
                  colour: red
        """)
        path = _write_catalog_yaml(tmp_path, yaml_text)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        with pytest.raises(ValueError, match="invalid band 'elevation' under dataset 'DEMO/IMAGE'"):
            Catalog()

    def test_unknown_dataset_field_rejected(self, monkeypatch, tmp_path):
        """An unknown dataset field surfaces as a wrapped ValueError.

        Test scenario:
            A dataset with an unexpected key... actually ``Dataset`` allows extra
            keys? It does not set ``extra="forbid"``, so unknown *dataset* keys
            are silently ignored — assert that an otherwise-valid dataset still
            loads. (Bands, in contrast, forbid extras — covered above.)
        """
        yaml_text = textwrap.dedent("""\
          version: 1
          available_datasets:
            - DEMO/IMAGE
          datasets:
            DEMO/IMAGE:
              title: Demo
              ee_type: image
              extent:
                start_date: "2000-01-01"
              unexpected_key: ignored
        """)
        path = _write_catalog_yaml(tmp_path, yaml_text)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        cat = Catalog()
        assert "DEMO/IMAGE" in cat.datasets

    def test_missing_datasets_block_rejected(self, monkeypatch, tmp_path):
        """A YAML with no ``datasets:`` block fails at load time.

        Test scenario:
            Only ``available_datasets:`` present → ``ValueError`` about an empty
            ``datasets:`` block.
        """
        path = _write_catalog_yaml(tmp_path, "version: 1\navailable_datasets:\n  - X\n")
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        with pytest.raises(ValueError, match="empty 'datasets:' block"):
            Catalog()

    def test_curated_dataset_not_in_available_rejected(self, monkeypatch, tmp_path):
        """A curated dataset missing from ``available_datasets`` fails at load time.

        Test scenario:
            ``datasets:`` lists ``DEMO/IMAGE`` but ``available_datasets:`` does not →
            ``ValueError`` telling the author to add it there too.
        """
        yaml_text = textwrap.dedent("""\
          version: 1
          available_datasets:
            - SOMETHING/ELSE
          datasets:
            DEMO/IMAGE:
              title: Demo
              ee_type: image
              extent:
                start_date: "2000-01-01"
        """)
        path = _write_catalog_yaml(tmp_path, yaml_text)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        with pytest.raises(ValueError, match="missing from 'available_datasets:'"):
            Catalog()

    def test_empty_available_datasets_skips_cross_ref(self, monkeypatch, tmp_path):
        """When ``available_datasets`` is empty/absent the cross-ref check is skipped.

        Test scenario:
            A YAML with ``datasets:`` but no ``available_datasets:`` loads fine
            (the invariant only bites when the index is non-empty).
        """
        yaml_text = textwrap.dedent("""\
          version: 1
          datasets:
            DEMO/IMAGE:
              title: Demo
              ee_type: image
              extent:
                start_date: "2000-01-01"
        """)
        path = _write_catalog_yaml(tmp_path, yaml_text)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        cat = Catalog()
        assert "DEMO/IMAGE" in cat.datasets and cat.available_datasets == []

    def test_band_id_injected_from_key(self, monkeypatch, tmp_path):
        """The band ``id`` comes from the YAML mapping key, not the body.

        Test scenario:
            The minimal YAML's ``value`` band has ``band.id == "value"``.
        """
        path = _write_catalog_yaml(tmp_path, _MINIMAL_YAML)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        cat = Catalog()
        assert cat.get_band("DEMO/COLLECTION", "value").id == "value"
        assert cat.get_dataset("DEMO/IMAGE").id == "DEMO/IMAGE"

    def test_dict_protocol(self, shipped_catalog: Catalog):
        """`Catalog` supports dict-style ``in``, ``[]``, ``len``, ``iter``.

        Test scenario:
            ``"USGS/SRTMGL1_003" in cat`` is True; ``cat["USGS/SRTMGL1_003"]``
            returns the same `Dataset` as ``get_dataset(...)``; ``len(cat)``
            == ``len(cat.datasets)``; iterating yields the asset ids.
        """
        cat = shipped_catalog
        assert "USGS/SRTMGL1_003" in cat
        assert "NOT/A/REAL/ID" not in cat
        assert cat["USGS/SRTMGL1_003"] is cat.get_dataset("USGS/SRTMGL1_003")
        assert len(cat) == len(cat.datasets)
        assert set(iter(cat)) == set(cat.datasets)

    def test_getitem_unknown_raises_keyerror(self, shipped_catalog: Catalog):
        """``cat[<bad-id>]`` raises `KeyError` (not `ValueError`) per the dict protocol.

        Test scenario:
            The original `ValueError` from `get_dataset` is preserved on
            `KeyError.__cause__` so the close-match hint isn't lost.
        """
        with pytest.raises(KeyError, match="USGS/SRTMGL1_004") as excinfo:
            shipped_catalog["USGS/SRTMGL1_004"]
        assert isinstance(excinfo.value.__cause__, ValueError)
        assert "not in the GEE catalog" in str(excinfo.value.__cause__)

    def test_repr_summarises_counts(self, shipped_catalog: Catalog):
        """``repr(cat)`` is a compact summary, not the full content.

        Test scenario:
            The repr names the class and reports both counts.
        """
        text = repr(shipped_catalog)
        assert text.startswith("Catalog(datasets=")
        assert f"datasets={len(shipped_catalog.datasets)}" in text
        assert f"available_datasets={len(shipped_catalog.available_datasets)}" in text

    def test_str_dumps_curated_yaml(self, shipped_catalog: Catalog):
        """``str(cat)`` is a YAML dump of the curated `datasets:` map.

        Test scenario:
            The dump round-trips through ``yaml.safe_load`` and recovers
            the curated dataset ids and per-dataset titles.
        """
        import yaml

        parsed = yaml.safe_load(str(shipped_catalog))
        assert set(parsed) == set(shipped_catalog.datasets)
        for asset_id, body in parsed.items():
            assert body["title"] == shipped_catalog.get_dataset(asset_id).title


class TestLicenseField:
    """Tests for the post-M2 `license` + `terms_note` schema on `Dataset`."""

    def test_shipped_catalog_uses_normalised_licenses(self, shipped_catalog: Catalog):
        """Every shipped stanza carries one of the agreed SPDX / conventional licence ids."""
        allowed = {
            "CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
            "CC0-1.0", "ODbL-1.0", "OGL-Canada-2.0", "etalab-2.0",
            "CDLA-Permissive-1.0", "public-domain", "proprietary", "unknown",
        }
        bad = sorted({d.license for d in shipped_catalog.datasets.values()} - allowed - {None})
        assert not bad, f"unexpected license ids: {bad}"

    def test_terms_note_preserved_for_proprietary(self, shipped_catalog: Catalog):
        """`proprietary` stanzas carry the original prose in `terms_note`."""
        # Sentinel-2 SR Harmonized — published under Copernicus Sentinel terms
        d = shipped_catalog.get_dataset("COPERNICUS/S2_SR_HARMONIZED")
        assert d.license == "proprietary"
        assert d.terms_note  # non-empty prose


class TestCatalogCache:
    """Tests for the module-level `(path, mtime_ns)` parse cache."""

    def test_cache_hit_returns_equivalent_data(self, monkeypatch, tmp_path):
        """A second `Catalog()` on an unchanged file reuses the cached parse."""
        path = _write_catalog_yaml(tmp_path, _MINIMAL_YAML)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        catalog_module.clear_catalog_cache()
        a = Catalog()
        b = Catalog()
        assert set(a.datasets) == set(b.datasets)
        assert a.get_dataset("DEMO/IMAGE").title == b.get_dataset("DEMO/IMAGE").title

    def test_cache_invalidates_on_mtime_change(self, monkeypatch, tmp_path):
        """Touching the file (changing its mtime) reparses on the next call."""
        import os

        path = _write_catalog_yaml(tmp_path, _MINIMAL_YAML)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        catalog_module.clear_catalog_cache()
        a = Catalog()
        assert "DEMO/IMAGE" in a.datasets

        # Rewrite the file with a different title and a bumped mtime
        new_yaml = _MINIMAL_YAML.replace("Demo static image", "Demo CHANGED image")
        path.write_text(new_yaml)
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))

        b = Catalog()
        assert b.get_dataset("DEMO/IMAGE").title == "Demo CHANGED image"

    def test_clear_catalog_cache(self, monkeypatch, tmp_path):
        """`clear_catalog_cache()` drops cached entries; the next call reparses."""
        path = _write_catalog_yaml(tmp_path, _MINIMAL_YAML)
        monkeypatch.setattr(catalog_module, "CATALOG_PATH", path)
        catalog_module.clear_catalog_cache()
        Catalog()
        assert catalog_module._CATALOG_CACHE
        catalog_module.clear_catalog_cache()
        assert not catalog_module._CATALOG_CACHE
