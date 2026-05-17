"""Lock-in for M3: reject unsigned-integer dtypes at the no-data sentinel guard."""

from __future__ import annotations

import numpy as np
import pytest

from earthlens.chc.backend import _reject_unsigned_for_nodata_sentinel

pytestmark = [pytest.mark.chc]


class TestRejectUnsignedForNodataSentinel:
    """The M3 guard refuses unsigned-int dtypes that can't carry a `-9999` sentinel."""

    @pytest.mark.parametrize(
        "dtype",
        [np.uint8, np.uint16, np.uint32, np.uint64],
    )
    def test_unsigned_integer_dtypes_raise_type_error(self, dtype):
        """Every unsigned-int dtype raises TypeError with a clear message."""
        with pytest.raises(TypeError, match=r"-9999 sentinel") as exc:
            _reject_unsigned_for_nodata_sentinel(np.dtype(dtype))
        assert "unsigned" in str(exc.value).lower()

    @pytest.mark.parametrize(
        "dtype",
        [np.float32, np.float64, np.int16, np.int32, np.int64],
    )
    def test_signed_and_float_dtypes_are_accepted_silently(self, dtype):
        """Signed-int and float dtypes pass the guard without raising."""
        _reject_unsigned_for_nodata_sentinel(np.dtype(dtype))

    def test_chirps_default_float32_is_accepted(self):
        """The shipped CHIRPS dtype (float32) passes silently."""
        _reject_unsigned_for_nodata_sentinel(np.dtype(np.float32))
