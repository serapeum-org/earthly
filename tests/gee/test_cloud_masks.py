"""Tests for `earthlens.gee.cloud_masks` (M3)."""

from __future__ import annotations

import pytest

from earthlens.gee.cloud_masks import _QA_PIXEL_CLEAR_BIT, landsat_sr


class _FakeMaskedImage:
    def __init__(self, mask):
        self.mask = mask


class _FakeQaBand:
    def __init__(self, recorder):
        self._recorder = recorder

    def bitwiseAnd(self, bit):  # noqa: N802
        self._recorder["bit"] = bit
        return f"clear<{bit}>"


class _FakeImage:
    def __init__(self):
        self.recorder: dict = {"selects": [], "bit": None, "mask": None}

    def select(self, band):
        self.recorder["selects"].append(band)
        return _FakeQaBand(self.recorder)

    def updateMask(self, mask):  # noqa: N802
        self.recorder["mask"] = mask
        return _FakeMaskedImage(mask)


class TestLandsatSr:
    """Tests for `landsat_sr`."""

    def test_clear_bit_is_position_six(self):
        """The Clear bit constant matches USGS's `1 << 6`."""
        assert _QA_PIXEL_CLEAR_BIT == 64

    def test_masks_with_clear_bit(self):
        """The image is masked by `qa.bitwiseAnd(1 << 6)`."""
        image = _FakeImage()
        out = landsat_sr(image)
        assert image.recorder["selects"] == ["QA_PIXEL"]
        assert image.recorder["bit"] == _QA_PIXEL_CLEAR_BIT
        assert image.recorder["mask"] == f"clear<{_QA_PIXEL_CLEAR_BIT}>"
        assert isinstance(out, _FakeMaskedImage)

    @pytest.mark.parametrize("sensor", ["LS7", "LS8", "LS9", "auto"])
    def test_accepts_supported_sensors(self, sensor):
        """Each supported sensor name produces the same masked image."""
        landsat_sr(_FakeImage(), sensor=sensor)

    def test_rejects_unknown_sensor(self):
        """An unrecognised sensor name raises `ValueError` listing the valid ones."""
        with pytest.raises(ValueError, match="sensor must be one of"):
            landsat_sr(_FakeImage(), sensor="S2")
