"""Descriptors, diagnostics and configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

HELP_BASE = "harness/rules/"  # relative to the repo root


class Severity(str, Enum):
    ERROR = "error"
    INFO = "info"


@dataclass(frozen=True)
class Descriptor:
    id: str
    title: str
    message_format: str
    category: str
    severity: Severity
    help_link: str

    def create(self, path: Path | None, line: int | None, *args: Any) -> Diagnostic:
        return Diagnostic(self, self.message_format.format(*args), path, line)


EIT000 = Descriptor(
    id="EIT000",
    title="Source file could not be parsed",
    message_format="'{0}' could not be parsed ({1}) — Eitri cannot police what Python cannot read",
    category="Eitri.Architecture",
    severity=Severity.ERROR,
    help_link=HELP_BASE + "EIT001.md",
)

EIT001 = Descriptor(
    id="EIT001",
    title="Public surface outside Contract",
    message_format=(
        "'{0}' imports '{1}' — a slice's public surface is its Contract; everything outside "
        "'{2}/contract' is internal to slice '{2}' and must be reached through its Contract"
    ),
    category="Eitri.Architecture",
    severity=Severity.ERROR,
    help_link=HELP_BASE + "EIT001.md",
)

EIT002 = Descriptor(
    id="EIT002",
    title="Visibility escape hatch is banned",
    message_format=(
        "{0} breaches slice isolation — dynamic imports, sys.path hacks and wildcard imports "
        "are invisible to grep and to Eitri; expose capability through the Contract instead"
    ),
    category="Eitri.Architecture",
    severity=Severity.ERROR,
    help_link=HELP_BASE + "EIT002.md",
)

EIT003 = Descriptor(
    id="EIT003",
    title="Contract module leaks a non-kernel dependency",
    message_format=(
        "Contract module '{0}' imports '{1}' — contract modules may use only kernel types, "
        "standard-library types, and types of the same Contract, so that consuming a contract "
        "never drags in transitive context"
    ),
    category="Eitri.Architecture",
    severity=Severity.ERROR,
    help_link=HELP_BASE + "EIT003.md",
)

EIT004 = Descriptor(
    id="EIT004",
    title="Undeclared slice dependency",
    message_format=(
        "'{0}' imports the Contract of '{1}' but {2} does not declare it — what a slice can reach "
        "is exactly what its manifest declares; add '{1}' to depends_on"
    ),
    category="Eitri.Architecture",
    severity=Severity.ERROR,
    help_link=HELP_BASE + "EIT004.md",
)

EIT005 = Descriptor(
    id="EIT005",
    title="HTTP error bypasses Problem Details",
    message_format=(
        "'{0}' uses '{1}' — slices answer errors as RFC 9457 problem documents only; raise "
        "the shared problem type '{2}' so every error on the wire has one shape"
    ),
    category="Eitri.Architecture",
    severity=Severity.ERROR,
    help_link=HELP_BASE + "EIT005.md",
)

EIT100 = Descriptor(
    id="EIT100",
    title="Agent context budget exceeded",
    message_format=(
        "Slice '{0}' worst-case agent working set is ≈{1:,} tokens (own source {2:,} + dependency "
        "contracts {3:,} + kernel {4:,}), over the budget of {5:,} — split the slice or slim its contracts"
    ),
    category="Eitri.ContextBudget",
    severity=Severity.ERROR,
    help_link=HELP_BASE + "EIT100.md",
)

EIT101 = Descriptor(
    id="EIT101",
    title="Agent context budget report",
    message_format=("Slice '{0}' agent working set ≈{1:,} tokens of {5:,} budget (source {2:,} · contracts {3:,} · kernel {4:,})"),
    category="Eitri.ContextBudget",
    severity=Severity.INFO,
    help_link=HELP_BASE + "EIT100.md",
)

ALL_DESCRIPTORS: tuple[Descriptor, ...] = (EIT000, EIT001, EIT002, EIT003, EIT004, EIT005, EIT100, EIT101)


@dataclass
class Diagnostic:
    descriptor: Descriptor
    message: str
    path: Path | None
    line: int | None

    @property
    def id(self) -> str:
        return self.descriptor.id

    @property
    def severity(self) -> Severity:
        return self.descriptor.severity

    def format(self, relative_to: Path | None = None) -> str:
        """Compiler-style line: ``path:line: error EIT001: message``."""
        loc = "eitri"
        if self.path is not None:
            p = self.path
            if relative_to is not None:
                try:
                    p = p.relative_to(relative_to)
                except ValueError:
                    pass
            loc = str(p).replace("\\", "/")
            if self.line is not None:
                loc += f":{self.line}"
        return f"{loc}: {self.severity.value} {self.id}: {self.message}"


@dataclass(frozen=True)
class EitriConfig:
    """What the walls police. Read from ``[tool.eitri]`` in pyproject.toml, overridable per call."""

    slice_prefix: str = "slices."
    kernel: str = "shared_kernel"
    token_budget: int = 15_000
    enabled: bool = True
    contract_allowed_modules: tuple[str, ...] = ()
    # The one sanctioned way for a slice to answer an error (EIT005 names it as the remedy).
    problem_helper: str = "http_common.Problem"

    @property
    def slices_package(self) -> str:
        return self.slice_prefix.rstrip(".")

    @classmethod
    def read(cls, root: Path | str, **overrides: Any) -> EitriConfig:
        values: dict[str, Any] = {}
        pyproject = Path(root) / "pyproject.toml"
        if pyproject.is_file():
            values.update(_read_tool_eitri(pyproject))
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls._from_values(values)

    @classmethod
    def _from_values(cls, values: dict[str, Any]) -> EitriConfig:
        prefix = str(values.get("slice_prefix") or "slices.")
        if not prefix.endswith("."):
            prefix += "."
        kernel = str(values.get("kernel") or "shared_kernel")
        budget_raw = values.get("token_budget", 15_000)
        try:
            budget = int(budget_raw)
        except (TypeError, ValueError):
            budget = 15_000
        enabled_raw = values.get("enabled", True)
        enabled = not (enabled_raw is False or str(enabled_raw).lower() == "false")
        allowed = values.get("contract_allowed_modules") or ()
        if isinstance(allowed, str):
            allowed = (allowed,)
        helper = str(values.get("problem_helper") or "http_common.Problem")
        return cls(prefix, kernel, budget, enabled, tuple(str(a) for a in allowed), helper)


_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KV = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(.+?)\s*$")
_QUOTED = re.compile(r"""["']([^"']*)["']""")


def _read_tool_eitri(pyproject: Path) -> dict[str, Any]:
    text = pyproject.read_text(encoding="utf-8")
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        return _mini_toml_section(text, "tool.eitri")
    data = tomllib.loads(text)
    section = data.get("tool", {}).get("eitri", {})
    return dict(section) if isinstance(section, dict) else {}


def _mini_toml_section(text: str, wanted: str) -> dict[str, Any]:
    """A deliberately tiny TOML subset (scalars + string arrays) for Python 3.10 without tomllib."""
    out: dict[str, Any] = {}
    current = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0]
        sec = _SECTION.match(line)
        if sec:
            current = sec.group(1).strip()
            continue
        if current != wanted:
            continue
        kv = _KV.match(line)
        if not kv:
            continue
        key, value = kv.group(1), kv.group(2)
        if value.startswith("["):
            out[key] = _QUOTED.findall(value)
        elif value[:1] in ("'", '"'):
            out[key] = value[1:-1] if value[-1:] == value[:1] else value[1:]
        elif value.lower() in ("true", "false"):
            out[key] = value.lower() == "true"
        else:
            try:
                out[key] = int(value.replace("_", ""))
            except ValueError:
                out[key] = value
    return out
