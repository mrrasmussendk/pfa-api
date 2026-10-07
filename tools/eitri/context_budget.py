"""EIT100/EIT101 — the context-budget fitness function, as a check gate.

For each slice, computes the worst-case *agent working set*:

    own source tokens
  + Σ public Contract API surface tokens of every declared dependency slice
  + public API surface tokens of the kernel

The dependency surfaces are reconstructed from the AST as body-less stubs (see
``surface.py``) — what an agent would actually need in context to consume that
dependency — so the number is architecture-truthful: internals of dependencies cost
zero, exactly as the architecture promises.

Always reports EIT101 (info) with the number; fails the check with EIT100 when the
budget (``token_budget``, default 15000) is exceeded.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from brokkr.tokenization import estimate

from .core import EIT100, EIT101, Diagnostic, EitriConfig
from .project import Slice, source_files
from .surface import render_module_surface


@dataclass(frozen=True)
class BudgetReport:
    slice: str
    own: int
    contracts: int
    kernel: int
    budget: int

    @property
    def total(self) -> int:
        return self.own + self.contracts + self.kernel

    @property
    def over(self) -> bool:
        return self.total > self.budget


def source_tokens(directory: Path) -> int:
    return sum(estimate(f.read_text(encoding="utf-8", errors="replace")) for f in source_files(directory))


def surface_tokens(directory: Path) -> int:
    """Price the public surface an agent would load for every module under ``directory``."""
    return estimate(render_surface(directory))


def render_surface(directory: Path) -> str:
    chunks = []
    for f in source_files(directory):
        text = f.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text, filename=str(f))
        except SyntaxError:
            chunks.append(text)  # unparsable: charge the raw text, never under-count
            continue
        rendered = render_module_surface(tree)
        if rendered:
            chunks.append(f"# {f.name}\n{rendered}")
    return "\n".join(chunks)


def measure(
    slice: Slice,
    slices_by_name: dict[str, Slice],
    kernel_dir: Path | None,
    config: EitriConfig,
) -> BudgetReport:
    own = source_tokens(slice.path)
    contracts = 0
    for dep in slice.depends_on:
        target = slices_by_name.get(dep)
        if target is not None and target.contract_dir.is_dir():
            contracts += surface_tokens(target.contract_dir)
    kernel = surface_tokens(kernel_dir) if kernel_dir is not None else 0
    return BudgetReport(slice.name, own, contracts, kernel, config.token_budget)


def analyze_budgets(
    slices: list[Slice],
    kernel_dir: Path | None,
    config: EitriConfig,
) -> list[Diagnostic]:
    by_name = {s.name: s for s in slices}
    diags = []
    for s in slices:
        r = measure(s, by_name, kernel_dir, config)
        descriptor = EIT100 if r.over else EIT101
        diags.append(descriptor.create(s.path, None, r.slice, r.total, r.own, r.contracts, r.kernel, r.budget))
    return diags
