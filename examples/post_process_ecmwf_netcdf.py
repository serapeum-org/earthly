"""Slice a CDS NetCDF into per-date GeoTIFFs (post-processing example).

Background
----------
`earth2observe.ecmwf.ECMWF.api()` retrieves a NetCDF from the Climate
Data Store and writes it to disk. That NetCDF is the package's
**primary** product. Many users stop there and operate on the NetCDF
directly with `xarray`, `netCDF4`, `pyramids`, or any other tool of
their choice.

Some users, however, want one **GeoTIFF per date** — daily means or
monthly means depending on how they retrieved the data. That step is
opinionated post-processing (window selection, time-axis averaging,
flux scaling, raster format, projection) and used to live inside
`ECMWF.post_download`. It was lifted out of the package to keep the
download/retrieve responsibility separate from the slicing/raster
responsibility.

This script reproduces the original `post_download` behaviour as a
standalone, runnable example. It is intentionally framework-agnostic:
the only inputs are a NetCDF path, an output directory, and a few
metadata fields. Drop it into your own pipeline, fork it, or copy
chunks of it as needed.

What the script does, step by step
----------------------------------
For each date in `dates`:

1. **Pick the time window.**

   - Daily resolution → 1-day window.
   - Monthly resolution → calendar-month window (28-31 days).

   The width of the window also drives flux scaling (step 4).

2. **Boolean-index the time axis** to keep only samples whose
   timestamp falls inside the window. CDS daily NetCDFs typically
   carry 4 sub-daily samples per day (00:00, 06:00, 12:00, 18:00);
   monthly NetCDFs carry one sample per month. Either way, this step
   produces a 3-D slice of shape `(N_in_window, lat, lon)`.

3. **Average over the time axis** with `np.nanmean` so each output
   date is a single 2-D `(lat, lon)` array. NaN-aware so masked /
   missing samples don't poison the mean.

4. **Apply flux scaling** when `is_flux=True`. CDS reports flux
   variables (precipitation, evaporation, runoff, radiation
   accumulations, …) as per-timestep accumulations. To get a
   per-window total — daily mm, monthly mm, etc. — we multiply the
   mean by the window length in days. State variables (temperature,
   pressure, humidity) are instantaneous and are not scaled.

5. **Write a GeoTIFF.** Builds a GDAL-style geotransform from the
   NetCDF's lon/lat extent (assumes a regular grid; ERA5's native
   resolution is 0.125°), then calls
   `pyramids.dataset.Dataset.create_from_array(...).to_file(...)`.
   Output filename:
   `<variable_name>_ECMWF_ERA5_<units>_<resolution>_<YYYY>.<M>.<D>.tif`.

The script exposes one function — `post_process(...)` — and a
`__main__` block that wires it up to the catalog so you can run it
against any variable already in `cds_data_catalog.yaml`.

CDS-Beta time-variable compatibility
------------------------------------
CDS-Beta renamed the time variable from `time` to `valid_time` and
changed its units from `"hours since 1900-01-01"` to
`"seconds since 1970-01-01"`. The `_read_time_axis` helper below
tries each candidate name and parses the units string, so the same
script works on both legacy and current NetCDFs without a flag.
"""

from __future__ import annotations

import argparse
import calendar
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pyramids.dataset import Dataset
from pyramids.netcdf import NetCDF
from tqdm import tqdm


_TIME_VAR_CANDIDATES: tuple[str, ...] = ("valid_time", "time")


def _read_time_axis(fh: NetCDF) -> pd.DatetimeIndex:
    """Read the time coordinate of a CDS NetCDF as a DatetimeIndex.

    Tries each candidate time-variable name and parses its CF-style
    `"<unit> since <epoch>"` units string. Handles both the legacy
    CDS NetCDFs (`time` in `hours since 1900-01-01`) and CDS-Beta
    NetCDFs (`valid_time` in `seconds since 1970-01-01`).
    """
    metadata_vars = fh.meta_data.variables
    for name in _TIME_VAR_CANDIDATES:
        if name not in metadata_vars:
            continue
        units = metadata_vars[name].unit
        raw = fh._read_variable(name)
        if units is None or raw is None:
            continue
        unit_word, _, origin = units.partition(" since ")
        unit_alias = {
            "seconds": "s",
            "minutes": "m",
            "hours": "h",
            "days": "D",
        }.get(unit_word.strip().lower())
        if unit_alias is None:
            raise ValueError(
                f"unsupported time unit {unit_word!r} on {name!r} "
                f"(full units string: {units!r})"
            )
        return pd.to_datetime(raw, unit=unit_alias, origin=origin.strip())
    raise KeyError(
        f"NetCDF at {fh.file_name!r} has no recognised time variable "
        f"(tried {list(_TIME_VAR_CANDIDATES)}; got "
        f"{sorted(metadata_vars)})"
    )


def post_process(
    nc_path: Path | str,
    out_dir: Path | str,
    *,
    variable_name: str,
    units: str,
    dates: pd.DatetimeIndex,
    temporal_resolution: str,
    is_flux: bool = False,
    cell_size: float = 0.125,
    progress_bar: bool = True,
) -> list[tuple[pd.Timestamp, np.ndarray, Path]]:
    """Slice a CDS NetCDF into per-date GeoTIFFs.

    Args:
        nc_path: Path to the NetCDF written by `ECMWF.api()` (or any
            CDS-shaped NetCDF with a recognised time variable).
        out_dir: Directory the GeoTIFFs are written into. Must
            already exist.
        variable_name: Short variable name inside the NetCDF
            (e.g. `"t2m"` for 2-metre temperature). Used to index
            `fh.read_array(variable=...)` and to seed the output
            filename.
        units: Raw unit string for this variable (e.g. `"K"`,
            `"m"`). Embedded in the output filename — does **not**
            trigger any unit conversion; outputs stay in raw ERA5
            units.
        dates: One `pandas.Timestamp` per output GeoTIFF. For daily
            resolution this is one entry per date in your range; for
            monthly resolution this is one entry per month-start.
        temporal_resolution: `"daily"` or `"monthly"`. Drives the
            window length for time-axis slicing and the flux
            scaling factor.
        is_flux: When `True`, the per-window mean is multiplied by
            the number of days in the window (1 for daily,
            days-in-month for monthly). Use for accumulated CDS
            variables (precipitation, evaporation, runoff,
            radiation totals).
        cell_size: Pixel size in degrees. ERA5 native is 0.125°;
            ERA5-Land is 0.1°. Used to build the geotransform.
        progress_bar: Whether to show a tqdm bar during the per-date
            loop. Defaults to `True`.

    Returns:
        list of `(timestamp, array, path)` tuples — one per date.
        `array` is the time-window mean (flux-scaled when
        applicable); `path` is the GeoTIFF file just written.
    """
    out_dir = Path(out_dir)
    per_date_outputs: list[tuple[pd.Timestamp, np.ndarray, Path]] = []

    with NetCDF.read_file(str(nc_path), read_only=True) as fh:
        # Step 0: read the full time-by-lat-by-lon array and the
        # parsed time axis. Both stay in memory for the whole loop.
        data = fh.read_array(variable=variable_name)
        data_time = _read_time_axis(fh)

        # Step 0b: build the geotransform. Output rasters are aligned
        # to the NetCDF's lon/lat extent. GDAL's geotransform is
        # (left_x, pixel_width, rotation, top_y, rotation, -pixel_height)
        # — the y-pixel is negative because GDAL rasters are written
        # top-down (max latitude at row 0).
        top_y = float(np.nanmax(fh.lat))
        left_x = float(np.nanmin(fh.lon))
        geo = (left_x, cell_size, 0.0, top_y, 0.0, -cell_size)

        dates_iter = tqdm(
            dates, desc="Post-processing", disable=not progress_bar
        )
        for date in dates_iter:
            year, month, day = date.year, date.month, date.day

            # Step 1: pick the window. Daily → 1 day; monthly →
            # calendar days in this specific month.
            if temporal_resolution == "daily":
                days_later = 1
            elif temporal_resolution == "monthly":
                days_later = calendar.monthrange(year, month)[1]
            else:
                raise ValueError(
                    f"temporal_resolution must be 'daily' or 'monthly'; "
                    f"got {temporal_resolution!r}"
                )

            window_start = pd.Timestamp(year=year, month=month, day=day)
            window_end = window_start + pd.Timedelta(days=days_later)

            # Step 2: boolean-index the time axis so only samples
            # inside [window_start, window_end) are kept.
            in_window = (data_time >= window_start) & (data_time < window_end)
            data_one = data[in_window, :, :]

            # Step 3: NaN-aware mean over the time axis. Yields a
            # single (lat, lon) array per date.
            data_end = np.nanmean(data_one, axis=0)

            # Step 4: flux variables are per-timestep accumulations
            # on CDS — multiply by the window length to get a
            # per-window total.
            if is_flux:
                data_end = data_end * days_later

            # Step 5: write the GeoTIFF. EPSG 4326 is WGS84
            # geographic — CDS's native projection.
            name_out = (
                out_dir
                / f"{variable_name}_ECMWF_ERA5_{units}_{temporal_resolution}_"
                  f"{year}.{month}.{day}.tif"
            )
            Dataset.create_from_array(
                arr=data_end, geo=geo, epsg=4326
            ).to_file(str(name_out))
            per_date_outputs.append((date, data_end, name_out))

    return per_date_outputs


def _cli() -> None:
    """Wire `post_process` to the catalog so a single short code is
    enough to dispatch the right variable_name / units / is_flux.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "nc_path",
        type=Path,
        help="Path to the NetCDF written by ECMWF.api()",
    )
    parser.add_argument(
        "out_dir",
        type=Path,
        help="Output directory for the GeoTIFFs (must already exist)",
    )
    parser.add_argument(
        "variable",
        help="Catalog short code, e.g. '2m-temperature' or "
        "'total-precipitation'. Resolved through the package "
        "`Catalog` so units and flux flag are inferred.",
    )
    parser.add_argument(
        "--start", required=True, help="Start date (YYYY-MM-DD)"
    )
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--resolution",
        choices=["daily", "monthly"],
        default="daily",
        help="Window length for slicing (default: daily).",
    )
    parser.add_argument(
        "--cell-size",
        type=float,
        default=0.125,
        help="Pixel size in degrees (default: 0.125 for ERA5 native).",
    )
    args = parser.parse_args()

    from earth2observe.ecmwf import Catalog

    spec = Catalog().get_dataset(args.variable)
    freq = "D" if args.resolution == "daily" else "MS"
    dates = pd.date_range(args.start, args.end, freq=freq)

    written = post_process(
        nc_path=args.nc_path,
        out_dir=args.out_dir,
        variable_name=spec.nc_variable,
        units=spec.units,
        dates=dates,
        temporal_resolution=args.resolution,
        is_flux=spec.is_flux,
        cell_size=args.cell_size,
    )
    print(f"Wrote {len(written)} GeoTIFFs to {args.out_dir}")


if __name__ == "__main__":
    _cli()
