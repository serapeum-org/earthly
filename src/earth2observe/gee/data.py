from __future__ import annotations

import os

import pandas as pd
from pandas import DataFrame

from earth2observe.gee import __path__


def getCatalog() -> DataFrame:
    """get_catalog.

        get_catalog retrieves the dataset catalog

    Returns
    -------
    DataFrame
    """
    return pd.read_json(os.path.join(__path__[0], "dataset_catalog.json"))
