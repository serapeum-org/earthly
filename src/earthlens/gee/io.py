"""Download Earth Engine `FeatureCollection`s into pandas / GeoPandas.

Three public helpers cover the typical "I built an `ee.FeatureCollection`,
now get me a usable frame" path:

* :func:`feature_collection_to_dataframe` — synchronous, one
  `getDownloadURL("CSV")` + `pandas.read_csv`. Suited to a single FC up
  to a few MB.
* :func:`feature_collections_to_dataframe` — the parallel variant: maps
  the above over an iterable using a thread pool, with a small retry
  budget around transient network errors. Returns one frame (column-
  concatenated).
* :func:`feature_collection_to_gdf` — synchronous, one `fc.getInfo()`
  → `GeoDataFrame`. Suited to small FCs (`getInfo` is rate-limited and
  capped at ~5000 features / ~10 MB).

The parallel helper retries on a fixed tuple of transient network
exceptions. The retry budget is `tries=5, backoff=2.0` (i.e. up to 5
attempts; on each failure wait then double the delay) — a deliberately
small budget. The original `gee_utils` retry was `tries=100`, which
masked real errors as transient. See N2 in `planning/gee-utils.md`.
"""

from __future__ import annotations

import ssl
import time
import urllib.error
from collections.abc import Callable, Iterable
from multiprocessing.dummy import Pool

import ee
import geopandas as gpd
import pandas as pd
import requests
import urllib3
from loguru import logger
from shapely.geometry import shape

# Network-layer exceptions that are worth a small number of retries.
# `googleapiclient.errors.HttpError` is only added when the optional
# package is importable (it ships transitively with `earthengine-api`).
_TRANSIENT_NETWORK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ssl.SSLEOFError,
    urllib.error.URLError,
    urllib3.exceptions.ProtocolError,
    urllib3.exceptions.SSLError,
    ConnectionResetError,
    ee.ee_exception.EEException,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

try:  # pragma: no cover - import side-effect, exercised in environments with the dep
    import googleapiclient.errors as _googleapiclient_errors

    _TRANSIENT_NETWORK_EXCEPTIONS = _TRANSIENT_NETWORK_EXCEPTIONS + (
        _googleapiclient_errors.HttpError,
    )
except ImportError:  # pragma: no cover
    pass


_DEFAULT_POOL_SIZE: int = 25
_DEFAULT_RETRIES: int = 5
_DEFAULT_BACKOFF: float = 2.0
_DEFAULT_INITIAL_DELAY: float = 1.0


def _retry_on_transient_errors(
    fn: Callable,
    *,
    tries: int = _DEFAULT_RETRIES,
    backoff: float = _DEFAULT_BACKOFF,
    initial_delay: float = _DEFAULT_INITIAL_DELAY,
    sleep: Callable[[float], None] | None = None,
    exceptions: tuple[type[BaseException], ...] = _TRANSIENT_NETWORK_EXCEPTIONS,
) -> Callable:
    """Wrap `fn` so it retries up to `tries` times on transient network errors.

    The first attempt waits `initial_delay` seconds after failing, then
    doubles (or multiplies by `backoff`) on each subsequent failure.
    After the final attempt the underlying exception is re-raised.

    Args:
        fn: The callable to wrap.
        tries: Maximum number of attempts (including the first).
            Defaults to 5.
        backoff: Multiplier applied to the delay after each failure.
            Defaults to 2.0.
        initial_delay: Seconds to wait after the first failure.
            Defaults to 1.0.
        sleep: Sleep implementation (injectable so tests run instantly).
        exceptions: Tuple of exception classes that trigger a retry.
            Anything else propagates immediately.

    Returns:
        A function with the same signature as `fn` whose call pattern
        is `attempt → sleep → attempt → sleep → ... → final attempt`.

    Raises:
        ValueError: If `tries < 1` (then no attempt would be made).
    """
    if tries < 1:
        raise ValueError(f"tries must be >= 1, got {tries}")

    def wrapper(*args, **kwargs):
        delay = initial_delay
        for attempt in range(1, tries + 1):
            try:
                return fn(*args, **kwargs)
            except exceptions as exc:
                if attempt == tries:
                    raise
                logger.warning(
                    f"{fn.__name__} attempt {attempt}/{tries} failed "
                    f"({type(exc).__name__}: {exc}); retrying in {delay:.1f}s"
                )
                # Resolve `sleep` lazily so tests can monkeypatch `io.time.sleep`.
                (sleep if sleep is not None else time.sleep)(delay)
                delay *= backoff

    return wrapper


def feature_collection_to_dataframe(
    fc: ee.FeatureCollection,
    selectors: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Download an `ee.FeatureCollection` to a `pandas.DataFrame` via CSV.

    Calls `fc.getDownloadURL(filetype="CSV", selectors=selectors)` and
    reads the resulting CSV with `pandas.read_csv`.

    The two `selectors` cases use a single `selectors is None`
    predicate (so an explicit `selectors=[]` is honoured verbatim,
    not collapsed into the default):

    * `selectors is None` — ask EE for everything, then drop the
      synthetic `system:index` and `.geo` columns.
    * `selectors is not None` (incl. `[]`) — forward verbatim to
      `getDownloadURL` and return whatever EE returns; no columns
      are dropped on the client side.

    Args:
        fc: The Earth Engine `FeatureCollection` to download.
        selectors: Optional iterable of property names to include.
            See semantic note above. Defaults to `None`.

    Returns:
        A `DataFrame` of the FC's features.

    Raises:
        Exceptions from the underlying Earth Engine / HTTP / CSV-parsing
        calls propagate verbatim.
    """
    if selectors is None:
        url = fc.getDownloadURL(filetype="CSV", selectors=None)
        df = pd.read_csv(url)
        return df.drop(columns=["system:index", ".geo"], errors="ignore")
    url = fc.getDownloadURL(filetype="CSV", selectors=list(selectors))
    return pd.read_csv(url)


def feature_collections_to_dataframe(
    feature_collections: Iterable[ee.FeatureCollection],
    *,
    pool_size: int = _DEFAULT_POOL_SIZE,
    tries: int = _DEFAULT_RETRIES,
    backoff: float = _DEFAULT_BACKOFF,
) -> pd.DataFrame:
    """Download many `ee.FeatureCollection`s in parallel and column-concat them.

    Each input FC is downloaded via :func:`feature_collection_to_dataframe`
    inside a `multiprocessing.dummy.Pool` (threads, not processes — Earth
    Engine's HTTP client is I/O-bound). Per-FC calls are retried on a
    fixed tuple of transient network errors with budget `tries=5,
    backoff=2.0`.

    Args:
        feature_collections: An iterable of `ee.FeatureCollection`s.
        pool_size: Number of worker threads. Defaults to 25.
        tries: Per-FC retry attempts (including the first). Defaults
            to 5.
        backoff: Multiplier applied to the inter-retry delay. Defaults
            to 2.0.

    Returns:
        A single `DataFrame` formed by `pd.concat(..., axis=1)` over
        the per-FC frames.

    Raises:
        ValueError: If `tries < 1` (propagated from
            :func:`_retry_on_transient_errors`).
    """
    fcs = list(feature_collections)
    if not fcs:
        return pd.DataFrame()
    retrying = _retry_on_transient_errors(
        feature_collection_to_dataframe, tries=tries, backoff=backoff,
    )
    with Pool(pool_size) as pool:
        frames = pool.map(retrying, fcs)
    return pd.concat(frames, axis=1)


def feature_collection_to_gdf(
    fc: ee.FeatureCollection, crs: int | str = 4326
) -> gpd.GeoDataFrame:
    """Convert an `ee.FeatureCollection` to a `GeoDataFrame` via `getInfo()`.

    Pulls the FC's GeoJSON-shaped dict with `fc.getInfo()`, parses each
    feature's geometry through `shapely.geometry.shape`, and returns a
    `GeoDataFrame` with the requested CRS. Note: `getInfo()` is
    rate-limited and capped at ~5000 features / ~10 MB — for larger
    collections, prefer :func:`feature_collection_to_dataframe` (and
    keep the geometry as a property if you need it).

    Args:
        fc: The Earth Engine `FeatureCollection` to convert.
        crs: Output CRS. An `int` is interpreted as an EPSG code
            (`4326` → `"EPSG:4326"`). A `str` is passed straight to
            `GeoDataFrame.crs`. Defaults to `4326`.

    Returns:
        A `GeoDataFrame` of the FC's features (one row per feature).
    """
    payload = fc.getInfo()
    rows = []
    for feature in payload.get("features", []):
        row = dict(feature.get("properties", {}))
        row["geometry"] = shape(feature["geometry"]) if feature.get("geometry") else None
        rows.append(row)
    crs_str = f"EPSG:{crs}" if isinstance(crs, int) else crs
    if not rows:
        return gpd.GeoDataFrame(geometry=[], crs=crs_str)
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs_str)
