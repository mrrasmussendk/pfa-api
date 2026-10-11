"""End-to-end harness test: feedforward -> sense -> feedback -> drift.

Drives the real ``heimdall`` entry point as a subprocess, the way Claude Code runs the
PostToolUse hook (fresh process per event, CWD = project root), against the synthetic
fixture tree, and prints the measured hook latency.

Run from the repo root:  python tools/smoke_test.py
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

from fixtures import write_fixture_tree  # noqa: E402

HEIMDALL = [sys.executable, "-m", "heimdall"]
# The hook runs on every agent tool call. Typically ~25 ms locally and a few hundred on a cold Windows runner; an
# accidental import of the service, torch or a model costs seconds.
LATENCY_CEILING_MS = 750


def _ev(session: str, tool: str, path: str) -> str:
    return json.dumps({"session_id": session, "tool_name": tool, "tool_input": {"file_path": path}})


class _Smoke:
    """Runs Heimdall as a subprocess in a fixture tree, and keeps the verdict of every check."""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.env = {**os.environ, "PYTHONPATH": str(TOOLS)}
        self.ok = True

    def run(self, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*HEIMDALL, *args], input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=self.cwd, env=self.env, check=False
        )

    def check(self, cond: bool, msg: str) -> None:
        print(("ok: " if cond else "FAIL: ") + msg)
        self.ok = self.ok and cond


def _feedforward(sm: _Smoke) -> None:
    r = sm.run("map", "--root", ".", "--budget", "15000")
    sm.check(r.returncode == 0 and "2 features" in r.stdout, "feedforward map emitted")
    agents = (Path(sm.cwd) / "src/pfa/features/kvad/AGENTS.md").read_text(encoding="utf-8")
    sm.check("heimdall:deps" in agents, "AGENTS.md deps generated from feature.json")


def _feedback(sm: _Smoke) -> None:
    sm.run("hook", stdin=_ev("s1", "Read", "src/pfa/features/kvad/internal/kvad_engine.py"))
    sm.run("hook", stdin=_ev("s1", "Edit", "src/pfa/features/kvad/internal/kvad_engine.py"))
    sm.run("hook", stdin=_ev("s1", "Read", "src/pfa/features/rune/contract/rune_service.py"))
    sm.run("hook", stdin=_ev("s1", "Read", "src/pfa/features/rune/internal/rune_engine.py"))  # OOB read
    r = sm.run("hook", stdin=_ev("s1", "Edit", "src/pfa/features/rune/internal/rune_engine.py"))
    sm.check(r.returncode == 2 and "cross-slice" in r.stderr, "feedback fired on second-slice edit (exit 2 -> agent sees it)")


def _drift(sm: _Smoke) -> None:
    # clean session: edits kvad only, reads a foreign internal -> must count as OOB
    sm.run("hook", stdin=_ev("s2", "Edit", "src/pfa/features/kvad/internal/kvad_service.py"))
    sm.run("hook", stdin=_ev("s2", "Read", "src/pfa/features/kvad/internal/kvad_engine.py"))
    sm.run("hook", stdin=_ev("s2", "Read", "src/pfa/features/rune/internal/rune_engine.py"))
    sm.check("kvad                        5              1     20%" in sm.run("drift").stdout, "OOB read detected and attributed")
    lines = (Path(sm.cwd) / ".heimdall/telemetry.jsonl").read_text(encoding="utf-8").count("\n")
    sm.check(lines >= 5, f"telemetry logged ({lines} findings)")
    est = sm.run("estimate", "src").stdout.strip()
    sm.check(est.isdigit() and int(est) > 0, f"estimate runs on the fixture tree ({est} tokens)")


def _latency(sm: _Smoke) -> None:
    """The hook runs as a PostToolUse hook on EVERY tool call, so it must stay cheap."""
    n = 20
    event = _ev("s1", "Read", "src/pfa/features/kvad/internal/kvad_engine.py")
    start = time.perf_counter()
    for _ in range(n):
        sm.run("hook", stdin=event)
    ms = (time.perf_counter() - start) * 1000 / n
    print(f"hook latency: {ms:.1f} ms/event (n={n}, full interpreter start-up included)")
    sm.check(ms <= LATENCY_CEILING_MS, f"hook latency within {LATENCY_CEILING_MS} ms (an import of the service or a model would blow it)")


def main() -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory(prefix="heimdall-smoke-") as tmp:
        write_fixture_tree(Path(tmp) / "src")
        sm = _Smoke(tmp)
        for step in (_feedforward, _feedback, _drift, _latency):
            step(sm)
    print("--- heimdall smoke: all green" if sm.ok else "--- heimdall smoke: FAILED")
    return 0 if sm.ok else 1


if __name__ == "__main__":
    sys.exit(main())
