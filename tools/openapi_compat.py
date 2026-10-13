"""The HTTP API stays compatible: ``docs/openapi.json`` is the committed spec, and a change to it may only
add, unless the major version says otherwise.

    python tools/openapi_compat.py --write              # regenerate docs/openapi.json from create_app()
    python tools/openapi_compat.py --base origin/main   # breaking changes since the merge base; exit 1 on any

A test (``tests/api/test_openapi.py``) fails while the snapshot differs from what the app serves, so the
snapshot is always the spec under review. Breaking, for a client that worked yesterday:

- an operation removed, or a documented success response removed;
- a request: a new required field, a field removed (requests reject unknown fields), a type changed, a limit
  narrowed (a lower max or a higher min), an enum value removed;
- a response: a field removed or no longer required, a type changed, an enum value added.

Raising the major version of ``info.version`` declares the break, and the check then reports and passes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = "docs/openapi.json"
_NARROWER_IF_LOWER = ("maxLength", "maximum", "maxItems", "exclusiveMaximum")
_NARROWER_IF_HIGHER = ("minLength", "minimum", "minItems", "exclusiveMinimum")

Schema = dict[str, Any]


def generate() -> Schema:
    sys.path.insert(0, str(REPO / "src"))
    from pfa.api.app import create_app  # the service; imported only here, so the comparison needs none of it

    return create_app(warmup=False).openapi()


def dump(spec: Schema) -> str:
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class Spec:
    """One side of the comparison: the document, with ``$ref`` resolved against its own components."""

    def __init__(self, doc: Schema) -> None:
        self.doc = doc

    def resolve(self, schema: Schema | None) -> Schema:
        seen = 0
        while schema and "$ref" in schema and seen < 32:
            schema = self.doc.get("components", {}).get("schemas", {}).get(schema["$ref"].rsplit("/", 1)[-1], {})
            seen += 1
        if schema and "allOf" in schema and len(schema["allOf"]) == 1:
            return self.resolve({**{k: v for k, v in schema.items() if k != "allOf"}, **self.resolve(schema["allOf"][0])})
        return schema or {}

    def body(self, op: Schema) -> Schema:
        return self.resolve(op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema"))

    def response(self, op: Schema, status: str) -> Schema:
        return self.resolve(op.get("responses", {}).get(status, {}).get("content", {}).get("application/json", {}).get("schema"))


def _type_change(o: Schema, n: Schema, where: str) -> list[str]:
    return [f"{where}: type {o['type']} became {n.get('type')}"] if "type" in o and o.get("type") != n.get("type") else []


def _narrowed(o: Schema, n: Schema, where: str) -> list[str]:
    out = [f"{where}: {k} lowered to {n[k]}" for k in _NARROWER_IF_LOWER if k in n and (k not in o or n[k] < o[k])]
    out += [f"{where}: {k} raised to {n[k]}" for k in _NARROWER_IF_HIGHER if k in n and (k not in o or n[k] > o[k])]
    return out


def _enum(o: Schema, n: Schema, where: str, *, request: bool) -> list[str]:
    old, new = set(map(str, o.get("enum", []))), set(map(str, n.get("enum", [])))
    if not old or not new:
        return []
    changed = old - new if request else new - old
    verb = "no longer accepts" if request else "may now return"
    return [f"{where}: {verb} {sorted(changed)}"] if changed else []


def request_breaks(old: Spec, new: Spec, o: Schema, n: Schema, where: str) -> list[str]:
    """What a client sending what ``o`` accepted would now have rejected by ``n``."""
    o, n = old.resolve(o), new.resolve(n)
    out = _type_change(o, n, where) + _narrowed(o, n, where) + _enum(o, n, where, request=True)
    o_props, n_props = o.get("properties", {}), n.get("properties", {})
    out += [f"{where}.{k}: now required" for k in sorted(set(n.get("required", [])) - set(o.get("required", [])))]
    if n.get("additionalProperties") is False:
        out += [f"{where}.{k}: removed, and unknown fields are rejected" for k in sorted(set(o_props) - set(n_props))]
    for k in sorted(set(o_props) & set(n_props)):
        out += request_breaks(old, new, o_props[k], n_props[k], f"{where}.{k}")
    if "items" in o and "items" in n:
        out += request_breaks(old, new, o["items"], n["items"], f"{where}[]")
    return out


def response_breaks(old: Spec, new: Spec, o: Schema, n: Schema, where: str) -> list[str]:
    """What a client reading what ``o`` promised would no longer find in ``n``."""
    o, n = old.resolve(o), new.resolve(n)
    out = _type_change(o, n, where) + _enum(o, n, where, request=False)
    o_props, n_props = o.get("properties", {}), n.get("properties", {})
    out += [f"{where}.{k}: removed" for k in sorted(set(o_props) - set(n_props))]
    out += [
        f"{where}.{k}: no longer always present" for k in sorted(set(o.get("required", [])) - set(n.get("required", []))) if k in n_props
    ]
    for k in sorted(set(o_props) & set(n_props)):
        out += response_breaks(old, new, o_props[k], n_props[k], f"{where}.{k}")
    if "items" in o and "items" in n:
        out += response_breaks(old, new, o["items"], n["items"], f"{where}[]")
    return out


def _operation_breaks(old: Spec, new: Spec, o: Schema, n: Schema, name: str) -> list[str]:
    out = request_breaks(old, new, old.body(o), new.body(n), f"{name} request")
    for status in sorted(s for s in o.get("responses", {}) if s.startswith("2")):
        if status not in n.get("responses", {}):
            out.append(f"{name}: success response {status} removed")
        else:
            out += response_breaks(old, new, old.response(o, status), new.response(n, status), f"{name} {status}")
    return out


def breaking_changes(old_doc: Schema, new_doc: Schema) -> list[str]:
    """Every change that would break a client written against ``old_doc``."""
    old, new = Spec(old_doc), Spec(new_doc)
    out = []
    for path, ops in sorted(old_doc.get("paths", {}).items()):
        for method, o in sorted(ops.items()):
            name = f"{method.upper()} {path}"
            n = new_doc.get("paths", {}).get(path, {}).get(method)
            out += [f"{name}: removed"] if n is None else _operation_breaks(old, new, o, n, name)
    return out


def major(doc: Schema) -> int:
    try:
        return int(str(doc.get("info", {}).get("version", "0")).split(".", 1)[0])
    except ValueError:
        return 0


def _base_snapshot(ref: str, root: Path) -> Schema | None:
    """The snapshot at the merge base with ``ref``, or None when the base has none."""
    if ref.startswith("-"):
        raise SystemExit(f"--base {ref!r} must be a ref, not an option")
    rev = subprocess.run(["git", "merge-base", ref, "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    shown = subprocess.run(["git", "show", f"{rev}:{SNAPSHOT}"], cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def compare(ref: str, root: Path = REPO) -> int:
    """0 when the working tree's snapshot breaks no client of the base's, or declares the break; 1 otherwise."""
    old = _base_snapshot(ref, root)
    if old is None:
        print(f"openapi compat: no {SNAPSHOT} at the merge base with {ref}; nothing to compare")
        return 0
    new = json.loads((root / SNAPSHOT).read_text(encoding="utf-8"))
    breaks = breaking_changes(old, new)
    if not breaks:
        print(f"openapi compat: compatible with {ref}")
        return 0
    declared = major(new) > major(old)
    print(f"openapi compat: {len(breaks)} breaking change(s) since {ref}" + (" — declared by the major version" if declared else ":"))
    print("".join(f"- {b}\n" for b in breaks), end="")
    if not declared:
        print("Keep the old shape beside the new one, or raise the major version of info.version to declare the break.")
    return 0 if declared else 1


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="openapi_compat", description=__doc__.split("\n\n")[0])
    p.add_argument("--write", action="store_true", help=f"regenerate {SNAPSHOT} from the app")
    p.add_argument("--base", help="report breaking changes since the merge base with this ref")
    args = p.parse_args(argv)
    if args.write:
        (REPO / SNAPSHOT).write_text(dump(generate()), encoding="utf-8", newline="\n")
        print(f"openapi compat: wrote {SNAPSHOT}")
    return compare(args.base) if args.base else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
