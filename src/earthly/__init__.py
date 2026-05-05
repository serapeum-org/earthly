from __future__ import annotations

try:
    from importlib.metadata import PackageNotFoundError  # type: ignore
    from importlib.metadata import version
except ImportError:  # pragma: no cover
    from importlib_metadata import PackageNotFoundError  # type: ignore
    from importlib_metadata import version

from earthly.aggregate import AggregationConfig, aggregate_netcdf

__all__ = ["AggregationConfig", "aggregate_netcdf"]


try:
    __version__ = version(__name__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
