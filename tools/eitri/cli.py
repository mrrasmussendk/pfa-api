"""``eitri check`` — the build gate. ``eitri surface`` — what an agent loads to consume a slice."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from .analyzer import EitriError, analyze
from .context_budget import render_surface
from .core import EitriConfig, Severity
from .project import discover_slices, find_kernel_dir, find_slices_dir


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eitri", description="Compile-time-style architecture enforcement for agent-ready Python.")
    sub = p.add_subparsers(dest="command")

    check = sub.add_parser("check", help="run the walls (EIT001-EIT005) and the context budget (EIT100/EIT101)")
    _common(check)
    check.add_argument("--no-info", action="store_true", help="print only errors (hides EIT101 budget reports)")

    surface = sub.add_parser("surface", help="print the reconstructed public surface of a slice's Contract (or the kernel)")
    _common(surface)
    surface.add_argument("slice", help="slice name, or the kernel name")
    return p


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", default=".", help="project root holding pyproject.toml and the slices package (default: .)")
    p.add_argument("--slices-dir", default=None, help="explicit slices directory (default: <root>/<prefix> or <root>/src/<prefix>)")
    p.add_argument("--budget", type=int, default=None, help="token budget per slice (default: [tool.eitri].token_budget or 15000)")
    p.add_argument("--kernel", default=None, help="kernel package, dotted (default: [tool.eitri].kernel or pfa.kernel)")
    p.add_argument("--prefix", default=None, help="slices package prefix, dotted (default: [tool.eitri].slice_prefix or 'pfa.features.')")


def _config(args: argparse.Namespace) -> EitriConfig:
    return EitriConfig.read(args.root, token_budget=args.budget, kernel=args.kernel, slice_prefix=args.prefix)


def run(argv: list[str], stdout: TextIO, stderr: TextIO) -> int:
    parser = _parser()
    args = parser.parse_args(argv or ["check"])
    if args.command is None:
        args = parser.parse_args(["check", *argv])

    root = Path(args.root).resolve()
    config = _config(args)

    if args.command == "surface":
        return _surface(args, root, config, stdout, stderr)

    try:
        diags = analyze(root, config, slices_dir=args.slices_dir)
    except EitriError as e:
        stderr.write(f"eitri: {e}\n")
        return 1

    diags.sort(key=lambda d: (str(d.path or ""), d.line or 0, d.id))
    errors = 0
    for d in diags:
        if d.severity == Severity.ERROR:
            errors += 1
        elif args.no_info:
            continue
        stdout.write(d.format(relative_to=Path.cwd()) + "\n")
    stdout.write(f"eitri: {errors} error(s), {len(diags) - errors} info\n")
    return 1 if errors else 0


def _surface(args: argparse.Namespace, root: Path, config: EitriConfig, stdout: TextIO, stderr: TextIO) -> int:
    sd = Path(args.slices_dir).resolve() if args.slices_dir else find_slices_dir(root, config)
    if sd is None:
        stderr.write(f"eitri: no {config.slices_package.replace('.', '/')}/ under {root}\n")
        return 1
    if args.slice == config.kernel:
        kernel_dir = find_kernel_dir(root, config, sd)
        if kernel_dir is None:
            stderr.write(f"eitri: no kernel package '{config.kernel}' under {root}\n")
            return 1
        stdout.write(render_surface(kernel_dir))
        return 0
    for s in discover_slices(sd):
        if s.name == args.slice:
            stdout.write(render_surface(s.contract_dir) if s.contract_dir.is_dir() else "")
            return 0
    stderr.write(f"eitri: no slice '{args.slice}' under {sd}\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    return run(sys.argv[1:] if argv is None else argv, sys.stdout, sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
