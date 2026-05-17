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

    def test_gefs_yaml_records_why_v3_rows_were_withheld(self):
        """gefs.yaml's header still explains why the v3 rows are absent (post-H2)."""
        text = (CATALOG_PATH / "gefs.yaml").read_text(encoding="utf-8")
        # After H2, the three `chirps-gefs-v3-*` rows were dropped because
        # their FTP patterns were known-broken. The header banner must keep
        # the institutional memory so a future maintainer who re-runs the
        # probe can resurrect the rows knowingly.
        assert "v3 rows are NOT shipped today" in text
        assert "anom" in text and "zscore" in text
        assert "year/month/" in text

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
        """The comments don't break parsing -- catalog still loads 97 datasets."""
        cat = Catalog()
        assert len(cat.datasets) == 97
        # Pin the same health state we had before the H5 commit: 7 clean keys
        # plus the known `precipitation/daily` drift flagged by H3.
        report = cat.health()
        assert report["variable_metadata_drift"] == ["precipitation/daily"]
