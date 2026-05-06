"""Top-level package surface.

Re-exports the small set of symbols that have no per-backend SDK
requirement, so users can write `from earthly import ...` for the
common cases:

* :class:`Earthly` — the user-facing facade. Imports succeed without
  any backend extras installed; each backend is imported lazily on
  first use through the registry.
* :class:`AggregationConfig` and :func:`aggregate_netcdf` — the
  temporal aggregation feature. Pure pyramids/numpy/pandas, no
  backend SDK.

The concrete backends (`earthly.ecmwf.ECMWF`, `earthly.chirps.CHIRPS`,
`earthly.s3.S3`) are intentionally **not** re-exported here. Each
requires its own optional SDK (`pip install earthly[ecmwf]`, etc.),
so a top-level re-export would crash at import time on installations
that omitted the extra. Reach them via their submodules — e.g.
`from earthly.ecmwf import ECMWF` — or, more typically, through the
:class:`Earthly` facade's `data_source=` argument.
"""

from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError  # type: ignore
    from importlib.metadata import version
except ImportError:  # pragma: no cover
    from importlib_metadata import PackageNotFoundError  # type: ignore
    from importlib_metadata import version

from earthly.aggregate import AggregationConfig, aggregate_netcdf
from earthly.earthly import Earthly

__all__ = ["AggregationConfig", "Earthly", "aggregate_netcdf"]


try:
    __version__ = version(__name__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
