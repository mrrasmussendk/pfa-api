"""The installed dependencies carry no vulnerability a release has fixed.

    pip install pip-audit && python tools/dependency_audit.py

Runs ``pip-audit`` over the installed environment (the editable project itself skipped) and fails on any
advisory with a fixed version: that one is an upgrade away. An advisory with no fix yet is reported and
does not fail, since nothing can be done but wait or replace the package; the report keeps it visible."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


def findings(report: dict[str, Any]) -> tuple[list[str], list[str]]:
    """``(fixable, unfixed)`` lines from ``pip-audit --format json``: package, version, advisory, and the fix."""
    fixable: list[str] = []
    unfixed: list[str] = []
    for dep in report.get("dependencies", []):
        for vuln in {v["id"]: v for v in dep.get("vulns", [])}.values():  # pip-audit may list one advisory twice
            line = f"{dep['name']} {dep['version']}: {vuln['id']}"
            fixes = vuln.get("fix_versions") or []
            if fixes:
                fixable.append(f"{line} — fixed in {', '.join(fixes)}")
            else:
                unfixed.append(f"{line} — no fixed release yet")
    return fixable, unfixed


def main() -> int:
    p = subprocess.run(
        [sys.executable, "-m", "pip_audit", "--skip-editable", "--progress-spinner", "off", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(p.stdout)
    except ValueError:
        print(f"dependency audit: pip-audit did not report ({p.stderr.strip() or 'is it installed?'})")
        return 2
    fixable, unfixed = findings(report)
    print(f"dependency audit: {len(report.get('dependencies', []))} packages, {len(fixable)} fixable and {len(unfixed)} unfixed advisories")
    print("".join(f"- {line}\n" for line in fixable + unfixed), end="")
    if fixable:
        print("Upgrade each fixable package to a fixed version (or pin past it) and run the suite.")
    return 1 if fixable else 0


if __name__ == "__main__":
    sys.exit(main())
