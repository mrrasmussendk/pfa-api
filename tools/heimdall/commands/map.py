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


def run(args: list[str], stdout: TextIO, stderr: TextIO, cwd: str) -> int:
    root: str | None = None
    budget = 15000
    kernel_name: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--root" and i + 1 < len(args):
            root = args[i + 1]
            i += 2
        elif a == "--budget" and i + 1 < len(args):
            try:
                budget = int(args[i + 1])
            except ValueError:
                stderr.write(USAGE + "\n")
                return 2
            i += 2
        elif a == "--kernel" and i + 1 < len(args):
            kernel_name = args[i + 1]
            i += 2
        else:
            stderr.write(USAGE + "\n")
            return 2
    if root is None:
        stderr.write(USAGE + "\n")
        return 2

    root_abs = os.path.abspath(os.path.join(cwd, root))
    section = _heimdall_section(root_abs)

    features = _path_key(section, "features")
    slices_dir = _first_dir(root_abs, *([features] if features else []), "src/pfa/features", "pfa/features", "src/slices", "slices")
    if slices_dir is None:
        stderr.write(f"no features/ under {root}\n")
        return 1
    app_key = _path_key(section, "app")
    app_dir = os.path.join(root_abs, app_key) if app_key else os.path.dirname(slices_dir)
    kernel_key = _path_key(section, "kernel")
    kernel_dir = os.path.join(root_abs, kernel_key) if kernel_key else os.path.join(os.path.dirname(slices_dir), "kernel")
    kernel = kernel_name or os.path.basename(kernel_dir)
    routes_key = _path_key(section, "routes")
    routes_dir = os.path.join(root_abs, routes_key) if routes_key else os.path.join(app_dir, "api", "routes")
    shared = _shared_packages(section)

    discovered: list[tuple[str, list[str]]] = []
    routes: dict[str, list[str]] = {}
    used = _budget_used(root_abs, budget)
    for name in sorted(os.listdir(slices_dir)):
        manifest = os.path.join(slices_dir, name, MANIFEST)
        if not os.path.isfile(manifest):
            continue
        try:
            refs = sorted({d for d in _read_deps(manifest) if d != kernel})
        except (OSError, ValueError) as e:
            stderr.write(f"heimdall map: unreadable {_rel(manifest, cwd)}: {e}\n")
            return 1
        discovered.append((name, refs))
        routes[name] = _routes_of(os.path.join(routes_dir, name + ".py"))

        generated = [
            ("deps", f"depends on: {', '.join(refs) if refs else '(none)'} + {kernel}"),
            ("routes", f"routes: {', '.join(routes[name]) if routes[name] else '(none)'}"),
            ("scenarios", _scenarios_line(root_abs, name)),
        ]
        if name in used:
            generated.append(("budget", f"budget: ≈{used[name]}% of {budget:,} tokens used — `eitri check` has the exact count"))
        _write_feature_agents(os.path.join(slices_dir, name, "AGENTS.md"), name, generated)

    fan_in = {name: 0 for name, _ in discovered}
    for _, deps in discovered:
        for d in deps:
            if d in fan_in:
                fan_in[d] += 1

    doc: dict[str, Any] = {
        "kernel": kernel,
        "kernel_dir": _rel(kernel_dir, cwd),
        "slices_dir": _rel(slices_dir, cwd),
        "app_dir": _rel(app_dir, cwd),
        "routes_dir": _rel(routes_dir, cwd),
        "slices": {
            name: {
                "path": _rel(os.path.join(slices_dir, name), cwd),
                "depends_on": deps,
                "budget": budget,
                "fan_in": fan_in[name],
                "routes": routes[name],
            }
            for name, deps in discovered
        },
    }
    if shared:
        doc["shared"] = shared
    heimdall_dir = os.path.join(cwd, ".heimdall")
    os.makedirs(heimdall_dir, exist_ok=True)
    with open(os.path.join(heimdall_dir, "map.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2)
    _update_root_agents(
        cwd, _root_map_block(_rel(kernel_dir, cwd), _rel(slices_dir, cwd), _rel(routes_dir, cwd), budget, discovered, fan_in, routes, used)
    )
    stdout.write(f"heimdall map: {len(discovered)} features -> .heimdall/map.json; AGENTS.md deps regenerated\n")
    return 0
