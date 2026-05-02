"""Apply bulk-add: for each gated dataset, append YAML rows for the
missing vars discovered by ``bulk_add_remaining`` and write them
inline into ``cds_data_catalog.yaml``.

For datasets gated by ``level_type`` / ``product_type`` /
``time_aggregation``, this emits one row per (var × gating-combo)
with per-row ``extras`` overrides.

Skips vars already present in the dataset (by ``cds_variable``).
Idempotent — safe to re-run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bulk_add_remaining import EXTRA_MAPPING, build_known
from earth2observe.ecmwf.constraints import fetch_constraints

CATALOG_PATH = Path("src/earth2observe/ecmwf/cds_data_catalog.yaml")

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


def emit_var(
    suffix_full: str,
    cv: str,
    nv: str,
    un: str,
    ty: str,
    extras: dict | None = None,
) -> str:
    key = f"{slug(cv)}{suffix_full}"
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
    return "\n".join(out) + "\n"


def collect_existing_keys(catalog: dict, ds: str) -> set[str]:
    block = catalog["datasets"].get(ds, {})
    return set(block.get("variables", {}).keys())


def collect_existing_cds(catalog: dict, ds: str) -> set[str]:
    block = catalog["datasets"].get(ds, {})
    return {
        v.get("cds_variable")
        for v in block.get("variables", {}).values()
        if isinstance(v, dict) and "cds_variable" in v
    }


def lookup(cv: str, known: dict) -> tuple[str, str, str] | None:
    return known.get(cv) or EXTRA_MAPPING.get(cv)


def gen_pan_carra(catalog, known) -> tuple[str, list[str]]:
    """For pan-carra and pan-carra-means: enumerate per level_type."""
    blocks = []
    skipped = []
    for ds in ("reanalysis-pan-carra", "reanalysis-pan-carra-means"):
        # Collect (level_type, var) pairs from constraints
        constraints = fetch_constraints(ds)
        per_lt: dict[str, set[str]] = {}
        for entry in constraints:
            lts = entry.get("level_type", [])
            for lt in lts:
                per_lt.setdefault(lt, set()).update(entry.get("variable", []))
        existing_keys = collect_existing_keys(catalog, ds)
        ds_block = []
        ds_block.append(f"\n  # ===== {ds} bulk-fill =====\n")
        ds_block.append(f"# (insert into reanalysis-pan-carra block)\n")
        for lt in sorted(per_lt):
            for cv in sorted(per_lt[lt]):
                m = lookup(cv, known)
                if m is None:
                    skipped.append(f"{ds}/{lt}/{cv}")
                    continue
                nv, un, ty = m
                # Compute key
                suffix_base = "-pancarra-means" if "means" in ds else "-pancarra"
                if "means" in ds:
                    suffix = f"{suffix_base}{LEVEL_SUFFIX[lt]}"
                else:
                    suffix = f"{suffix_base}{LEVEL_SUFFIX[lt]}"
                key = f"{slug(cv)}{suffix}"
                if key in existing_keys:
                    continue
                # Build extras with level_type override + level coordinate
                extras = {"level_type": lt}
                extras.update(LEVEL_DEFAULTS[lt])
                ds_block.append(emit_var(suffix, cv, nv, un, ty, extras))
        blocks.append((ds, "".join(ds_block)))
    return blocks, skipped


def gen_carra_means(catalog, known):
    """For carra-means: enumerate per (level_type, product_type)."""
    ds = "reanalysis-carra-means"
    constraints = fetch_constraints(ds)
    # Group: (level_type, product_type) -> {cds_var}
    combos: dict[tuple, set[str]] = {}
    for entry in constraints:
        for lt in entry.get("level_type", []):
            for pt in entry.get("product_type", []):
                combos.setdefault((lt, pt), set()).update(
                    entry.get("variable", [])
                )
    existing_keys = collect_existing_keys(catalog, ds)
    out = []
    skipped = []
    out.append(f"\n  # ===== {ds} bulk-fill =====\n")
    out.append(f"# (insert into reanalysis-carra-means block)\n")
    for (lt, pt) in sorted(combos):
        for cv in sorted(combos[(lt, pt)]):
            m = lookup(cv, known)
            if m is None:
                skipped.append(f"{ds}/{lt}/{pt}/{cv}")
                continue
            nv, un, ty = m
            # suffix: -carra-means + level-suffix + analysis_based variant
            level_suffix = LEVEL_SUFFIX.get(lt, "")
            pt_suffix = "" if pt == "forecast_based" else f"-{pt.replace('_','')}"
            suffix = f"-carra-means{level_suffix}{pt_suffix}"
            key = f"{slug(cv)}{suffix}"
            if key in existing_keys:
                continue
            extras = {"level_type": lt, "product_type": [pt]}
            extras.update(LEVEL_DEFAULTS.get(lt, {}))
            out.append(emit_var(suffix, cv, nv, un, ty, extras))
    return ds, "".join(out), skipped


def gen_seasonal_sibling(ds, catalog, known):
    """For seasonal siblings: each is its own dataset, simple append."""
    constraints = fetch_constraints(ds)
    all_vars = set()
    for entry in constraints:
        all_vars.update(entry.get("variable", []))
    in_cat = collect_existing_cds(catalog, ds)
    missing = sorted(all_vars - in_cat)
    out = []
    skipped = []
    out.append(f"\n  # ===== {ds} bulk-fill =====\n")
    out.append(f"# (insert into {ds} block)\n")
    suffix_map = {
        "seasonal-monthly-pressure-levels": "-seasonal-p",
        "seasonal-original-single-levels": "-seasonal-orig",
        "seasonal-original-pressure-levels": "-seasonal-orig-p",
        "seasonal-postprocessed-single-levels": "-seasonal-pp",
        "seasonal-postprocessed-pressure-levels": "-seasonal-pp-p",
        "seasonal-monthly-ocean": "-seasonal-ocean",
    }
    suffix = suffix_map.get(ds, "")
    for cv in missing:
        m = lookup(cv, known)
        if m is None:
            skipped.append(f"{ds}/{cv}")
            continue
        nv, un, ty = m
        out.append(emit_var(suffix, cv, nv, un, ty))
    return ds, "".join(out), skipped


def main() -> int:
    catalog = yaml.safe_load(CATALOG_PATH.read_text())
    known = build_known(catalog)
    print(f"Catalog known: {len(known)}, EXTRA_MAPPING: {len(EXTRA_MAPPING)}")

    out = []
    skipped_all = []

    # PAN-CARRA family
    pan_blocks, sk = gen_pan_carra(catalog, known)
    skipped_all.extend(sk)
    for name, content in pan_blocks:
        out.append(content)

    # CARRA-means
    name, content, sk = gen_carra_means(catalog, known)
    skipped_all.extend(sk)
    out.append(content)

    # Seasonal siblings
    for sib in [
        "seasonal-monthly-pressure-levels",
        "seasonal-original-single-levels",
        "seasonal-original-pressure-levels",
        "seasonal-postprocessed-single-levels",
        "seasonal-postprocessed-pressure-levels",
        "seasonal-monthly-ocean",
    ]:
        try:
            name, content, sk = gen_seasonal_sibling(sib, catalog, known)
            skipped_all.extend(sk)
            out.append(content)
        except Exception as e:
            print(f"err {sib}: {e}")

    Path("tools/_bulk_output.yaml").write_text("".join(out))
    print(f"Wrote tools/_bulk_output.yaml ({len(''.join(out))} chars)")
    print(f"Skipped: {len(skipped_all)}")
    for s in skipped_all[:10]:
        print(f"  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
