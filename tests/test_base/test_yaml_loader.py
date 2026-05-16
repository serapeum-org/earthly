"""Tests for `earthlens.base.yaml_loader` — strict YAML loading."""

from __future__ import annotations

import textwrap

import pytest
import yaml

from earthlens.base.yaml_loader import _StrictSafeLoader, load_yaml_strict


def _write(tmp_path, name, body):
    """Write dedented `body` to `tmp_path/name` and return the path."""
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return path


class TestStrictSafeLoader:
    """Tests for the `_StrictSafeLoader` class itself."""

    def test_is_safe_loader_subclass(self):
        """`_StrictSafeLoader` extends `yaml.SafeLoader` (no arbitrary objects)."""
        assert issubclass(_StrictSafeLoader, yaml.SafeLoader)


class TestLoadYamlStrict:
    """Tests for `load_yaml_strict`."""

    def test_parses_mapping(self, tmp_path):
        """A simple mapping is returned as a dict."""
        path = _write(tmp_path, "ok.yaml", """\
            name: demo
            items:
              - a
              - b
        """)
        data = load_yaml_strict(path)
        assert data == {"name": "demo", "items": ["a", "b"]}

    def test_empty_file_returns_none(self, tmp_path):
        """An empty file parses to `None`."""
        path = _write(tmp_path, "empty.yaml", "")
        assert load_yaml_strict(path) is None

    def test_accepts_string_path(self, tmp_path):
        """A `str` path works as well as a `Path`."""
        path = _write(tmp_path, "ok.yaml", "key: value\n")
        assert load_yaml_strict(str(path)) == {"key": "value"}

    def test_nested_mappings(self, tmp_path):
        """Nested mappings are parsed recursively."""
        path = _write(tmp_path, "nested.yaml", """\
            outer:
              inner:
                leaf: 1
        """)
        assert load_yaml_strict(path) == {"outer": {"inner": {"leaf": 1}}}

    def test_duplicate_top_level_key_rejected(self, tmp_path):
        """A duplicated top-level key raises `ValueError` with the line number."""
        path = _write(tmp_path, "dup.yaml", "a: 1\na: 2\n")
        with pytest.raises(ValueError, match=r"duplicate YAML key 'a' at line 2"):
            load_yaml_strict(path)

    def test_duplicate_nested_key_rejected(self, tmp_path):
        """A duplicated key inside a nested mapping is also rejected."""
        path = _write(tmp_path, "dup_nested.yaml", """\
            outer:
              x: 1
              x: 2
        """)
        with pytest.raises(ValueError, match="duplicate YAML key 'x'"):
            load_yaml_strict(path)

    def test_list_of_mappings(self, tmp_path):
        """A list of mappings parses correctly (no false duplicates across items)."""
        path = _write(tmp_path, "list.yaml", """\
            items:
              - name: a
                v: 1
              - name: b
                v: 2
        """)
        assert load_yaml_strict(path) == {"items": [{"name": "a", "v": 1},
                                                    {"name": "b", "v": 2}]}
