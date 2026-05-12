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
        """`_StrictSafeLoader` extends `yaml.SafeLoader` (no arbitrary objects).

        Test scenario:
            ``issubclass(_StrictSafeLoader, yaml.SafeLoader)`` is True.
        """
        assert issubclass(_StrictSafeLoader, yaml.SafeLoader)


class TestLoadYamlStrict:
    """Tests for `load_yaml_strict`."""

    def test_parses_mapping(self, tmp_path):
        """A simple mapping is returned as a dict.

        Test scenario:
            ``name: demo`` + a list value parses to the expected nested dict.
        """
        path = _write(tmp_path, "ok.yaml", """\
            name: demo
            items:
              - a
              - b
        """)
        data = load_yaml_strict(path)
        assert data == {"name": "demo", "items": ["a", "b"]}

    def test_empty_file_returns_none(self, tmp_path):
        """An empty file parses to ``None``.

        Test scenario:
            ``load_yaml_strict`` on a zero-byte file returns ``None``.
        """
        path = _write(tmp_path, "empty.yaml", "")
        assert load_yaml_strict(path) is None

    def test_accepts_string_path(self, tmp_path):
        """A ``str`` path works as well as a ``Path``.

        Test scenario:
            Passing ``str(path)`` returns the same parsed content.
        """
        path = _write(tmp_path, "ok.yaml", "key: value\n")
        assert load_yaml_strict(str(path)) == {"key": "value"}

    def test_nested_mappings(self, tmp_path):
        """Nested mappings are parsed recursively.

        Test scenario:
            A two-level mapping parses to nested dicts.
        """
        path = _write(tmp_path, "nested.yaml", """\
            outer:
              inner:
                leaf: 1
        """)
        assert load_yaml_strict(path) == {"outer": {"inner": {"leaf": 1}}}

    def test_duplicate_top_level_key_rejected(self, tmp_path):
        """A duplicated top-level key raises ``ValueError`` with the line number.

        Test scenario:
            ``a: 1`` then ``a: 2`` → ``ValueError`` mentioning "duplicate YAML key"
            and "line 2".
        """
        path = _write(tmp_path, "dup.yaml", "a: 1\na: 2\n")
        with pytest.raises(ValueError, match=r"duplicate YAML key 'a' at line 2"):
            load_yaml_strict(path)

    def test_duplicate_nested_key_rejected(self, tmp_path):
        """A duplicated key inside a nested mapping is also rejected.

        Test scenario:
            ``outer.x`` declared twice → ``ValueError`` mentioning "duplicate YAML key".
        """
        path = _write(tmp_path, "dup_nested.yaml", """\
            outer:
              x: 1
              x: 2
        """)
        with pytest.raises(ValueError, match="duplicate YAML key 'x'"):
            load_yaml_strict(path)

    def test_list_of_mappings(self, tmp_path):
        """A list of mappings parses correctly (no false duplicates across items).

        Test scenario:
            Two list items each with a ``name:`` key parse to a 2-element list.
        """
        path = _write(tmp_path, "list.yaml", """\
            items:
              - name: a
                v: 1
              - name: b
                v: 2
        """)
        assert load_yaml_strict(path) == {"items": [{"name": "a", "v": 1},
                                                    {"name": "b", "v": 2}]}
