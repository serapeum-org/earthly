"""Lock-in for `Catalog.describe()` after the C1 fix."""

from __future__ import annotations

import pytest

from earthlens.chc import Catalog

pytestmark = [pytest.mark.chc]


_DESCRIBE_KEYS: set[str] = {
    "dataset",
    "region",
    "temporal_resolution",
    "pandas_freq",
    "spatial_resolution",
    "formats",
    "ftp_bases",
    "file_patterns",
    "discrete_files",
    "is_discrete",
    "lat_boundaries",
    "lon_boundaries",
    "start_date",
    "end_date",
    "preliminary",
    "variables",
}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    """Loaded once per module — cached on disk mtime, so reuse is cheap."""
    return Catalog()


class TestDescribePerDate:
    """Datasets that publish per-date files (file_patterns set)."""

    def test_global_daily_pins_every_key(self, catalog: Catalog):
        """global-daily exposes all 16 record fields with expected types."""
        info = catalog.describe("global-daily")
        assert set(info) == _DESCRIBE_KEYS, info.keys()
        assert info["dataset"] == "global-daily"
        assert info["region"] == "global"
        assert info["temporal_resolution"] == "daily"
        assert info["pandas_freq"] == "D"
        assert info["spatial_resolution"] == [0.05, 0.25]
        assert info["formats"] == ["tif", "cog", "netcdf"]
        assert info["ftp_bases"] == {
            "tif": "pub/org/chc/products/CHIRPS-2.0/global_daily/tifs/p05/"
        }
        assert info["file_patterns"] == {
            "tif": "{year}/chirps-v2.0.{year}.{month}.{day}.tif.gz"
        }
        assert info["discrete_files"] is None
        assert info["is_discrete"] is False
        assert info["lat_boundaries"] == [-50.0, 50.0]
        assert info["lon_boundaries"] == [-180.0, 180.0]
        assert info["start_date"] == "1981-01-01"
        assert info["end_date"] is None
        assert info["preliminary"] is False
        assert info["variables"] == ["precipitation"]

    def test_global_monthly_per_date_branch(self, catalog: Catalog):
        """global-monthly is a simpler per-date case, no year-subdir."""
        info = catalog.describe("global-monthly")
        assert info["is_discrete"] is False
        assert info["file_patterns"] == {"tif": "chirps-v2.0.{year}.{month}.tif.gz"}
        assert info["discrete_files"] is None
        assert info["temporal_resolution"] == "monthly"
        assert info["pandas_freq"] == "MS"


class TestDescribeDiscrete:
    """Datasets that publish a fixed enumerated file set (discrete_files set)."""

    def test_centennial_monthly_single_file(self, catalog: Catalog):
        """centennial-trends-v1-monthly is a one-file multi-year NetCDF."""
        info = catalog.describe("centennial-trends-v1-monthly")
        assert info["is_discrete"] is True
        assert info["file_patterns"] is None
        assert info["discrete_files"] == {"netcdf": ["CenTrends_v1_monthly.nc"]}
        assert info["end_date"] == "2014-12-31"

    def test_centennial_seasonal_four_files(self, catalog: Catalog):
        """centennial-trends-v1-seasonal enumerates the four CenTrends seasons."""
        info = catalog.describe("centennial-trends-v1-seasonal")
        assert info["is_discrete"] is True
        assert info["file_patterns"] is None
        netcdf_files = info["discrete_files"]["netcdf"]
        assert len(netcdf_files) == 4
        assert all(name.startswith("CenTrends_v1_") for name in netcdf_files)
        assert all(name.endswith(".nc") for name in netcdf_files)

    def test_chpclim_twelve_static_months(self, catalog: Catalog):
        """chpclim-v2-monthly lists all 12 calendar-month TIFs."""
        info = catalog.describe("chpclim-v2-monthly")
        assert info["is_discrete"] is True
        assert info["file_patterns"] is None
        tif_files = info["discrete_files"]["tif"]
        assert len(tif_files) == 12
        assert tif_files[0] == "CHPclim2.90-90.01.tif"
        assert tif_files[-1] == "CHPclim2.90-90.12.tif"


class TestDescribeContract:
    """Contract-level invariants across the whole catalog."""

    def test_unknown_key_raises_value_error_with_hint(self, catalog: Catalog):
        """Unknown dataset raises ValueError with a did-you-mean hint."""
        with pytest.raises(ValueError, match="not in the CHC catalog") as exc:
            catalog.describe("global-dialy")
        assert "Did you mean 'global-daily'?" in str(exc.value)

    def test_every_dataset_is_describable(self, catalog: Catalog):
        """describe() succeeds for every key in available_datasets (no KeyError, no TypeError)."""
        missing: list[str] = []
        bad: list[tuple[str, str]] = []
        for key in catalog.available_datasets:
            try:
                info = catalog.describe(key)
            except ValueError:
                missing.append(key)
                continue
            except Exception as exc:
                bad.append((key, f"{type(exc).__name__}: {exc}"))
                continue
            if set(info) != _DESCRIBE_KEYS:
                bad.append((key, f"keys differ: {set(info) ^ _DESCRIBE_KEYS}"))
        assert not missing, f"available_datasets entries with no Dataset record: {missing}"
        assert not bad, f"describe() returned malformed records: {bad}"

    def test_exactly_one_of_file_patterns_or_discrete_files_is_set(self, catalog: Catalog):
        """The discriminator invariant on Dataset is reflected in describe() output."""
        for key in catalog.available_datasets:
            info = catalog.describe(key)
            has_patterns = info["file_patterns"] is not None
            has_discrete = info["discrete_files"] is not None
            assert has_patterns ^ has_discrete, (
                f"{key}: exactly one of file_patterns / discrete_files must be set; "
                f"got patterns={info['file_patterns']!r} discrete={info['discrete_files']!r}"
            )
            assert info["is_discrete"] is has_discrete, (
                f"{key}: is_discrete={info['is_discrete']} disagrees with "
                f"discrete_files={info['discrete_files']!r}"
            )
