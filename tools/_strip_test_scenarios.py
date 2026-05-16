"""One-off migration: strip ``Test scenario:`` blocks from test docstrings (M1).

Walks every ``tests/**/test_*.py`` and rewrites each function-level
docstring whose body contains a ``Test scenario:`` subheader into one
short plain-prose sentence. The rewrite:

* keeps the summary lines (everything before the first blank line),
* drops the blank line + ``Test scenario:`` line + the entire indented
  body that follows,
* converts rST double-backtick spans `` ``foo`` `` to single-backtick
  Markdown `` `foo` `` inside the remaining summary (CLAUDE.md style),
* collapses single-line summaries onto one line as
  ``\"\"\"Summary.\"\"\"``,
* preserves multi-line summaries verbatim, with the closing ``\"\"\"``
  re-indented to match the function indent.

Idempotent — docstrings without ``Test scenario:`` are left alone.
Run from the repo root::

    pixi run -e dev python tools/_strip_test_scenarios.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

TESTS_ROOT = Path("tests")

_DOUBLE_BT = re.compile(r"``([^`]+)``")
_SCENARIO_HEADER = re.compile(r"^[ \t]*Test scenario:\s*$", re.MULTILINE)


def _replace_double_backticks(text: str) -> str:
    """rST `` ``x`` `` -> Markdown `` `x` ``."""
    return _DOUBLE_BT.sub(r"`\1`", text)


def _summary_from_docstring(body: str) -> str:
    """Return the summary portion of `body` (everything before the first blank line).

    `body` is the raw docstring content (without the surrounding triple
    quotes). Each line preserves its original indentation.
    """
    lines = body.split("\n")
    summary_lines: list[str] = []
    for line in lines:
        if line.strip() == "":
            break
        summary_lines.append(line)
    return "\n".join(summary_lines)


def _rebuild_docstring(summary: str, indent: str) -> str:
    """Rebuild the triple-quoted docstring literal from the summary text.

    Args:
        summary: The summary part of the original docstring (without
            the surrounding `\"\"\"`). May be one line or several.
        indent: The whitespace prefix at the start of the function body
            (matches the `def` line's leading whitespace + 4 spaces),
            used when re-emitting a multi-line docstring's closing
            `\"\"\"`.
    """
    summary = _replace_double_backticks(summary)
    if "\n" not in summary:
        return f'"""{summary.strip()}"""'
    lines = [ln.rstrip() for ln in summary.split("\n")]
    # First line stays attached to the opening `"""`.
    body = "\n".join(lines)
    return f'"""{body}\n{indent}"""'


def _rewrite_file(path: Path) -> int:
    """Rewrite `path`. Returns the number of docstrings modified."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        print(f"  ! {path}: parse error: {exc}", file=sys.stderr)
        return 0

    # Collect (start_lineno, start_col, end_lineno, end_col, raw_body) for
    # every function-level docstring carrying a "Test scenario:" block.
    targets: list[tuple[int, int, int, int, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        val = first.value
        if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
            continue
        ds = val.value
        if "Test scenario:" not in ds:
            continue
        targets.append((val.lineno, val.col_offset, val.end_lineno, val.end_col_offset, ds, node.col_offset))

    if not targets:
        return 0

    # Apply replacements bottom-up so earlier offsets stay valid.
    lines = source.split("\n")
    targets.sort(reverse=True)
    modified = 0
    for start_ln, start_col, end_ln, end_col, ds, fn_col in targets:
        summary = _summary_from_docstring(ds)
        if not summary.strip():
            continue
        indent = " " * (fn_col + 4)
        new_literal = _rebuild_docstring(summary, indent)
        # Splice the new literal into the source.
        # ast lines are 1-indexed.
        before = "\n".join(lines[: start_ln - 1])
        after = "\n".join(lines[end_ln:])
        head = lines[start_ln - 1][:start_col]
        tail = lines[end_ln - 1][end_col:]
        replaced_block = head + new_literal + tail
        new_source = "\n".join(filter(None, [before, replaced_block, after]))
        # Re-split for the next iteration (targets processed bottom-up).
        lines = new_source.split("\n")
        modified += 1

    new_source = "\n".join(lines)
    if not new_source.endswith("\n"):
        new_source += "\n"
    if new_source != source:
        path.write_text(new_source, encoding="utf-8")
    return modified


def main() -> int:
    files = sorted(TESTS_ROOT.rglob("test_*.py"))
    total = 0
    for path in files:
        n = _rewrite_file(path)
        if n:
            print(f"  {n:4d}  {path}")
            total += n
    print(f"rewrote {total} docstrings across {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
