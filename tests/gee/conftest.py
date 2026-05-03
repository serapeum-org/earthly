from pathlib import Path
from typing import List

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


@pytest.fixture(scope="module")
def catalog_columns() -> List[str]:
    return [
        "dataset",
        "name",
        "provider",
        "url",
        "bands",
        "band_describtion",
        "spatial_resolution",
        "temporal_resolution",
        "start_date",
        "end_date",
        "min",
        "max",
    ]
