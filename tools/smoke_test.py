"""End-to-end harness test: feedforward -> sense -> feedback -> drift.

Drives the real ``heimdall`` entry point as a subprocess, the way Claude Code runs the
PostToolUse hook (fresh process per event, CWD = project root), against the synthetic
fixture tree, and prints the measured hook latency.

Run from the repo root:  python tools/smoke_test.py
"""

from __future__ import annotations

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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    env = {**os.environ, "PYTHONPATH": str(TOOLS)}
    ok = True

    def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*HEIMDALL, *args], input=stdin, capture_output=True, text=True, encoding="utf-8", cwd=cwd, env=env, check=False
        )

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        print(("ok: " if cond else "FAIL: ") + msg)
        ok = ok and cond

    def ev(session: str, tool: str, path: str) -> str:
        return json.dumps({"session_id": session, "tool_name": tool, "tool_input": {"file_path": path}})

    with tempfile.TemporaryDirectory(prefix="heimdall-smoke-") as tmp:
        cwd = tmp
        write_fixture_tree(Path(tmp) / "src")

        r = run("map", "--root", ".", "--budget", "15000")
        check(r.returncode == 0 and "2 slices" in r.stdout, "feedforward map emitted")
        agents = (Path(tmp) / "src/slices/kvad/AGENTS.md").read_text(encoding="utf-8")
        check("heimdall:deps" in agents, "AGENTS.md deps generated from slice.json")

        run("hook", stdin=ev("s1", "Read", "src/slices/kvad/internal/kvad_engine.py"))
        run("hook", stdin=ev("s1", "Edit", "src/slices/kvad/internal/kvad_engine.py"))
        run("hook", stdin=ev("s1", "Read", "src/slices/rune/contract/rune_service.py"))
        run("hook", stdin=ev("s1", "Read", "src/slices/rune/internal/rune_engine.py"))  # OOB read
        r = run("hook", stdin=ev("s1", "Edit", "src/slices/rune/internal/rune_engine.py"))
        check(r.returncode == 2 and "cross-slice" in r.stderr, "feedback fired on second-slice edit (exit 2 -> agent sees it)")

        # clean session: edits kvad only, reads a foreign internal -> must count as OOB
        run("hook", stdin=ev("s2", "Edit", "src/slices/kvad/internal/kvad_service.py"))
        run("hook", stdin=ev("s2", "Read", "src/slices/kvad/internal/kvad_engine.py"))
        run("hook", stdin=ev("s2", "Read", "src/slices/rune/internal/rune_engine.py"))
        drift = run("drift").stdout
        check("kvad                        5              1     20%" in drift, "OOB read detected and attributed")
        lines = (Path(tmp) / ".heimdall/telemetry.jsonl").read_text(encoding="utf-8").count("\n")
        check(lines >= 5, f"telemetry logged ({lines} findings)")

        est = run("estimate", "src").stdout.strip()
        check(est.isdigit() and int(est) > 0, f"estimate runs on the fixture tree ({est} tokens)")

        # hook latency: this runs as a PostToolUse hook on EVERY tool call — must stay cheap.
        n = 20
        event = ev("s1", "Read", "src/slices/kvad/internal/kvad_engine.py")
        start = time.perf_counter()
        for _ in range(n):
            run("hook", stdin=event)
        ms = (time.perf_counter() - start) * 1000 / n
        print(f"hook latency: {ms:.1f} ms/event (n={n}, full interpreter start-up included)")

    print("--- heimdall smoke: all green" if ok else "--- heimdall smoke: FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
