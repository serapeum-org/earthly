"""Execute a notebook in-place with a long per-cell timeout.

Bypasses ``nbconvert`` (which the docs config wires up to optional
extensions) by calling ``nbclient`` directly. Each notebook is run
in the working directory of its parent so relative ``data/...`` paths
behave the way the user would see them in JupyterLab.

Usage::

    pixi run -e dev python tools/docs/run_notebook.py docs/examples/cds_quickstart.ipynb
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import nbformat
from nbclient import NotebookClient


def run(path: Path, timeout: int) -> int:
    nb = nbformat.read(path, as_version=4)
    client = NotebookClient(
        nb,
        timeout=timeout,
        kernel_name="python3",
        resources={"metadata": {"path": str(path.parent)}},
        allow_errors=False,
    )
    try:
        client.execute()
        nbformat.write(nb, path)
        print(f"OK   {path}")
        return 0
    except Exception as exc:
        nbformat.write(nb, path)
        print(f"FAIL {path}: {type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("notebook", type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    return run(args.notebook, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
