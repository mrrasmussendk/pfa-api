"""``heimdall map --root <dir> [--budget 15000] [--kernel <name>]`` — the feedforward emitter.

The ``feature.json`` graph is the ground truth: writes ``.heimdall/map.json`` (relative to CWD)
and regenerates the AGENTS.md markers in every slice: its dependencies, its routes, the e2e scenarios
that state its promise (``tests/e2e/<feature>.feature``), and how much of the token budget it uses,
rounded to 10% so the line moves only when the feature grows by a tenth of its budget. Where things are comes
from ``[tool.heimdall]`` in the root ``pyproject.toml``, as paths from the repo root:

- ``features`` — the folder of slices (default: ``src/pfa/features``, else ``src/slices``, ``slices``);
- ``kernel`` — the kernel package (default: ``kernel`` beside the features folder);
- ``app`` — the application package (default: the features folder's parent);
- ``routes`` — the HTTP edge; ``<routes>/<slice>.py`` is that slice's routes module and is where
  its routes are inventoried (default: ``<app>/api/routes``);
- ``shared`` — extra packages every slice may read.

All of it is recorded in the map so the sensors and the review classify the same way."""

from __future__ import annotations

import ast
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

USAGE = "usage: heimdall map --root <dir> [--budget 15000] [--kernel <name>]"
MANIFEST = "feature.json"
_HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_MAP = re.compile(r"<!--heimdall:map-->.*?<!--/heimdall:map-->", re.S)
_SCENARIO = re.compile(r"^\s*Scenario:\s*(\S+)", re.M)
SCENARIOS_DIR = "tests/e2e"


def _const(text: str) -> Callable[[re.Match[str]], str]:
    """A ``re.sub`` replacement that inserts ``text`` literally (no backslash escapes interpreted)."""
    return lambda _m: text


def _routes_of(path: str) -> list[str]:
    """``METHOD /prefix/path`` for every ``@router.<method>("...")`` in a routes module.
    Parsed with ``ast`` (never imported): the map must not execute the service."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError):
        return []
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "APIRouter":
                prefix = ""
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        prefix = kw.value.value
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = prefix
    routes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
                continue
            method = deco.func.attr
            owner = deco.func.value
            if method not in _HTTP_METHODS or not isinstance(owner, ast.Name) or owner.id not in prefixes:
                continue
            if deco.args and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str):
                routes.append(f"{method.upper()} {prefixes[owner.id]}{deco.args[0].value}")
    return routes


def _scenarios_line(root_abs: str, name: str) -> str:
    """Where the feature's promise is written, and the scenarios in it."""
    path = os.path.join(root_abs, SCENARIOS_DIR, name + ".feature")
    try:
        with open(path, encoding="utf-8") as f:
            names = _SCENARIO.findall(f.read())
    except OSError:
        return f"scenarios: (none) — add them with `heimdall e2e new {name}/<name>`"
    return f"scenarios: `{SCENARIOS_DIR}/{name}.feature` — " + (", ".join(names) if names else "(none yet)")


def _budget_used(root_abs: str, budget: int) -> dict[str, int]:
    """Each feature's EIT100 working set as a percentage of ``budget``, rounded to 10: the headroom an agent
    should know before it adds code, coarse enough that the generated line rarely changes. Empty when Eitri
    cannot measure the tree (it has its own check for that)."""
    try:
        from eitri.context_budget import measure
        from eitri.core import EitriConfig
        from eitri.project import discover_slices, find_kernel_dir, find_slices_dir
    except ImportError:
        return {}
    from pathlib import Path

    root = Path(root_abs)
    config = EitriConfig.read(root, token_budget=budget)
    try:
        sd = find_slices_dir(root, config)
        slices = discover_slices(sd) if sd is not None else []
        kernel = find_kernel_dir(root, config, sd)
        by_name = {s.name: s for s in slices}
        return {s.name: round(10 * measure(s, by_name, kernel, config).total / budget) * 10 for s in slices}
    except (OSError, ValueError, SyntaxError):
        return {}


def _set_marker(txt: str, name: str, line: str, after: str | None) -> str:
    """``txt`` with the ``name`` marker holding ``line``: replaced where it is, else inserted after the ``after``
    marker's line, else appended."""
    marked = f"<!--heimdall:{name}-->{line}<!--/heimdall:{name}-->"
    pattern = re.compile(rf"<!--heimdall:{name}-->.*?<!--/heimdall:{name}-->", re.S)
    if pattern.search(txt):
        return pattern.sub(_const(marked), txt)
    anchor = re.search(rf"<!--/heimdall:{after}-->\n", txt) if after else None
    if anchor:
        return txt[: anchor.end()] + marked + "\n" + txt[anchor.end() :]
    return txt.rstrip() + "\n" + marked + "\n"


def _write_feature_agents(path: str, name: str, lines: list[tuple[str, str]]) -> None:
    """The feature's AGENTS.md with each generated ``(marker, line)`` in place, in order; the rest is the author's."""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    else:
        txt = f"# {name}\n"
    previous = None
    for marker, line in lines:
        txt = _set_marker(txt, marker, line, previous)
        previous = marker
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)


def _root_map_block(
    kernel_dir: str,
    slices_dir: str,
    routes_dir: str,
    budget: int,
    discovered: list[tuple[str, list[str]]],
    fan_in: dict[str, int],
    routes: dict[str, list[str]],
    used: dict[str, int],
) -> str:
    """The generated slice table in the root AGENTS.md — the feedforward map in prose form."""
    lines = [
        "<!--heimdall:map-->",
        f"features: `{slices_dir}` · kernel: `{kernel_dir}` · routes: `{routes_dir}/<feature>.py` · budget: {budget} tokens per feature · regenerate with `heimdall map --root .`",
        "",
        "| feature | depends on | fan-in | budget used | contract | routes |",
        "|---|---|---|---|---|---|",
    ]
    for name, deps in discovered:
        rs = "<br>".join(f"`{r}`" for r in routes[name]) if routes[name] else "(none)"
        share = f"≈{used[name]}%" if name in used else "?"
        lines.append(
            f"| {name} | {', '.join(deps) if deps else '(none)'} | {fan_in[name]} | {share} | `{slices_dir}/{name}/contract/` | {rs} |"
        )
    lines.append("<!--/heimdall:map-->")
    return "\n".join(lines)


def _update_root_agents(cwd: str, block: str) -> None:
    agents = os.path.join(cwd, "AGENTS.md")
    if os.path.isfile(agents):
        with open(agents, encoding="utf-8") as f:
            txt = f.read()
        if "<!--heimdall:map-->" in txt:
            txt = _MAP.sub(_const(block), txt)
        else:
            txt = txt.rstrip() + "\n\n## Feature map\n\n" + block + "\n"
    else:
        txt = "# Agents\n\n## Feature map\n\n" + block + "\n"
    with open(agents, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)


def _rel(path: str, cwd: str) -> str:
    return os.path.relpath(path, cwd).replace("\\", "/")


_SHARED_KEY = re.compile(r"^\s*shared\s*=\s*\[(.*?)\]", re.S | re.M)
_STRING_KEY = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["']([^"']*)["']""", re.M)
_QUOTED = re.compile(r"""["']([^"']*)["']""")


def _heimdall_section(root_abs: str) -> dict[str, Any]:
    """``[tool.heimdall]`` from the root pyproject, or {}. tomllib when available (3.11+), a
    deliberately tiny regex fallback (string keys and ``shared = [...]``) otherwise — Heimdall
    stays stdlib-only."""
    pyproject = os.path.join(root_abs, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return {}
    with open(pyproject, encoding="utf-8") as f:
        text = f.read()
    section: Any = {}
    try:
        import tomllib  # Python 3.11+

        section = tomllib.loads(text).get("tool", {}).get("heimdall", {})
    except ModuleNotFoundError:
        m = re.search(r"^\s*\[tool\.heimdall\]\s*$(.*?)(?=^\s*\[|\Z)", text, re.S | re.M)
        if m:
            body = m.group(1)
            section = dict(_STRING_KEY.findall(body))
            arr = _SHARED_KEY.search(body)
            if arr:
                section["shared"] = _QUOTED.findall(arr.group(1))
    except ValueError:
        return {}
    return section if isinstance(section, dict) else {}


def _shared_packages(section: dict[str, Any]) -> list[str]:
    shared = section.get("shared", [])
    return sorted({str(s) for s in shared}) if isinstance(shared, list) else []


def _path_key(section: dict[str, Any], key: str) -> str | None:
    v = section.get(key)
    return str(v).replace("\\", "/").strip("/") if isinstance(v, str) and v else None


def _first_dir(root_abs: str, *cands: str) -> str | None:
    for c in cands:
        if os.path.isdir(os.path.join(root_abs, c)):
            return os.path.join(root_abs, c)
    return None


def _read_deps(manifest: str) -> list[str]:
    with open(manifest, encoding="utf-8") as f:
        data = json.load(f)
    deps = data.get("depends_on", []) if isinstance(data, dict) else []
    return [str(d) for d in deps] if isinstance(deps, list) else []


class _UsageError(Exception):
    pass


class _UnreadableManifest(Exception):
    def __init__(self, path: str, reason: Exception) -> None:
        super().__init__(f"{path}: {reason}")
        self.path, self.reason = path, reason


@dataclass(frozen=True)
class _Options:
    root: str
    budget: int = 15000
    kernel: str | None = None


def _parse(args: list[str]) -> _Options:
    """``--root`` (required), ``--budget`` and ``--kernel``; anything else is a usage error."""
    values: dict[str, str] = {}
    for i in range(0, len(args), 2):
        if args[i] not in ("--root", "--budget", "--kernel") or i + 1 >= len(args):
            raise _UsageError
        values[args[i][2:]] = args[i + 1]
    if "root" not in values:
        raise _UsageError
    try:
        budget = int(values.get("budget", "15000"))
    except ValueError:
        raise _UsageError from None
    return _Options(values["root"], budget, values.get("kernel"))


@dataclass(frozen=True)
class _Layout:
    """Where everything is, as absolute paths, from ``[tool.heimdall]`` or the defaults."""

    root: str
    slices_dir: str
    app_dir: str
    kernel_dir: str
    kernel: str
    routes_dir: str
    shared: tuple[str, ...]


def _layout(root_abs: str, kernel_name: str | None) -> _Layout | None:
    section = _heimdall_section(root_abs)
    features = _path_key(section, "features")
    slices_dir = _first_dir(root_abs, *([features] if features else []), "src/pfa/features", "pfa/features", "src/slices", "slices")
    if slices_dir is None:
        return None
    app_key, kernel_key, routes_key = (_path_key(section, k) for k in ("app", "kernel", "routes"))
    app_dir = os.path.join(root_abs, app_key) if app_key else os.path.dirname(slices_dir)
    kernel_dir = os.path.join(root_abs, kernel_key) if kernel_key else os.path.join(os.path.dirname(slices_dir), "kernel")
    routes_dir = os.path.join(root_abs, routes_key) if routes_key else os.path.join(app_dir, "api", "routes")
    kernel = kernel_name or os.path.basename(kernel_dir)
    return _Layout(root_abs, slices_dir, app_dir, kernel_dir, kernel, routes_dir, tuple(_shared_packages(section)))


def _discover(lay: _Layout) -> tuple[list[tuple[str, list[str]]], dict[str, list[str]]]:
    """Each feature with a manifest, its dependencies (the kernel left out) and its routes."""
    discovered: list[tuple[str, list[str]]] = []
    routes: dict[str, list[str]] = {}
    for name in sorted(os.listdir(lay.slices_dir)):
        manifest = os.path.join(lay.slices_dir, name, MANIFEST)
        if not os.path.isfile(manifest):
            continue
        try:
            refs = sorted({d for d in _read_deps(manifest) if d != lay.kernel})
        except (OSError, ValueError) as e:
            raise _UnreadableManifest(manifest, e) from None
        discovered.append((name, refs))
        routes[name] = _routes_of(os.path.join(lay.routes_dir, name + ".py"))
    return discovered, routes


def _feature_lines(lay: _Layout, name: str, refs: list[str], routes: list[str], used: dict[str, int], budget: int) -> list[tuple[str, str]]:
    lines = [
        ("deps", f"depends on: {', '.join(refs) if refs else '(none)'} + {lay.kernel}"),
        ("routes", f"routes: {', '.join(routes) if routes else '(none)'}"),
        ("scenarios", _scenarios_line(lay.root, name)),
    ]
    if name in used:
        lines.append(("budget", f"budget: ≈{used[name]}% of {budget:,} tokens used — `eitri check` has the exact count"))
    return lines


def _fan_in(discovered: list[tuple[str, list[str]]]) -> dict[str, int]:
    fan_in = {name: 0 for name, _ in discovered}
    for _, deps in discovered:
        for d in deps:
            if d in fan_in:
                fan_in[d] += 1
    return fan_in


def _map_doc(lay: _Layout, cwd: str, budget: int, discovered: list[tuple[str, list[str]]], routes: dict[str, list[str]]) -> dict[str, Any]:
    fan_in = _fan_in(discovered)
    doc: dict[str, Any] = {
        "kernel": lay.kernel,
        "kernel_dir": _rel(lay.kernel_dir, cwd),
        "slices_dir": _rel(lay.slices_dir, cwd),
        "app_dir": _rel(lay.app_dir, cwd),
        "routes_dir": _rel(lay.routes_dir, cwd),
        "slices": {
            name: {
                "path": _rel(os.path.join(lay.slices_dir, name), cwd),
                "depends_on": deps,
                "budget": budget,
                "fan_in": fan_in[name],
                "routes": routes[name],
            }
            for name, deps in discovered
        },
    }
    if lay.shared:
        doc["shared"] = list(lay.shared)
    return doc


def run(args: list[str], stdout: TextIO, stderr: TextIO, cwd: str) -> int:
    try:
        opts = _parse(args)
    except _UsageError:
        stderr.write(USAGE + "\n")
        return 2
    lay = _layout(os.path.abspath(os.path.join(cwd, opts.root)), opts.kernel)
    if lay is None:
        stderr.write(f"no features/ under {opts.root}\n")
        return 1
    try:
        discovered, routes = _discover(lay)
    except _UnreadableManifest as e:
        stderr.write(f"heimdall map: unreadable {_rel(e.path, cwd)}: {e.reason}\n")
        return 1
    used = _budget_used(lay.root, opts.budget)
    for name, refs in discovered:
        lines = _feature_lines(lay, name, refs, routes[name], used, opts.budget)
        _write_feature_agents(os.path.join(lay.slices_dir, name, "AGENTS.md"), name, lines)
    doc = _map_doc(lay, cwd, opts.budget, discovered, routes)
    heimdall_dir = os.path.join(cwd, ".heimdall")
    os.makedirs(heimdall_dir, exist_ok=True)
    with open(os.path.join(heimdall_dir, "map.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2)
    rels = (_rel(lay.kernel_dir, cwd), _rel(lay.slices_dir, cwd), _rel(lay.routes_dir, cwd))
    _update_root_agents(cwd, _root_map_block(*rels, opts.budget, discovered, _fan_in(discovered), routes, used))
    stdout.write(f"heimdall map: {len(discovered)} features -> .heimdall/map.json; AGENTS.md deps regenerated\n")
    return 0
