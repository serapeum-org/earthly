"""Lock-in for the H5 restored YAML comments (provisional flags + rationale)."""

from __future__ import annotations

import pytest

from earthlens.chc import Catalog
from earthlens.chc.catalog import CATALOG_PATH

pytestmark = [pytest.mark.chc]


_PROVISIONAL_LINE = (
    "Provisional pattern; verify against the FTP listing before use."
)


def _read_folded(name: str) -> str:
    """Read a catalog YAML and fold comment line-breaks so multi-line prose is searchable."""
    text = (CATALOG_PATH / name).read_text(encoding="utf-8")
    # Fold "...\n# rest-of-sentence" into "... rest-of-sentence" so a search
    # phrase that legitimately wraps across two comment lines still matches.
    return text.replace("\n# ", " ")


class TestYamlComments:
    """The H5 comment restorations are present in the per-family YAML files."""

    def test_gefs_yaml_carries_three_provisional_flags(self):
        """gefs.yaml has the provisional warning above each of the 3 v3 file_patterns."""
        text = (CATALOG_PATH / "gefs.yaml").read_text(encoding="utf-8")
        assert text.count(_PROVISIONAL_LINE) >= 3

    def test_gefs_yaml_points_at_the_probe_tool(self):
        """gefs.yaml's banner names the probe tool so a maintainer knows where to look."""
        text = (CATALOG_PATH / "gefs.yaml").read_text(encoding="utf-8")
        assert "probe_chirps_gefs.py" in text

    def test_derived_yaml_explains_static_climatology(self):
        """derived.yaml retains the CHPclim static-climatology rationale post-M7 merge."""
        folded = _read_folded("derived.yaml")
        assert "static climatology, not a repeating time series" in folded

    def test_derived_yaml_explains_fixed_archive(self):
        """derived.yaml retains the CenTrends multi-year NetCDF archive rationale post-M7 merge."""
        folded = _read_folded("derived.yaml")
        assert "fixed archive of multi-year NetCDFs" in folded

    def test_bundled_catalog_still_loads_after_comment_restoration(self):
        """The comments don't break parsing -- catalog still loads 100 datasets."""
        cat = Catalog()
        assert len(cat.datasets) == 100
        # Pin the same health state we had before the H5 commit: 7 clean keys
        # plus the known `precipitation/daily` drift flagged by H3.
        report = cat.health()
        assert report["variable_metadata_drift"] == ["precipitation/daily"]
