"""Tiny shared base classes for per-variable / per-band catalog rows.

Only carries the slivers of behavior that genuinely repeat across
backends — most leaf metadata is domain-specific (optical bands carry
`wavelength` / `scale` / `offset`; CDS variables carry
`cds_variable` / `cds_pressure_level`; CHIRPS variables carry
`description` / `units`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FluxableLeaf(BaseModel):
    """Catalog row that flags whether its quantity accumulates over time.

    Both ECMWF :class:`earthlens.ecmwf.Variable` and CHIRPS
    :class:`earthlens.chc.Variable` carry an identical `types` field
    plus `is_flux` property — flux quantities (precipitation,
    evapotranspiration, radiation) are accumulated per timestep on
    the server side, so monthly aggregation has to multiply by the
    number of days in the month. State / instantaneous values
    (temperature, pressure) don't need that scaling.

    GEE :class:`earthlens.gee.Band` does NOT inherit from this — its
    raster bands don't carry flux semantics (cloud-screened optical
    reflectance, NDVI, etc.).

    Attributes:
        types: `"flux"` for accumulated quantities, `None` (the
            default) for state / instantaneous values. Concrete
            subclasses may narrow to a `Literal` if they enumerate
            other values.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    types: str | None = None

    @property
    def is_flux(self) -> bool:
        """`True` when `types == "flux"`; drives monthly accumulation scaling."""
        return self.types == "flux"
