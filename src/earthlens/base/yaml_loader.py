"""Strict YAML loading shared by the package's variable/data catalogs.

The catalogs under `earthlens` (`cds_data_catalog.yaml` for the ECMWF
backend, `gee_data_catalog.yaml` for the GEE backend, ...) are
hand-maintained config-as-code. PyYAML's default `SafeLoader` silently
merges duplicate mapping keys (last one wins), which would let a
copy-paste typo — two identical variable/band codes under the same
dataset — slip through with the first silently shadowed. This module
provides a loader that fails loud at parse time with a `ValueError`
naming the offending line, plus a small `load_yaml_strict` helper, so
every catalog gets the same guarantee from one place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class _StrictSafeLoader(yaml.SafeLoader):
    """:class:`yaml.SafeLoader` that rejects duplicate keys in any mapping.

    Behaves like `SafeLoader` (no arbitrary object instantiation) except
    that a mapping declaring the same key twice raises a `ValueError`
    pinpointing the line/column rather than silently keeping the last
    value.
    """


def _construct_mapping_no_duplicates(
    loader: _StrictSafeLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    """Build a dict from a YAML mapping node, rejecting duplicate keys.

    Replaces :meth:`yaml.SafeLoader.construct_mapping` for
    :class:`_StrictSafeLoader` so every mapping in a catalog YAML (the
    dataset map, each dataset's `variables:` / `bands:` block, every
    `extras:` map, ...) is required to have unique keys.

    Args:
        loader: The active strict loader instance.
        node: The YAML mapping node being constructed.
        deep: Whether to construct child nodes eagerly (passed through
            to :meth:`yaml.Loader.construct_object`).

    Returns:
        The mapping as a plain `dict`.

    Raises:
        ValueError: If the same key appears more than once in the
            mapping; the message includes the line/column of the
            duplicate.
    """
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            mark = key_node.start_mark
            raise ValueError(
                f"duplicate YAML key {key!r} at line {mark.line + 1}, "
                f"column {mark.column + 1} of {mark.name}: every key in a "
                "YAML mapping must be unique (in particular, every variable "
                "or band code must be unique within its dataset's block)"
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_no_duplicates,
)


def load_yaml_strict(path: str | Path) -> Any:
    """Parse a YAML file, rejecting duplicate mapping keys.

    A thin wrapper over `yaml.load(..., Loader=_StrictSafeLoader)` so
    callers (the catalog loaders) never touch the loader class directly.

    Args:
        path: Filesystem path to the YAML file.

    Returns:
        The parsed YAML (typically a `dict`), or `None` for an empty
        file.

    Raises:
        ValueError: If any mapping in the file declares a key twice.
    """
    with open(path, encoding="utf-8") as stream:
        # `_StrictSafeLoader` subclasses `yaml.SafeLoader` (no arbitrary
        # object instantiation); bandit's B506 flags any `yaml.load`.
        return yaml.load(stream, Loader=_StrictSafeLoader)  # nosec B506
