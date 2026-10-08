from __future__ import annotations

from .model import MapModel


def norm(s: str) -> str:
    """Normalize path separators to forward slashes."""
    return s.replace("\\", "/")


def _within(path: str, directory: str) -> str | None:
    """The part of ``path`` below ``directory`` (``""`` when it is the directory itself), or None
    when the path is not inside it. Both are separator-normalized; ``directory`` may appear at
    the start of a relative path or anywhere inside an absolute one."""
    if not directory:
        return None
    p = norm(path)
    d = norm(directory).strip("/")
    if p == d or p.startswith(d + "/"):
        return p[len(d) :].lstrip("/")
    idx = p.find("/" + d + "/")
    if idx >= 0:
        return p[idx + len(d) + 2 :]
    return None


def slice_of(path: str, m: MapModel) -> str | None:
    """Which slice a path belongs to, or None: a file under the slice's folder, or the slice's
    HTTP edge ``<routes_dir>/<slice>.py``."""
    rest = _within(path, m.slices_dir)
    if rest:
        seg = rest.split("/", 1)[0]
        if seg:
            return seg
    return route_slice_of(path, m)


def route_slice_of(path: str, m: MapModel) -> str | None:
    """``<routes_dir>/<slice>.py`` -> ``<slice>`` when the map knows that slice, else None."""
    rest = _within(path, m.routes_dir)
    if not rest or "/" in rest or not rest.endswith(".py"):
        return None
    name = rest[: -len(".py")]
    return name if name in m.slices else None


def is_route_path(path: str, m: MapModel) -> bool:
    return route_slice_of(path, m) is not None


def is_kernel_path(path: str, m: MapModel) -> bool:
    if m.kernel_dir:
        return _within(path, m.kernel_dir) is not None
    return bool(m.kernel) and f"/{m.kernel}/" in f"/{norm(path)}"


def app_of(path: str, m: MapModel) -> str | None:
    """The application package when the path lives under it (composition root, entry points, the
    HTTP edge …), or None. Callers check slices and the kernel first: in a nested layout they
    live inside the application package."""
    return m.app_dir if _within(path, m.app_dir) is not None else None


def shared_of(path: str, m: MapModel) -> str | None:
    """Which shared (non-kernel, non-slice) package a path lives under, or None."""
    p = norm(path)
    for name in m.shared:
        if f"/{name}/" in p or p.startswith(name + "/"):
            return name
    return None


def is_contract_path(path: str) -> bool:
    return "/contract/" in norm(path).lower()
