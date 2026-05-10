"""Unit tests for :func:`earthly.ecmwf.backend._unwrap_zipped_netcdf`.

CDS occasionally returns a zip-wrapped NetCDF even when
`data_format='netcdf'` was requested (observed consistently on
``reanalysis-era5-land-monthly-means`` and similar partitioned
datasets). The helper detects the zip header, extracts the single
inner ``.nc`` member, and overwrites the target file in place. These
tests pin the contract for that helper without going to CDS.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from earthly.ecmwf.backend import _unwrap_zipped_netcdf

pytestmark = [pytest.mark.unit]


# Magic bytes used to make a "looks like a real NetCDF" file. The
# helper only inspects the zip header (`PK\x03\x04`); these four
# bytes are the netCDF-4 / HDF5 signature, which is what GDAL would
# also key off if asked to validate. The exact body does not matter.
_NETCDF_MAGIC: bytes = b"\x89HDF"
_FAKE_NC_BODY: bytes = _NETCDF_MAGIC + b"\x00" * 60


def _write_fake_nc(path: Path) -> bytes:
    """Write a fake NetCDF body to `path` and return its bytes."""
    path.write_bytes(_FAKE_NC_BODY)
    return _FAKE_NC_BODY


def _zip_with_members(path: Path, members: dict[str, bytes]) -> None:
    """Create a zip at `path` containing each `name -> bytes` pair."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in members.items():
            zf.writestr(name, body)


class TestUnwrapZippedNetcdf:
    """Behavioural contract for `_unwrap_zipped_netcdf`."""

    def test_noop_on_real_netcdf(self, tmp_path):
        """A plain NetCDF file is left untouched (bytes and mtime preserved)."""
        target = tmp_path / "real.nc"
        original = _write_fake_nc(target)
        mtime_before = target.stat().st_mtime_ns

        _unwrap_zipped_netcdf(target)

        assert target.read_bytes() == original
        # File untouched: mtime should not change because we never write.
        assert target.stat().st_mtime_ns == mtime_before

    def test_unwraps_single_nc_member(self, tmp_path):
        """A one-`.nc` zip is replaced in place with the inner member's bytes."""
        target = tmp_path / "wrapped.nc"
        inner_body = _NETCDF_MAGIC + b"some-payload-bytes"
        _zip_with_members(target, {"data_stream-moda.nc": inner_body})

        # Sanity: file is currently a zip.
        assert zipfile.is_zipfile(target)

        _unwrap_zipped_netcdf(target)

        # File is no longer a zip and its bytes match the inner member.
        assert not zipfile.is_zipfile(target)
        assert target.read_bytes() == inner_body

    def test_zero_nc_members_raises(self, tmp_path):
        """A zip with no `.nc` member raises RuntimeError."""
        target = tmp_path / "wrapped.nc"
        _zip_with_members(target, {"readme.txt": b"hello"})

        with pytest.raises(RuntimeError, match=r"0 \.nc members"):
            _unwrap_zipped_netcdf(target)

    def test_multiple_nc_members_raises(self, tmp_path):
        """A zip with more than one `.nc` member raises RuntimeError.

        Multi-member zips are a real CDS shape (some derived datasets
        return per-variable bundles), but the current helper rejects
        them so an unexpected layout never silently passes — handling
        them needs a per-variable file-pick policy that is out of
        scope for this helper.
        """
        target = tmp_path / "wrapped.nc"
        _zip_with_members(
            target,
            {
                "first.nc": _NETCDF_MAGIC + b"a",
                "second.nc": _NETCDF_MAGIC + b"b",
            },
        )

        with pytest.raises(RuntimeError, match=r"2 \.nc members"):
            _unwrap_zipped_netcdf(target)

    def test_idempotent_on_re_run(self, tmp_path):
        """Running the helper twice on a now-unwrapped file is a no-op."""
        target = tmp_path / "wrapped.nc"
        inner_body = _NETCDF_MAGIC + b"payload"
        _zip_with_members(target, {"only.nc": inner_body})

        _unwrap_zipped_netcdf(target)
        first_pass = target.read_bytes()

        _unwrap_zipped_netcdf(target)  # second call: noop branch.
        assert target.read_bytes() == first_pass
        assert target.read_bytes() == inner_body

    def test_error_message_lists_zip_members(self, tmp_path):
        """The RuntimeError includes every member name so the user
        can see what CDS actually returned (helps debug new shapes).
        """
        target = tmp_path / "wrapped.nc"
        _zip_with_members(
            target,
            {"readme.txt": b"hi", "data.csv": b"a,b,c"},
        )

        with pytest.raises(RuntimeError) as excinfo:
            _unwrap_zipped_netcdf(target)
        message = str(excinfo.value)
        assert "readme.txt" in message
        assert "data.csv" in message

    def test_no_temp_file_left_after_error(self, tmp_path):
        """On any RuntimeError path, the `.unwrap.tmp` sibling is cleaned up.

        The streaming implementation extracts to a sibling temp file and
        then atomically renames it onto the target. If the helper raises
        (multi-member zip, etc.), the temp file must not be left behind.
        """
        target = tmp_path / "wrapped.nc"
        _zip_with_members(
            target,
            {
                "first.nc": _NETCDF_MAGIC + b"a",
                "second.nc": _NETCDF_MAGIC + b"b",
            },
        )

        with pytest.raises(RuntimeError):
            _unwrap_zipped_netcdf(target)

        # No `.unwrap.tmp` sibling left behind.
        assert not (target.parent / (target.name + ".unwrap.tmp")).exists()
        # Original (pre-call) zip is still in place.
        assert zipfile.is_zipfile(target)

    def test_streaming_handles_files_larger_than_default_buffer(self, tmp_path):
        """A multi-buffer-size inner member round-trips byte-for-byte.

        Picks a payload bigger than `shutil.COPY_BUFSIZE` (64 KiB on
        most platforms) so that `copyfileobj` makes more than one read
        / write cycle. Equivalent of confirming the implementation is
        actually streaming and not leaning on a single full read.
        """
        # ~256 KiB payload — well past the default 64 KiB copyfileobj buffer.
        big_body = _NETCDF_MAGIC + (b"\x42" * (256 * 1024))
        target = tmp_path / "wrapped.nc"
        _zip_with_members(target, {"big.nc": big_body})

        _unwrap_zipped_netcdf(target)

        assert target.read_bytes() == big_body
        assert not (target.parent / (target.name + ".unwrap.tmp")).exists()
