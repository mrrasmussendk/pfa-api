"""Brokkr — mutation tests for Eitri's rules: each injected violation must FAIL the check.

If a config change, tooling update, or helpful refactor quietly disarms a wall, Brokkr
notices. Runs against the synthetic fixture tree (tools/fixtures.py) so it is independent
of the API's real slices, and first asserts the real repo (``src/``) passes clean.

Run from the repo root:  python tools/brokkr_canary.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from eitri.cli import run  # noqa: E402
from fixtures import write_fixture_tree  # noqa: E402

CANARIES = [
    ("EIT001", "pfa/features/kvad/internal/_c.py", "from pfa.features.rune.internal.rune_engine import RuneEngine\n"),
    (
        "EIT002",
        "pfa/features/rune/internal/_c.py",
        "import importlib\nengine = importlib.import_module('pfa.features.kvad.internal.kvad_engine')\n",
    ),
    ("EIT003", "pfa/features/kvad/contract/_c.py", "from pfa.features.rune.contract import RuneReading\n"),
    ("EIT004", "pfa/features/rune/internal/_c.py", "from pfa.features.kvad.contract import Verse\n"),
    (
        "EIT005",
        "pfa/api/routes/_c.py",
        "from fastapi import HTTPException\n\ndef fail():\n    raise HTTPException(status_code=422, detail='x')\n",
    ),
    ("EIT006", "pfa/features/rune/internal/_c.py", "from fastapi import APIRouter\n\nrouter = APIRouter()\n"),
    ("EIT001", "pfa/api/_c.py", "from pfa.features.rune.internal.rune_engine import RuneEngine\n"),
    ("EIT007", "pfa/features/rune/feature.json", '{"depends_on": ["kvad"]}\n'),
]


def check(root: Path, *extra: str) -> str:
    out, err = io.StringIO(), io.StringIO()
    run(["check", "--root", str(root), *extra], out, err)
    return out.getvalue() + err.getvalue()


def _bites(root: Path, rule: str, rel: str, code: str) -> bool:
    """Whether writing ``code`` at ``rel`` makes ``rule`` fail the check; the file is put back as it was."""
    target = root / rel
    original = target.read_text(encoding="utf-8") if target.exists() else None  # a canary may overwrite a manifest
    target.write_text(code, encoding="utf-8")
    try:
        return f"error {rule}" in check(root)
    finally:
        if original is None:
            target.unlink()
        else:
            target.write_text(original, encoding="utf-8")


def _disarmed(root: Path) -> bool:
    """Whether ``enabled = false`` lets a violation through: the footgun Brokkr exists for."""
    (root / "pyproject.toml").write_text("[tool.eitri]\nenabled = false\n", encoding="utf-8")
    (root / "pfa/features/kvad/internal/_c.py").write_text(CANARIES[0][2], encoding="utf-8")
    return "error EIT001" not in check(root)


def _report(ok: bool, passed: str, failed: str) -> bool:
    print(passed if ok else failed)
    return ok


def _fixture_canaries(root: Path) -> list[bool]:
    """Every canary against the fixture tree, in order, each printed as it runs."""
    results = [
        _report(_bites(root, rule, rel, code), f"canary ok:   {rule} bit", f"CANARY FAIL: {rule} did not bite")
        for rule, rel, code in CANARIES
    ]
    results.append(_report("error EIT100" in check(root, "--budget", "100"), "canary ok:   EIT100 bit", "CANARY FAIL: EIT100"))
    results.append(
        _report(
            _disarmed(root),
            "canary note: 'enabled = false' silently disarms every wall — keep Brokkr in CI",
            "CANARY FAIL: enabled=false did not disarm (config not honored?)",
        )
    )
    return results


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # diagnostics contain em dashes
    real = check(REPO)
    if " error " in real:
        print("CANARY FAIL: the real repo does not pass eitri check — fix that first:\n" + real)
        return 1
    print("canary ok:   real repo passes (walls do not bite on clean work)")
    with tempfile.TemporaryDirectory(prefix="brokkr-") as tmp:
        root = write_fixture_tree(Path(tmp))
        baseline = check(root)
        if " error " in baseline:
            print("CANARY FAIL: the fixture tree does not pass — fix that first:\n" + baseline)
            return 1
        results = [True, *_fixture_canaries(root)]
    failed = results.count(False)
    print("---")
    print(f"canary: {len(results) - failed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(main())
