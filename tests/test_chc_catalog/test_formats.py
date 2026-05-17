"""Lock-in for the over-claimed-formats invariant after the C4 trim."""

from __future__ import annotations

import pytest

from earthlens.chc import Catalog

pytestmark = [pytest.mark.chc]


_KNOWN_FORMATS: frozenset[str] = frozenset(
    {"tif", "cog", "netcdf", "bil", "bin", "png"}
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    """Bundled catalog, loaded once per module via the disk-mtime cache."""
    return Catalog()


class TestFormats:
    """Contract: `formats` only advertises what `ftp_bases` can reach."""

    def test_no_dataset_over_claims_formats(self, catalog: Catalog):
        """For every dataset, set(formats) is a subset of set(ftp_bases) keys."""
        offenders = [
            (key, sorted(set(ds.formats) - set(ds.ftp_bases)))
            for key, ds in catalog.datasets.items()
            if set(ds.formats) - set(ds.ftp_bases)
        ]
        assert not offenders, (
            f"datasets advertising formats with no matching ftp_bases entry "
            f"(C4 regression): {offenders}"
        )

    def test_default_format_is_listed_in_formats(self, catalog: Catalog):
        """`Dataset.default_format` (first ftp_bases key) must appear in `formats`."""
        offenders = [
            (key, ds.default_format, ds.formats)
            for key, ds in catalog.datasets.items()
            if ds.default_format not in ds.formats
        ]
        assert not offenders, (
            f"datasets whose default ftp_bases format is missing from formats: "
            f"{offenders}"
        )

    def test_every_format_string_is_in_the_known_vocabulary(self, catalog: Catalog):
        """No typos: every format value comes from the small curated set."""
        unknown: list[tuple[str, str]] = []
        for key, ds in catalog.datasets.items():
            for fmt in ds.formats:
                if not isinstance(fmt, str) or fmt not in _KNOWN_FORMATS:
                    unknown.append((key, fmt))
        assert not unknown, (
            f"unknown format strings found (typo? new format?): {unknown}. "
            f"Expand _KNOWN_FORMATS in test_formats.py if a new format is "
            f"legitimately added."
        )
