"""Cloud-masking helpers for Earth Engine Landsat / Sentinel collections.

Currently exposes :func:`landsat_sr`, which masks an LS7 / LS8 / LS9
Collection-2 Level-2 surface-reflectance image to its `QA_PIXEL` Clear
bit (bit 6). The Landsat C2-L2 `QA_PIXEL` bitmask is identical across
LS7 / LS8 / LS9, so the `sensor` argument is currently informational —
kept on the signature so callers can be explicit and so future
per-sensor refinements (e.g. extra confidence-bit thresholds) don't
break the public API.

Bit layout (USGS LSDS-1330):

```
Bit 0: Fill
Bit 1: Dilated Cloud
Bit 2: Cirrus              (LS8/LS9 only — set to 0 on LS7)
Bit 3: Cloud
Bit 4: Cloud Shadow
Bit 5: Snow
Bit 6: Clear               (set when bits 1,3,4 are all clear)
Bit 7: Water
Bit 8-9:   Cloud Confidence
Bit 10-11: Cloud Shadow Confidence
Bit 12-13: Snow/Ice Confidence
Bit 14-15: Cirrus Confidence
```

Ported from `gee_utils.raster.cloud_mask_ls7_sr`.
"""

from __future__ import annotations

from typing import Literal

import ee

# Bit 6 of QA_PIXEL: the "Clear" bit. Set when the pixel is neither
# Dilated Cloud (bit 1), Cloud (bit 3), nor Cloud Shadow (bit 4).
_QA_PIXEL_CLEAR_BIT: int = 1 << 6

Sensor = Literal["LS7", "LS8", "LS9", "auto"]
_SUPPORTED_SENSORS: frozenset[str] = frozenset({"LS7", "LS8", "LS9", "auto"})


def landsat_sr(image: ee.Image, sensor: Sensor = "auto") -> ee.Image:
    """Mask a Landsat C2-L2 surface-reflectance image to its Clear pixels.

    Reads `QA_PIXEL` and keeps only pixels whose bit 6 ("Clear") is set
    — i.e. no Dilated Cloud, no Cloud, no Cloud Shadow per USGS's
    derived flag.

    Args:
        image: An `ee.Image` from `LANDSAT/LE07/C02/T1_L2`,
            `LANDSAT/LC08/C02/T1_L2`, or `LANDSAT/LC09/C02/T1_L2`.
            Must carry the `QA_PIXEL` band.
        sensor: Source sensor name; one of `"LS7"`, `"LS8"`, `"LS9"`,
            or `"auto"` (the default). Currently informational — the
            Clear bit is at the same position across all three C2-L2
            sensors — but kept on the signature for future per-sensor
            refinements (e.g. tighter confidence thresholds) and so
            callers can declare intent.

    Returns:
        The input image with `updateMask(qa.bitwiseAnd(1 << 6))`
        applied.

    Raises:
        ValueError: If `sensor` is not one of the supported names.
    """
    if sensor not in _SUPPORTED_SENSORS:
        raise ValueError(
            f"sensor must be one of {sorted(_SUPPORTED_SENSORS)}, got {sensor!r}"
        )
    qa = image.select("QA_PIXEL")
    clear_pixels = qa.bitwiseAnd(_QA_PIXEL_CLEAR_BIT)
    return image.updateMask(clear_pixels)
