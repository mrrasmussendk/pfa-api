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
]


def check(root: Path, *extra: str) -> str:
    out, err = io.StringIO(), io.StringIO()
    run(["check", "--root", str(root), *extra], out, err)
    return out.getvalue() + err.getvalue()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # diagnostics contain em dashes
    except (AttributeError, ValueError):
        pass
    passed = failed = 0

    real = check(REPO)
    if " error " in real:
        print("CANARY FAIL: the real repo does not pass eitri check — fix that first:\n" + real)
        return 1
    print("canary ok:   real repo passes (walls do not bite on clean work)")
    passed += 1

    with tempfile.TemporaryDirectory(prefix="brokkr-") as tmp:
        root = write_fixture_tree(Path(tmp))
        baseline = check(root)
        if " error " in baseline:
            print("CANARY FAIL: the fixture tree does not pass — fix that first:\n" + baseline)
            return 1

        for rule, rel, code in CANARIES:
            target = root / rel
            target.write_text(code, encoding="utf-8")
            if f"error {rule}" in check(root):
                print(f"canary ok:   {rule} bit")
                passed += 1
            else:
                print(f"CANARY FAIL: {rule} did not bite")
                failed += 1
            target.unlink()

        if "error EIT100" in check(root, "--budget", "100"):
            print("canary ok:   EIT100 bit")
            passed += 1
        else:
            print("CANARY FAIL: EIT100")
            failed += 1

        # disarming the config is the footgun Brokkr exists for
        (root / "pyproject.toml").write_text("[tool.eitri]\nenabled = false\n", encoding="utf-8")
        (root / "pfa/features/kvad/internal/_c.py").write_text(CANARIES[0][2], encoding="utf-8")
        if "error EIT001" not in check(root):
            print("canary note: 'enabled = false' silently disarms every wall — keep Brokkr in CI")
            passed += 1
        else:  # pragma: no cover
            print("CANARY FAIL: enabled=false did not disarm (config not honored?)")
            failed += 1

    print("---")
    print(f"canary: {passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(main())
