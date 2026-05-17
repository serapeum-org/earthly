from __future__ import annotations

from pathlib import Path

import pytest


def pytest_collection_modifyitems(items):
    """Tag every test in this subtree with `@pytest.mark.gee`.

    Lets the suite be filtered with `-m gee` and lets the
    `test-gee` pixi task / GitHub workflow step run only the
    GEE backend's tests.

    Pytest delivers the FULL item list to every conftest hook,
    not just items from this subtree, so we filter by path.
    """
    here = Path(__file__).parent.resolve()
    for item in items:
        try:
            if Path(item.fspath).resolve().is_relative_to(here):
                item.add_marker(pytest.mark.gee)
        except (OSError, ValueError):
            continue


@pytest.fixture(autouse=True)
def _clear_gee_module_caches():
    """Reset every process-wide GEE cache between tests.

    `_EXTENT_CACHE` (in `earthlens.gee.backend`) is module-level so
    repeated `GEE(...)` constructions against the same asset don't
    re-issue the 2-5 s `reduceColumns(minMax)` round trip — see L5 in
    `planning/pr-diff-review-feat-gee-2026-05-17-2.md`. Without this
    clear, a test that mocks `_discover_ee_extent` and counts calls
    would see a stale hit from a previous test. We clear *before* the
    test runs (so any caller seeing the cache sees an empty one).
    """
    from earthlens.gee.backend import clear_extent_cache

    clear_extent_cache()
    yield
