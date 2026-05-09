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
    - Standalone aggregation: read a CDS NetCDF, write per-month
      GeoTIFFs to disk. `Catalog` is only consulted to resolve the
      `(dataset, code)` pair into the `Variable` row that drives
      `is_flux` and the output filename — no `ECMWF` instance is
      built:

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
        >>> for window_label, arr, target in results:  # doctest: +SKIP
        ...     print(window_label, arr.shape, target.name)

        ```
    - In-memory aggregation: pass `out_dir=None` to skip disk writes
      and inspect the per-window arrays directly:

        ```python
        >>> from earthly import AggregationConfig, aggregate_netcdf  # doctest: +SKIP
        >>> from earthly.ecmwf import Catalog  # doctest: +SKIP
        >>> spec = Catalog().get_variable(  # doctest: +SKIP
        ...     "reanalysis-era5-single-levels", "2m-temperature"
        ... )
        >>> results = aggregate_netcdf(  # doctest: +SKIP
        ...     "out/2m_temperature_reanalysis-era5-single-levels.nc",
        ...     spec,
        ...     AggregationConfig(freq="1D", op="auto"),
        ... )
        >>> first_label, first_array, first_path = results[0]  # doctest: +SKIP
        >>> first_path is None  # doctest: +SKIP
        True

        ```
    - Bundled with download via the ECMWF backend (single call
      retrieves and aggregates each variable):

        ```python
        >>> from earthly import AggregationConfig  # doctest: +SKIP
        >>> from earthly.earthly import Earthly  # doctest: +SKIP
        >>> earthly = Earthly(  # doctest: +SKIP
        ...     data_source="ecmwf",
        ...     temporal_resolution="daily",
        ...     start="2022-01-01",
        ...     end="2022-01-31",
        ...     variables={"reanalysis-era5-single-levels": ["2m-temperature"]},
        ...     lat_lim=[4.0, 5.0],
        ...     lon_lim=[-75.0, -74.0],
        ...     path="out/era5",
        ... )
        >>> earthly.download(  # doctest: +SKIP
        ...     aggregate=AggregationConfig(freq="1MS", op="mean"),
        ... )

        ```
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Literal

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

    Walks `nc.dimension_names` (root-container view) or
    `nc._md_array_dims` (variable-cube view, set by
    :meth:`NetCDF.get_variable`) looking for any of
    :data:`_LEVEL_DIM_CANDIDATES`. CDS pressure-level NetCDFs use
    `pressure_level`; some derived datasets use plain `level`. The
    first match wins.

    Args:
        nc: An open :class:`pyramids.netcdf.NetCDF` instance — either
            the root MDIM container or a variable subset returned by
            :meth:`NetCDF.get_variable`.

    Returns:
        str | None: The matched dimension name when the NetCDF has a
        pressure-level axis; `None` for 3-D `(time, lat, lon)` files.
    """
    dim_names = nc.dimension_names or getattr(nc, "_md_array_dims", None) or ()
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


def _window_groups(
    time_axis: pd.DatetimeIndex,
    freq: str,
) -> Iterator[tuple[pd.Timestamp, np.ndarray]]:
    """Yield `(window_label, mask)` pairs that bucket `time_axis` by `freq`.

    Builds a `pandas.Series` indexed by `time_axis` and groups it
    with `pandas.Grouper(freq=freq)`. Each group's `index` gives the
    timestamps belonging to that window; the boolean mask is built
    by membership against `time_axis` so callers can use it to slice
    a numpy array along its first axis.

    Empty groups (windows with no samples) are silently skipped —
    `aggregate_netcdf` doesn't write a GeoTIFF for a window it has
    no data for.

    Args:
        time_axis: Time coordinate as a :class:`pandas.DatetimeIndex`.
            Typically the result of :func:`_read_time_axis`.
        freq: Pandas offset alias (`"1D"`, `"7D"`, `"1MS"`, `"QS-DEC"`,
            `"AS"`, ...). Anything :class:`pandas.Grouper` accepts.

    Yields:
        tuple[pd.Timestamp, np.ndarray]: For each non-empty window:
        the group key (window's left-edge timestamp) paired with a
        boolean mask of length `len(time_axis)`.

    Examples:
        - Group four 6-hourly slots into one daily window:

            ```python
            >>> import pandas as pd
            >>> from earthly.aggregate import _window_groups
            >>> idx = pd.date_range("2022-01-01", periods=4, freq="6h")
            >>> windows = list(_window_groups(idx, "1D"))
            >>> len(windows)
            1
            >>> label, mask = windows[0]
            >>> label
            Timestamp('2022-01-01 00:00:00')
            >>> mask.tolist()
            [True, True, True, True]

            ```
        - Group two days of 6-hourly samples into two daily windows:

            ```python
            >>> import pandas as pd
            >>> from earthly.aggregate import _window_groups
            >>> idx = pd.date_range("2022-01-01", periods=8, freq="6h")
            >>> [label.strftime("%Y-%m-%d") for label, _ in _window_groups(idx, "1D")]
            ['2022-01-01', '2022-01-02']

            ```
    """
    indexer = pd.Series(np.arange(len(time_axis)), index=time_axis)
    timestamps = pd.Index(time_axis)
    for window_label, group in indexer.groupby(pd.Grouper(freq=freq)):
        if group.empty:
            continue
        mask = np.asarray(timestamps.isin(group.index))
        yield window_label, mask


OperationLiteral = Literal["mean", "sum", "min", "max", "std", "auto"]


_REDUCERS_SKIPNA: dict[str, np.ufunc | callable] = {
    "mean": np.nanmean,
    "sum": np.nansum,
    "min": np.nanmin,
    "max": np.nanmax,
    "std": np.nanstd,
}

_REDUCERS_STRICT: dict[str, np.ufunc | callable] = {
    "mean": np.mean,
    "sum": np.sum,
    "min": np.min,
    "max": np.max,
    "std": np.std,
}


def _reduce(
    arr: np.ndarray,
    op: str,
    skipna: bool,
    min_count: int | None,
) -> np.ndarray:
    """Reduce a `(time, lat, lon)` slice along axis 0 with the named op.

    Dispatches `op` to the matching numpy reducer (`np.nanmean` etc.
    when `skipna=True`, plain `np.mean` etc. when `skipna=False`),
    then masks pixels whose non-NaN sample count falls below
    `min_count`.

    `op="auto"` is **not** accepted here — `aggregate_netcdf` resolves
    `auto` to a concrete operator before calling this helper. Passing
    `"auto"` raises `KeyError` to surface the mistake at the call site.

    Args:
        arr: Array to reduce. The first axis is collapsed; the
            remaining axes pass through unchanged. Typically
            `(N_in_window, lat, lon)`.
        op: One of `"mean" / "sum" / "min" / "max" / "std"`. Resolved
            to a numpy reducer via the dispatch table.
        skipna: When `True`, the NaN-aware reducer is used
            (`np.nanmean` etc.); when `False`, the strict variant is
            used and any NaN in a window propagates to the output.
        min_count: When set, pixels with fewer than this many non-NaN
            samples along axis 0 emit NaN regardless of the reduction
            result. `None` disables the floor.

    Returns:
        np.ndarray: Reduced array with axis 0 collapsed.

    Raises:
        KeyError: If `op` is not in the dispatch table (in particular,
            `"auto"` is rejected — resolve it to a concrete op first).

    Examples:
        - NaN-aware mean over the time axis:

            ```python
            >>> import numpy as np
            >>> from earthly.aggregate import _reduce
            >>> arr = np.array([[[1.0, 2.0]], [[3.0, np.nan]], [[5.0, 6.0]]])
            >>> _reduce(arr, op="mean", skipna=True, min_count=None).tolist()
            [[3.0, 4.0]]

            ```
        - Strict mean propagates NaN when `skipna=False`:

            ```python
            >>> import numpy as np
            >>> from earthly.aggregate import _reduce
            >>> arr = np.array([[[1.0, np.nan]], [[3.0, 4.0]]])
            >>> result = _reduce(arr, op="mean", skipna=False, min_count=None)
            >>> bool(np.isnan(result[0, 1])), float(result[0, 0])
            (True, 2.0)

            ```
        - `min_count` masks under-sampled pixels:

            ```python
            >>> import numpy as np
            >>> from earthly.aggregate import _reduce
            >>> arr = np.array([[[1.0, np.nan]], [[2.0, np.nan]]])
            >>> result = _reduce(arr, op="mean", skipna=True, min_count=2)
            >>> float(result[0, 0]), bool(np.isnan(result[0, 1]))
            (1.5, True)

            ```
    """
    table = _REDUCERS_SKIPNA if skipna else _REDUCERS_STRICT
    if op not in table:
        raise KeyError(
            f"unknown reduction op {op!r}; expected one of "
            f"{sorted(table)!r} (resolve 'auto' before calling _reduce)"
        )
    reducer = table[op]
    result = reducer(arr, axis=0)
    if min_count is not None:
        non_nan_count = np.count_nonzero(~np.isnan(arr), axis=0)
        result = np.where(non_nan_count >= min_count, result, np.nan)
    return result


def _resolve_op(op: OperationLiteral, var_info: Variable) -> str:
    """Turn `op="auto"` into a concrete reduction based on the catalog row.

    `Variable.is_flux` (in `earthly.ecmwf.catalog`) is `True` for CDS
    flux variables — precipitation, evaporation, runoff, radiation
    accumulations — and `False` for state variables (temperature,
    pressure, humidity, ...).

    Resolution rules:

    * `op="auto"` + `var_info.is_flux=True` → `"sum"`
    * `op="auto"` + `var_info.is_flux=False` → `"mean"`
    * any explicit op → returned unchanged

    This **replaces** the legacy `mean × days_later` scaling that
    `examples/post_process_ecmwf_netcdf.py:226` (pre-rewrite) used.
    The two are equivalent only when every slot inside a window has
    a sample; for partial windows, true `sum` is correct and
    `mean × N` overcounts. `_reduce(..., op="sum", ...)` gives the
    correct per-window total in both cases.

    Args:
        op: The :attr:`AggregationConfig.op` value, possibly `"auto"`.
        var_info: Catalog entry for the variable being aggregated.
            Only `is_flux` is consulted; the rest is ignored.

    Returns:
        str: The concrete operator name (`"mean"`, `"sum"`, `"min"`,
        `"max"`, or `"std"`) ready for :func:`_reduce`.

    Examples:
        - State variable with `is_flux=False` resolves to `"mean"`:

            ```python
            >>> from types import SimpleNamespace
            >>> from earthly.aggregate import _resolve_op
            >>> _resolve_op("auto", SimpleNamespace(is_flux=False))
            'mean'

            ```
        - Flux variable with `is_flux=True` resolves to `"sum"`:

            ```python
            >>> from types import SimpleNamespace
            >>> from earthly.aggregate import _resolve_op
            >>> _resolve_op("auto", SimpleNamespace(is_flux=True))
            'sum'

            ```
        - Explicit ops pass through verbatim:

            ```python
            >>> from types import SimpleNamespace
            >>> from earthly.aggregate import _resolve_op
            >>> _resolve_op("max", SimpleNamespace(is_flux=True))
            'max'

            ```
    """
    if op != "auto":
        return op
    return "sum" if var_info.is_flux else "mean"


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
        KeyError: If the NetCDF has no recognised time variable
            (`valid_time` / `time`); see :func:`_read_time_axis`.
        ValueError: If `config.level` is set but the NetCDF has no
            pressure-level dimension, or vice versa; see
            :func:`_resolve_pressure_level`. Also raised by pandas
            when `config.freq` is not a recognised offset alias.

    See Also:
        - :class:`AggregationConfig`: the frozen request payload.
        - :class:`earthly.ecmwf.Catalog`: resolves `(dataset, code)`
          pairs to the :class:`earthly.ecmwf.Variable` rows that
          drive `var_info.is_flux` and the output filename.
        - `examples/post_process_ecmwf_netcdf.py`: thin CLI demo of
          this function (after task L1).
    """
    from pyramids.dataset import Dataset
    from pyramids.netcdf import NetCDF

    out_dir: Path | None = config.out_dir
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    op = _resolve_op(config.op, var_info)

    nc = NetCDF.read_file(str(nc_path))
    # Read time axis + geotransform from the root container — only the
    # container exposes `get_time_variable` against the underlying CF
    # metadata. The variable-subset cube returned by `get_variable`
    # tracks coords on `_band_dim_values_map` instead, but does not
    # round-trip them through `get_time_variable`. The cube is what
    # `sel()` and the band-dim-aware multi-D logic need, so use it
    # for level pinning + array read.
    time_axis = _read_time_axis(nc)
    geo = nc.geotransform
    var = nc.get_variable(var_info.nc_variable)
    var = _resolve_pressure_level(var, config.level)
    arr = var.read_array()

    results: list[tuple[pd.Timestamp, np.ndarray, Path | None]] = []
    for window_label, mask in _window_groups(time_axis, config.freq):
        slice_ = arr[mask, :, :]
        reduced = _reduce(slice_, op=op, skipna=config.skipna, min_count=config.min_count)

        target: Path | None = None
        if out_dir is not None:
            target = out_dir / (
                f"{var_info.cds_variable}_{config.freq}_"
                f"{window_label:%Y%m%d}.tif"
            )
            Dataset.create_from_array(
                arr=reduced, geo=geo, epsg=4326
            ).to_file(str(target))

        results.append((window_label, reduced, target))

    return results
