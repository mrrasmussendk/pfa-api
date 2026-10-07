from __future__ import annotations

from .model import MapModel


def norm(s: str) -> str:
    """Normalize path separators to forward slashes."""
    return s.replace("\\", "/")


def slice_of(path: str, m: MapModel) -> str | None:
    """Which slice folder a path lives under, or None. Separator-normalized so classification
    works regardless of whether the map's slices_dir or the event path use '/' or '\\'."""
    sd = norm(m.slices_dir)
    if not sd:
        return None
    p = norm(path)
    idx = p.find(sd)
    if idx < 0:
        return None
    rest = p[idx + len(sd) :].lstrip("/")
    seg = rest.split("/", 1)[0]
    return seg or None


def shared_of(path: str, m: MapModel) -> str | None:
    """Which shared (non-kernel, non-slice) package a path lives under, or None."""
    p = norm(path)
    for name in m.shared:
        if f"/{name}/" in p or p.startswith(name + "/"):
            return name
    return None


def is_contract_path(path: str) -> bool:
    return "/contract/" in norm(path).lower()
