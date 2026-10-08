"""Claude Code ``Stop`` hook: the agent may not finish until the tests pass and the review has run.

stdin: the Stop event JSON. Runs ``pytest`` and then ``heimdall review --base origin/main``.
A failing suite or a ``request_changes`` verdict blocks the stop (exit 2, reason on stderr) so the
agent keeps working; ``escalate`` and a review that could not run (no ``TYPESAFE_API_KEY``) are
reported to the user without blocking. Nothing to review (no diff from origin/main, clean tree)
exits 0 at once. ``stop_hook_active`` exits 0 so a block can never loop.
"""

from __future__ import annotations

import json
import subprocess
import sys

REVIEW = [sys.executable, "-m", "heimdall", "review", "--base", "origin/main"]
TESTS = [sys.executable, "-m", "pytest", "-q"]
TAIL = 40  # lines of output the agent gets back on a block

EXIT_REQUEST_CHANGES, EXIT_ESCALATE, EXIT_ERROR = 1, 2, 3


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL:])


def _nothing_changed() -> bool:
    _, status = _run(["git", "status", "--porcelain"])
    committed, _ = _run(["git", "diff", "--quiet", "origin/main...HEAD"])
    return not status and committed == 0


def _block(reason: str) -> int:
    sys.stderr.buffer.write((reason + "\n").encode("utf-8"))
    return 2


def _tell(message: str) -> int:
    sys.stdout.write(json.dumps({"systemMessage": message}) + "\n")
    return 0


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        event = {}
    if event.get("stop_hook_active") or _nothing_changed():
        return 0

    code, out = _run(TESTS)
    if code != 0:
        return _block("Stop hook: pytest failed. Fix the suite before finishing.\n\n" + _tail(out))

    code, out = _run(REVIEW)
    if code == EXIT_REQUEST_CHANGES:
        return _block("Stop hook: heimdall review asked for changes. Fix what it names before finishing.\n\n" + _tail(out))
    if code == EXIT_ESCALATE:
        return _tell("heimdall review: escalate — the judge wants a human to look.\n" + _tail(out))
    if code == EXIT_ERROR:
        return _tell("heimdall review did not run (" + _tail(out) + "). The PR check will judge the branch once it is pushed.")
    return _tell("pytest green; heimdall review: " + _tail(out).splitlines()[0] if out else "pytest green; review ran.")


if __name__ == "__main__":
    sys.exit(main())
