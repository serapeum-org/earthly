"""Inject the bulk-add YAML blocks into ``cds_data_catalog.yaml``.

For each gated dataset, find the closing line of its ``variables:``
section and inject the generated rows before the next dataset
header. Idempotent: skips rows whose YAML key already exists.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml
from bulk_add_remaining import EXTRA_MAPPING, build_known

from earthly.ecmwf.constraints import fetch_constraints

CATALOG_PATH = Path("src/earthly/ecmwf/cds_data_catalog.yaml")

LEVEL_DEFAULTS = {
    "height_levels": {"height_level": ["100_m"]},
    "pressure_levels": {"pressure_level": ["1000"]},
    "model_levels": {"model_level": ["1"]},
    "single_levels": {},
}

LEVEL_SUFFIX = {
    "height_levels": "-h",
    "pressure_levels": "-p",
    "model_levels": "-m",
    "single_levels": "",
}


def slug(name: str) -> str:
    return name.lower().replace("_", "-").replace(",", "").replace(" ", "-")


def lookup(cv: str, known) -> tuple[str, str, str] | None:
    return known.get(cv) or EXTRA_MAPPING.get(cv)


def emit(suffix, cv, nv, un, ty, extras=None):
    key = f"{slug(cv)}{suffix}"
    out = [
        f'      "{key}":',
        f"        cds_variable: {cv}",
        f"        nc_variable: {nv}",
        f"        types: {ty}",
        f'        units: "{un}"',
    ]
    if extras:
        out.append("        extras:")
        for k, v in extras.items():
            if isinstance(v, list):
                out.append(f"          {k}: {v}")
            elif isinstance(v, str):
                out.append(f'          {k}: "{v}"')
            else:
                out.append(f"          {k}: {v}")
    return "\n".join(out) + "\n\n"


def gen_pan_carra(known, ds, existing_keys):
    constraints = fetch_constraints(ds)
    per_lt: dict[str, set[str]] = {}
    for entry in constraints:
        for lt in entry.get("level_type", []):
            per_lt.setdefault(lt, set()).update(entry.get("variable", []))
    out = []
    suffix_base = "-pancarra-means" if "means" in ds else "-pancarra"
    for lt in sorted(per_lt):
        for cv in sorted(per_lt[lt]):
            m = lookup(cv, known)
            if not m:
                continue
            nv, un, ty = m
            suffix = f"{suffix_base}{LEVEL_SUFFIX[lt]}"
            key = f"{slug(cv)}{suffix}"
            if key in existing_keys:
                continue
            extras = {"level_type": lt}
            extras.update(LEVEL_DEFAULTS[lt])
            out.append(emit(suffix, cv, nv, un, ty, extras))
            existing_keys.add(key)
    return "".join(out)


def gen_carra_means(known, existing_keys):
    constraints = fetch_constraints("reanalysis-carra-means")
    combos: dict[tuple, set[str]] = {}
    for entry in constraints:
        for lt in entry.get("level_type", []):
            for pt in entry.get("product_type", []):
                combos.setdefault((lt, pt), set()).update(entry.get("variable", []))
    out = []
    for lt, pt in sorted(combos):
        for cv in sorted(combos[(lt, pt)]):
            m = lookup(cv, known)
            if not m:
                continue
            nv, un, ty = m
            level_suffix = LEVEL_SUFFIX.get(lt, "")
            pt_suffix = "" if pt == "forecast_based" else f"-{pt.replace('_', '')}"
            suffix = f"-carra-means{level_suffix}{pt_suffix}"
            key = f"{slug(cv)}{suffix}"
            if key in existing_keys:
                continue
            extras = {"level_type": lt, "product_type": [pt]}
            extras.update(LEVEL_DEFAULTS.get(lt, {}))
            out.append(emit(suffix, cv, nv, un, ty, extras))
            existing_keys.add(key)
    return "".join(out)


def gen_sibling(ds, known, existing_keys):
    constraints = fetch_constraints(ds)
    all_vars = set()
    for e in constraints:
        all_vars.update(e.get("variable", []))
    suffix_map = {
        "seasonal-monthly-pressure-levels": "-seasonal-p",
        "seasonal-original-single-levels": "-seasonal-orig",
        "seasonal-original-pressure-levels": "-seasonal-orig-p",
        "seasonal-postprocessed-single-levels": "-seasonal-pp",
        "seasonal-postprocessed-pressure-levels": "-seasonal-pp-p",
        "seasonal-monthly-ocean": "-seasonal-ocean",
    }
    suffix = suffix_map.get(ds, "")
    out = []
    for cv in sorted(all_vars):
        m = lookup(cv, known)
        if not m:
            continue
        nv, un, ty = m
        key = f"{slug(cv)}{suffix}"
        if key in existing_keys:
            continue
        out.append(emit(suffix, cv, nv, un, ty))
        existing_keys.add(key)
    return "".join(out)


def find_block_end(text: str, ds_name: str) -> int:
    """Return the line index right BEFORE the next dataset header
    (or end of file) — i.e. the position to inject the new rows."""
    lines = text.split("\n")
    in_block = False
    for i, line in enumerate(lines):
        if line.startswith(f"  {ds_name}:"):
            in_block = True
            continue
        if in_block and re.match(r"^  [a-zA-Z]", line) and ":" in line:
            # Found next dataset
            return i
    return len(lines)


def inject_block(text: str, ds_name: str, new_yaml: str) -> str:
    if not new_yaml.strip():
        return text
    lines = text.split("\n")
    end_idx = find_block_end(text, ds_name)
    # Trim trailing blank lines from the existing block
    while end_idx > 0 and lines[end_idx - 1].strip() == "":
        end_idx -= 1
    new_lines = new_yaml.rstrip("\n").split("\n")
    return "\n".join(lines[:end_idx] + [""] + new_lines + [""] + lines[end_idx:])


def main() -> int:
    catalog = yaml.safe_load(CATALOG_PATH.read_text())
    known = build_known(catalog)
    text = CATALOG_PATH.read_text()

    # Build inserts per dataset
    targets = [
        (
            "reanalysis-pan-carra",
            lambda keys: gen_pan_carra(known, "reanalysis-pan-carra", keys),
        ),
        (
            "reanalysis-pan-carra-means",
            lambda keys: gen_pan_carra(known, "reanalysis-pan-carra-means", keys),
        ),
        ("reanalysis-carra-means", lambda keys: gen_carra_means(known, keys)),
        (
            "seasonal-monthly-pressure-levels",
            lambda keys: gen_sibling("seasonal-monthly-pressure-levels", known, keys),
        ),
        (
            "seasonal-original-single-levels",
            lambda keys: gen_sibling("seasonal-original-single-levels", known, keys),
        ),
        (
            "seasonal-original-pressure-levels",
            lambda keys: gen_sibling("seasonal-original-pressure-levels", known, keys),
        ),
        (
            "seasonal-postprocessed-single-levels",
            lambda keys: gen_sibling(
                "seasonal-postprocessed-single-levels", known, keys
            ),
        ),
        (
            "seasonal-postprocessed-pressure-levels",
            lambda keys: gen_sibling(
                "seasonal-postprocessed-pressure-levels", known, keys
            ),
        ),
        (
            "seasonal-monthly-ocean",
            lambda keys: gen_sibling("seasonal-monthly-ocean", known, keys),
        ),
    ]

    for ds_name, gen_fn in targets:
        block = catalog["datasets"].get(ds_name, {})
        existing_keys = set(block.get("variables", {}).keys())
        new_yaml = gen_fn(existing_keys)
        added = new_yaml.count('cds_variable:')
        print(f"  {ds_name}: +{added} rows")
        if new_yaml.strip():
            text = inject_block(text, ds_name, new_yaml)

    CATALOG_PATH.write_text(text)
    print("Written.")
    # Verify parse
    yaml.safe_load(text)
    print("YAML parses OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
