"""Shared `Provider` value object + registry loader.

Each backend catalog ships its own `providers.yaml` enumerating the
canonical publishers/organisations its datasets attribute to. This
module holds the per-backend-agnostic pieces — the `Provider` pydantic
value object and the cached YAML loader — so the three backends (GEE,
ECMWF, CHC) all reach the same shape (L2 in
`planning/catalog-cross-backend-comparison.md`).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from earthlens.base.yaml_loader import load_yaml_strict


class Provider(BaseModel):
    """One canonical data provider — a slug-id with a display name and parent.

    Frozen value object loaded from a backend's `providers.yaml`.
    Datasets reference providers by slug via their `provider:` field;
    the catalog loader validates that every referenced slug is
    registered.

    Attributes:
        slug: Stable kebab-case identifier (e.g. `"nasa-lp-daac"`,
            `"copernicus-marine"`, `"ucsb-chc"`); injected from the
            YAML mapping key.
        display_name: Human-readable name to render in docs and UIs.
        parent: Slug of the parent provider, or `None` for top-level
            organisations. Used to group e.g. all NASA DAACs under
            the `"nasa"` umbrella.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    slug: str
    display_name: str
    parent: str | None = None


_PROVIDERS_CACHE: dict[tuple[str, int], dict[str, Provider]] = {}


def clear_providers_cache() -> None:
    """Empty the module-level providers parse cache (test helper)."""
    _PROVIDERS_CACHE.clear()


def load_providers(path: Path) -> dict[str, Provider]:
    """Parse + cache `providers.yaml` at `path`, keyed on `(path, mtime_ns)`.

    Args:
        path: Filesystem path of a `providers.yaml`-shaped file (a
            top-level `providers:` map of `slug -> {display_name,
            parent?}`).

    Returns:
        `slug -> Provider` mapping.

    Raises:
        ValueError: If the file is missing, declares a slug whose
            `parent` is not itself a registered slug, or fails
            pydantic validation on any entry.
    """
    resolved = str(path.resolve())
    try:
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError as exc:
        raise ValueError(
            f"providers registry not found at {path}; L2 (provider "
            "normalisation) expects this file alongside the catalog."
        ) from exc
    key = (resolved, mtime_ns)
    cached = _PROVIDERS_CACHE.get(key)
    if cached is not None:
        return cached

    data = load_yaml_strict(path) or {}
    raw = data.get("providers") or {}
    out: dict[str, Provider] = {}
    for slug, body in raw.items():
        try:
            out[slug] = Provider(slug=slug, **dict(body or {}))
        except ValidationError as exc:
            raise ValueError(f"invalid provider {slug!r} in {path}: {exc}") from exc
    for slug, p in out.items():
        if p.parent is not None and p.parent not in out:
            raise ValueError(
                f"provider {slug!r} declares parent={p.parent!r}, "
                f"which is not a known provider slug in {path}"
            )
    _PROVIDERS_CACHE[key] = out
    return out
