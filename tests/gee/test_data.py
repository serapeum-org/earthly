from typing import List

from pandas import DataFrame

from earthly.gee.data import getCatalog


def test_get_catalog(catalog_columns: List[str]):
    catalog = getCatalog()

    assert isinstance(catalog, DataFrame)
    assert all(col in catalog_columns for col in catalog.columns.to_list())
