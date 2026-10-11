"""EIT008 — a contract changes additively while anything still relies on it.

``eitri check --base <ref>`` compares every feature's ``contract/`` with the same files at the merge base
with ``<ref>``. Adding is free: a new message, a new public name, a field or parameter with a default.
Removing or changing a public member is breaking, and is allowed only once nothing outside the owning
feature used it at the base. That is the expand-contract migration the guide asks for, as a check: add
the replacement, migrate every consumer, and remove the old member in a later change.

The owning feature's own HTTP edge (``<app>/…/routes/<feature>.py``) is part of the feature and is not a
consumer. A file that cannot be parsed is skipped here; ``eitri check`` reports it as EIT000."""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .core import EIT008, Diagnostic, EitriConfig
from .project import Slice, import_root_of, module_name, package_path
from .surface import is_public, public_names


class CompatError(Exception):
    """git could not answer: no repository, or an unknown ref."""


def _git(root: Path, *args: str) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as e:
        raise CompatError(f"git not available: {e}") from None
    if p.returncode != 0:
        raise CompatError(f"git {args[0]} failed: {p.stderr.strip()}")
    return p.stdout


@dataclass(frozen=True)
class Base:
    """The tree at the merge base: its ``.py`` files under the import root, read on demand."""

    root: Path
    rev: str
    files: tuple[str, ...]  # repo-relative posix paths

    @classmethod
    def at(cls, root: Path, ref: str, under: Path) -> Base:
        if ref.startswith("-"):
            raise CompatError(f"--base {ref!r} must be a ref, not an option")
        rev = _git(root, "merge-base", ref, "HEAD").strip()
        prefix = under.relative_to(root).as_posix()
        listed = _git(root, "ls-tree", "-r", "--name-only", rev, "--", prefix).splitlines()
        return cls(root, rev, tuple(p for p in listed if p.endswith(".py")))

    def read(self, path: str) -> str | None:
        try:
            return _git(self.root, "show", f"{self.rev}:{path}")
        except CompatError:
            return None


def _parse(text: str | None) -> ast.Module | None:
    if text is None:
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    stub = ast.FunctionDef(node.name, node.args, [ast.Expr(ast.Constant(...))], [], node.returns, None, lineno=0)
    return ast.unparse(stub)


def _members(tree: ast.Module) -> dict[str, str]:
    """``name`` (or ``Class.member``) → its public shape, from one contract module. A class contributes its
    bases and each public member; a dataclass field keeps whether it has a default; a re-export counts too."""
    exported = public_names(tree)
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_public(node.name, exported):
            out[node.name] = _signature(node)
        elif isinstance(node, ast.ClassDef) and is_public(node.name, exported):
            out[node.name] = "class(" + ", ".join(ast.unparse(b) for b in node.bases) + ")"
            for member in node.body:
                out.update({f"{node.name}.{k}": v for k, v in _class_member(member).items()})
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and is_public(node.target.id, exported):
            out[node.target.id] = ast.unparse(node.annotation)
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            out.update({a.asname or a.name: "re-export" for a in node.names if is_public(a.asname or a.name, exported)})
    return out


def _class_member(member: ast.stmt) -> dict[str, str]:
    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_public(member.name):
        return {member.name: _signature(member)}
    if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name) and is_public(member.target.id):
        return {member.target.id: ast.unparse(member.annotation) + (" = …" if member.value is not None else "")}
    return {}


def _additive(old: str, new: str) -> bool:
    """Whether ``new`` only extends ``old``: a field gaining a default, or a signature gaining defaulted
    keyword-compatible parameters at its end."""
    if old == new or (not old.endswith(" = …") and new == old + " = …"):
        return True
    if not (old.startswith("def ") and new.startswith("def ")):
        return False
    a, b = ast.parse(old).body[0], ast.parse(new).body[0]
    assert isinstance(a, ast.FunctionDef) and isinstance(b, ast.FunctionDef)
    added = len(b.args.args) - len(a.args.args)
    kept = ast.unparse(a.args) == ast.unparse(_trim(b.args, added))
    return (
        added > 0
        and kept
        and len(b.args.defaults) >= added
        and ast.unparse(a.returns or ast.Constant(None)) == ast.unparse(b.returns or ast.Constant(None))
    )


def _trim(args: ast.arguments, added: int) -> ast.arguments:
    """``args`` without its last ``added`` positional parameters and their defaults."""
    keep = len(args.args) - added
    return ast.arguments(
        args.posonlyargs,
        args.args[:keep],
        args.vararg,
        args.kwonlyargs,
        args.kw_defaults,
        args.kwarg,
        args.defaults[: max(0, len(args.defaults) - added)],
    )


def _breaks(old: dict[str, str], new: dict[str, str]) -> list[tuple[str, str]]:
    """Each breaking change, as (top-level name, what happened to the member)."""
    out = []
    for key, shape in old.items():
        top = key.split(".", 1)[0]
        if key not in new:
            out.append((top, f"`{key}` removed"))
        elif not _additive(shape, new[key]):
            out.append((top, f"`{key}` changed from `{shape}` to `{new[key]}`"))
    out += [(k.split(".", 1)[0], f"`{k}` added without a default") for k, v in new.items() if "." in k and k not in old and _required(v)]
    return out


def _required(shape: str) -> bool:
    """A new member a caller must now supply: a field without a default. Methods and defaulted fields are free."""
    return not shape.startswith("def ") and not shape.endswith(" = …")


def _uses(tree: ast.Module, contract: str, name: str) -> bool:
    """Whether a module imports ``name`` from the contract package, or reaches it through the package."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(contract) and any(a.name in (name, "*") for a in node.names):
            return True
        if isinstance(node, ast.Attribute) and node.attr == name and contract.rsplit(".", 1)[-1] in ast.unparse(node.value):
            return True
    return False


def _consumers(base: Base, owner: Slice, contract: str, name: str) -> list[str]:
    own = owner.path.relative_to(base.root).as_posix() + "/"
    users = []
    for path in base.files:
        if path.startswith(own) or PurePosixPath(path).match(f"routes/{owner.name}.py"):
            continue
        tree = _parse(base.read(path))
        if tree is not None and _uses(tree, contract, name):
            users.append(path)
    return users


def analyze_compat(root: Path, slices: list[Slice], slices_dir: Path, config: EitriConfig, ref: str) -> list[Diagnostic]:
    root = root.resolve()
    import_root = import_root_of(slices_dir, config)
    base = Base.at(root, ref, import_root / package_path(config.app_package))
    diags = []
    for s in slices:
        contract = module_name(s.contract_dir / "__init__.py", import_root)
        for path in sorted(s.contract_dir.rglob("*.py")) if s.contract_dir.is_dir() else []:
            rel = path.relative_to(root).as_posix()
            old, new = _parse(base.read(rel)), _parse(path.read_text(encoding="utf-8", errors="replace"))
            if old is None or new is None:
                continue
            for top, what in _breaks(_members(old), _members(new)):
                users = _consumers(base, s, contract, top)
                if users:
                    diags.append(EIT008.create(path, None, s.name, what, ref, ", ".join(users)))
    # A contract module deleted outright removes everything it held.
    for gone in base.files:
        here = root / gone
        for s in slices:
            if gone.startswith(s.contract_dir.relative_to(root).as_posix() + "/") and not here.exists():
                tree = _parse(base.read(gone))
                for top in sorted({k.split(".", 1)[0] for k in _members(tree)} if tree else ()):
                    users = _consumers(base, s, module_name(s.contract_dir / "__init__.py", import_root), top)
                    if users:
                        diags.append(EIT008.create(here, None, s.name, f"`{top}` removed with its module", ref, ", ".join(users)))
    return diags
