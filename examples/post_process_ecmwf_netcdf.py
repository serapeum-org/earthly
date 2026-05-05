"""CLI demo: aggregate a CDS NetCDF into per-window GeoTIFFs.

This script is a thin wrapper around
:func:`earthly.aggregate.aggregate_netcdf`. It exists so users can
slice a downloaded NetCDF into per-window GeoTIFFs (daily mean,
monthly sum, weekly mean, seasonal climatology, ...) without writing
any Python — just:

    python examples/post_process_ecmwf_netcdf.py \\
        out/2m_temperature_reanalysis-era5-single-levels.nc \\
        out/daily \\
        reanalysis-era5-single-levels \\
        2m-temperature \\
        --freq 1D --op auto

The flags map 1-to-1 to fields on
:class:`earthly.aggregate.AggregationConfig`. The catalog lookup
(`Catalog().get_variable(dataset, code)`) supplies the
:class:`earthly.ecmwf.Variable` row that drives `op="auto"` (state
vs flux) and the output filename stem.

Aggregation can also run as part of the download in one call:

    Earthly(...).download(aggregate=AggregationConfig(freq="1MS"))

See `docs/reference/aggregation.md` for the full feature reference.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from earthly.aggregate import AggregationConfig, aggregate_netcdf
from earthly.ecmwf import Catalog


def _cli() -> None:
    """Parse argv and run :func:`aggregate_netcdf` once."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "nc_path",
        type=Path,
        help="Path to the NetCDF written by ECMWF._api()",
    )
    parser.add_argument(
        "out_dir",
        type=Path,
        help="Output directory for the GeoTIFFs (created if absent).",
    )
    parser.add_argument(
        "dataset",
        help=(
            "CDS dataset short name, e.g. 'reanalysis-era5-single-levels' "
            "or 'reanalysis-era5-land'."
        ),
    )
    parser.add_argument(
        "variable",
        help=(
            "Catalog short code under that dataset, e.g. '2m-temperature' "
            "or 'total-precipitation'. Resolved through `Catalog` to drive "
            "op=auto routing and the output filename stem."
        ),
    )
    parser.add_argument(
        "--freq",
        default="1D",
        help=(
            "Pandas offset alias defining the window. Examples: '1D' (daily, "
            "default), '7D' (weekly), '1MS' (month-start), 'QS-DEC' (DJF/MAM/"
            "JJA/SON seasons), 'AS' (annual)."
        ),
    )
    parser.add_argument(
        "--op",
        choices=["mean", "sum", "min", "max", "std", "auto"],
        default="auto",
        help=(
            "Reduction within each window. 'auto' (default) reads "
            "Variable.is_flux: state -> mean, flux -> sum."
        ),
    )
    parser.add_argument(
        "--cell-size",
        type=float,
        default=0.125,
        help="Pixel size in degrees (default: 0.125 for ERA5 native).",
    )
    parser.add_argument(
        "--level",
        type=int,
        default=None,
        help=(
            "Pin a pressure level (e.g. 1000) when the NetCDF carries a "
            "`pressure_level` dimension. Required for 4-D inputs."
        ),
    )
    parser.add_argument(
        "--no-skipna",
        action="store_true",
        help="Disable NaN-aware reduction (any NaN in a window propagates).",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=None,
        help=(
            "Minimum non-NaN samples required for a window to produce a "
            "non-NaN value. Windows with fewer samples emit NaN."
        ),
    )
    args = parser.parse_args()

    spec = Catalog().get_variable(args.dataset, args.variable)
    config = AggregationConfig(
        freq=args.freq,
        op=args.op,
        out_dir=args.out_dir,
        cell_size=args.cell_size,
        level=args.level,
        skipna=not args.no_skipna,
        min_count=args.min_count,
    )
    results = aggregate_netcdf(args.nc_path, spec, config)
    print(f"Wrote {len(results)} GeoTIFFs to {args.out_dir}")


if __name__ == "__main__":
    _cli()
