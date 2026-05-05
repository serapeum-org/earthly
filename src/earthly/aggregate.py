"""Temporal aggregation of CDS-shaped NetCDF files into per-window GeoTIFFs.

Read a NetCDF whose first axis is time, group its samples by a pandas
offset alias (`"1D"`, `"7D"`, `"1MS"`, `"QS-DEC"`, ...), reduce each
group with one of mean / sum / min / max / std, and write one GeoTIFF
per window. The whole pipeline runs against pyramids primitives —
`pyramids.netcdf.NetCDF` for read + CF metadata, `pyramids.dataset.Dataset`
for write — plus numpy and pandas. No xarray.

The module sits at the top level of `earthly` because the algorithm is
not specific to any backend: any CDS-shaped NetCDF works (ECMWF S3
exports, CDS retrieves, CDS-Beta retrieves, ...). The ECMWF backend
chains it via `ECMWF.download(aggregate=...)` for the
"download-and-aggregate-in-one-call" path; standalone callers use
`aggregate_netcdf` directly.

The two public symbols are :class:`AggregationConfig` (the frozen
request shape) and :func:`aggregate_netcdf` (the function). They are
also re-exported from `earthly` so callers can write
`from earthly import AggregationConfig, aggregate_netcdf`.

Examples:
    - Standalone aggregation against a NetCDF on disk:

        ```python
        >>> from earthly import AggregationConfig, aggregate_netcdf  # doctest: +SKIP
        >>> from earthly.ecmwf import Catalog  # doctest: +SKIP
        >>> spec = Catalog().get_variable(  # doctest: +SKIP
        ...     "reanalysis-era5-single-levels", "2m-temperature"
        ... )
        >>> results = aggregate_netcdf(  # doctest: +SKIP
        ...     "out/2m_temperature_reanalysis-era5-single-levels.nc",
        ...     spec,
        ...     AggregationConfig(freq="1MS", op="mean", out_dir="out/monthly"),
        ... )

        ```
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from earthly.ecmwf import Variable
    from pyramids.netcdf import NetCDF

__all__ = ["AggregationConfig", "OperationLiteral", "aggregate_netcdf"]


_TIME_VAR_CANDIDATES: tuple[str, ...] = ("valid_time", "time")


def _read_time_axis(nc: NetCDF) -> pd.DatetimeIndex:
    """Return a NetCDF's time coordinate as a :class:`pandas.DatetimeIndex`.

    Tries each name in :data:`_TIME_VAR_CANDIDATES` in order
    (`"valid_time"` first to cover CDS-Beta NetCDFs, then `"time"`
    for legacy CDS). The first candidate that resolves to a non-empty
    list of date strings via
    :meth:`pyramids.netcdf.NetCDF.get_time_variable` is returned as a
    :class:`pandas.DatetimeIndex`.

    pyramids' :meth:`get_time_variable` already parses the CF
    `"<unit> since <epoch>"` units string for us through
    `create_time_conversion_func`; this helper just chooses the
    candidate name and converts the formatted strings back to
    timestamps with :func:`pandas.to_datetime`.

    Args:
        nc: An open :class:`pyramids.netcdf.NetCDF` instance pointed
            at the source file.

    Returns:
        pd.DatetimeIndex: One entry per timestep in the NetCDF's
        time dimension.

    Raises:
        KeyError: If none of :data:`_TIME_VAR_CANDIDATES` is present
            on the NetCDF as a parseable time variable.
    """
    for name in _TIME_VAR_CANDIDATES:
        time_strs = nc.get_time_variable(var_name=name)
        if time_strs:
            return pd.to_datetime(time_strs)
    raise KeyError(
        f"NetCDF has no recognised time variable; tried "
        f"{list(_TIME_VAR_CANDIDATES)!r}. Re-check the file's time "
        "dimension name and CF `units` attribute."
    )


_LEVEL_DIM_CANDIDATES: tuple[str, ...] = ("pressure_level", "level")


def _find_level_dim(nc: NetCDF) -> str | None:
    """Return the pressure-level dimension name, or `None` for 3-D files.

    Walks `nc.dimension_names` looking for any of
    :data:`_LEVEL_DIM_CANDIDATES`. CDS pressure-level NetCDFs use
    `pressure_level`; some derived datasets use plain `level`. The
    first match wins.

    Args:
        nc: An open :class:`pyramids.netcdf.NetCDF` instance.

    Returns:
        str | None: The matched dimension name when the NetCDF has a
        pressure-level axis; `None` for 3-D `(time, lat, lon)` files.
    """
    dim_names = nc.dimension_names or ()
    for candidate in _LEVEL_DIM_CANDIDATES:
        if candidate in dim_names:
            return candidate
    return None


def _resolve_pressure_level(
    nc: NetCDF,
    level: int | float | None,
) -> NetCDF:
    """Pin a pressure level on a 4-D NetCDF, or pass through 3-D files.

    Decision matrix:

    | Has level dim? | `level` set? | Result                         |
    |----------------|--------------|--------------------------------|
    | Yes            | Yes          | `nc.sel(<level_dim>=level)`    |
    | Yes            | No           | `ValueError` (ambiguous)       |
    | No             | Yes          | `ValueError` (no level to set) |
    | No             | No           | Pass-through                   |

    Args:
        nc: The NetCDF to examine.
        level: The :attr:`AggregationConfig.level` value.

    Returns:
        NetCDF: Either the original `nc` (3-D pass-through) or a new
        instance from :meth:`pyramids.netcdf.NetCDF.sel` with the
        chosen level pinned.

    Raises:
        ValueError: When the NetCDF has a pressure-level dimension
            but no `level` was passed, or when `level` was passed but
            the NetCDF has no pressure-level dimension.
    """
    level_dim = _find_level_dim(nc)
    if level_dim is None and level is None:
        return nc
    if level_dim is None and level is not None:
        raise ValueError(
            f"`level={level!r}` was set but the NetCDF has no "
            f"pressure-level dimension (looked for "
            f"{list(_LEVEL_DIM_CANDIDATES)!r} in "
            f"{list(nc.dimension_names or ())!r}). Drop `level` or "
            "point at a 4-D pressure-level file."
        )
    if level_dim is not None and level is None:
        raise ValueError(
            f"NetCDF has a {level_dim!r} dimension; pass "
            "`level=<value>` on AggregationConfig to pin one (e.g. "
            "`level=1000`). 4-D aggregation across all levels at "
            "once is not supported."
        )
    return nc.sel(**{level_dim: level})


OperationLiteral = Literal["mean", "sum", "min", "max", "std", "auto"]


class AggregationConfig(BaseModel):
    """Frozen request shape consumed by :func:`aggregate_netcdf`.

    Carries the windowing frequency, reduction operator, and output
    location. Frozen + `extra="forbid"` so a typo in a field name
    (e.g. `freqency=`) fails loud at construction time rather than
    silently using the default.

    Attributes:
        freq: Pandas offset alias defining the window. Examples:
            `"1D"` (daily), `"7D"` (weekly), `"1MS"` (month-start),
            `"QS-DEC"` (DJF/MAM/JJA/SON climatological seasons),
            `"AS"` (annual). Any string accepted by
            `pandas.Grouper(freq=...)` is valid.
        op: Reduction applied within each window. `"auto"` reads
            `Variable.is_flux` (state→`"mean"`, flux→`"sum"`); the
            other values are forwarded as-is to the dispatcher.
        out_dir: Directory the per-window GeoTIFFs are written to.
            Created (with parents) if absent. `None` skips the write
            step entirely and returns arrays in memory only.
        cell_size: Pixel size in degrees, embedded in the output
            filename as a metadata note. `0.125` for ERA5 native,
            `0.1` for ERA5-Land. The geotransform itself is read off
            the NetCDF — this is informational only.
        level: When the NetCDF has a `pressure_level` dimension, pin
            this level via :meth:`pyramids.netcdf.NetCDF.sel`. `None`
            (default) requires a 3-D NetCDF; pass an explicit level
            (e.g. `1000`) to aggregate a single 4-D layer.
        skipna: When `True`, the reduction is NaN-aware
            (`np.nanmean` etc.). `False` propagates any NaN in a
            window to the output.
        min_count: Minimum non-NaN samples required for a window to
            produce a non-NaN value. Windows with fewer samples emit
            NaN. `None` (default) means no minimum.

    Examples:
        - Daily-mean defaults — only `freq` is required, the rest
          stays at sensible CDS-shaped defaults:

            ```python
            >>> from earthly.aggregate import AggregationConfig
            >>> cfg = AggregationConfig(freq="1D")
            >>> cfg.op
            'auto'
            >>> cfg.skipna
            True
            >>> cfg.cell_size
            0.125
            >>> cfg.out_dir is None
            True

            ```
        - Monthly sum into an explicit output directory:

            ```python
            >>> from pathlib import Path
            >>> from earthly.aggregate import AggregationConfig
            >>> cfg = AggregationConfig(
            ...     freq="1MS",
            ...     op="sum",
            ...     out_dir=Path("out") / "monthly",
            ... )
            >>> cfg.freq, cfg.op
            ('1MS', 'sum')
            >>> cfg.out_dir.name
            'monthly'

            ```
        - Pin a pressure level for 4-D inputs and require a minimum
          sample count per window:

            ```python
            >>> from earthly.aggregate import AggregationConfig
            >>> cfg = AggregationConfig(
            ...     freq="7D", op="mean", level=1000, min_count=20,
            ... )
            >>> cfg.level, cfg.min_count
            (1000, 20)

            ```
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    freq: str
    op: OperationLiteral = "auto"
    out_dir: Path | None = None
    cell_size: float = 0.125
    level: int | float | None = None
    skipna: bool = True
    min_count: int | None = None


def aggregate_netcdf(
    nc_path: Path | str,
    var_info: Variable,
    config: AggregationConfig,
) -> list[tuple[pd.Timestamp, np.ndarray, Path | None]]:
    """Slice a CDS-shaped NetCDF into per-window aggregated outputs.

    Reads the NetCDF, groups its time axis by `config.freq`, reduces
    each group with `config.op`, and (when `config.out_dir` is set)
    writes one GeoTIFF per window. Returns the per-window arrays
    alongside their timestamps and output paths so callers can chain
    further processing without re-opening the files.

    Args:
        nc_path: Path to the NetCDF on disk.
        var_info: Catalog row for the variable being aggregated. Used
            to pick the variable from the NetCDF
            (`var_info.nc_variable`), seed the output filename
            (`var_info.cds_variable`), and resolve `op="auto"`
            (`var_info.is_flux`).
        config: Frozen :class:`AggregationConfig` describing the
            window, reduction, and output location.

    Returns:
        list[tuple[pd.Timestamp, np.ndarray, Path | None]]: One entry
        per window. The first item is the window's left-edge
        timestamp; the second is the reduced 2-D array; the third is
        the GeoTIFF path (or `None` when `config.out_dir` was `None`).

    Raises:
        NotImplementedError: This is the H1 skeleton; the body is
            wired up by H5.

    See Also:
        - :class:`AggregationConfig`: the frozen request payload.
        - :class:`earthly.ecmwf.Catalog`: resolves `(dataset, code)`
          pairs to the :class:`earthly.ecmwf.Variable` rows that
          drive `var_info.is_flux` and the output filename.
        - `examples/post_process_ecmwf_netcdf.py`: thin CLI demo of
          this function (after task L1).
    """
    raise NotImplementedError(
        "aggregate_netcdf is implemented in task H5; H1 only ships the "
        "skeleton + AggregationConfig."
    )
