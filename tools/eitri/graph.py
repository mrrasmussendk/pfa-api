"""EIT007 — the feature graph is acyclic. ``feature.json`` declares what a feature depends on; a cycle means
two features each rely on the other, so neither can be understood, changed or budgeted alone, and the
dependency direction the walls assume no longer exists."""

from __future__ import annotations

from .core import EIT007, Diagnostic
from .project import Slice


def _cycle_from(start: str, graph: dict[str, tuple[str, ...]], path: list[str], done: set[str]) -> list[str] | None:
    """The first cycle reachable from ``start``, as the features along it with the first repeated at the end."""
    if start in path:
        return [*path[path.index(start) :], start]
    if start in done:
        return None
    path.append(start)
    for dep in graph.get(start, ()):
        found = _cycle_from(dep, graph, path, done)
        if found:
            return found
    path.pop()
    done.add(start)
    return None


def _canonical(cycle: list[str]) -> tuple[str, ...]:
    """The cycle rotated to start at its smallest name, so each one is reported once."""
    ring = cycle[:-1]
    i = ring.index(min(ring))
    return tuple(ring[i:] + ring[:i])


def analyze_graph(slices: list[Slice]) -> list[Diagnostic]:
    by_name = {s.name: s for s in slices}
    graph = {s.name: tuple(d for d in s.depends_on if d in by_name) for s in slices}
    seen: set[tuple[str, ...]] = set()
    diags = []
    for s in slices:
        cycle = _cycle_from(s.name, graph, [], set())
        if cycle is None or _canonical(cycle) in seen:
            continue
        ring = _canonical(cycle)
        seen.add(ring)
        diags.append(EIT007.create(by_name[ring[0]].manifest, None, " → ".join([*ring, ring[0]])))
    return diags
