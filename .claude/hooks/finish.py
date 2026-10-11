"""Claude Code ``Stop`` hook: the agent may not finish until the tests pass, the BDD scenarios the change
reaches pass, and the review has run.

stdin: the Stop event JSON. Runs ``pytest``, then ``heimdall e2e --base origin/main`` (Jev picks the
scenarios to rerun and says whether one is missing), then ``heimdall review --base origin/main``.
A failing suite, a failing or missing scenario, or a ``request_changes`` verdict blocks the stop (exit 2,
reason on stderr) so the agent keeps working; ``escalate``, e2e that could not run (no Postman CLI, the
service would not start) and a review that could not run (no ``TYPESAFE_API_KEY``) are reported to the
user without blocking. Nothing to review (no diff from origin/main, clean tree)
exits 0 at once. ``stop_hook_active`` exits 0 so a block can never loop.
"""

from __future__ import annotations

import json
import subprocess
import sys

REVIEW = [sys.executable, "-m", "heimdall", "review", "--base", "origin/main"]
TESTS = [sys.executable, "-m", "pytest", "-q"]
E2E = [sys.executable, "-m", "heimdall", "e2e", "--base", "origin/main"]
TAIL = 40  # lines of output the agent gets back on a block

EXIT_REQUEST_CHANGES, EXIT_ESCALATE, EXIT_ERROR = 1, 2, 3
E2E_FAILED, E2E_NEEDS_SCENARIOS, E2E_ERROR = 1, 2, 3


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
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


def _e2e() -> int | str:
    """The scenarios the change reaches: the exit code of a block, or one line for the user."""
    code, out = _run(E2E)
    if code == E2E_FAILED:
        return _block("Stop hook: an e2e scenario failed. Fix the behaviour, or the scenario if the change is meant.\n\n" + _tail(out))
    if code == E2E_NEEDS_SCENARIOS:
        return _block(
            "Stop hook: heimdall e2e asks for scenarios. Run the `heimdall e2e new` it names (or fix the file it names), then make the test it writes assert each `then` clause under its name.\n\n"
            + _tail(out)
        )
    if code == E2E_ERROR:
        return "heimdall e2e did not run (" + _tail(out) + ")."
    return out.splitlines()[-1] if out else "heimdall e2e ran."


def _review(e2e: str) -> int:
    """Jev's review of the committed diff: a block, or a message for the user that leads with the e2e line."""
    code, out = _run(REVIEW)
    if code == EXIT_REQUEST_CHANGES:
        return _block("Stop hook: heimdall review asked for changes. Fix what it names before finishing.\n\n" + _tail(out))
    if code == EXIT_ESCALATE:
        return _tell(e2e + "\nheimdall review: escalate — the judge wants a human to look.\n" + _tail(out))
    if code == EXIT_ERROR:
        return _tell(e2e + "\nheimdall review did not run (" + _tail(out) + "). The PR check will judge the branch once it is pushed.")
    return _tell(e2e + "\npytest green; heimdall review: " + (_tail(out).splitlines()[0] if out else "ran."))


def _event() -> dict[str, object]:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return event if isinstance(event, dict) else {}


def main() -> int:
    """The suite, then the scenarios, then the review; the first that blocks ends the stop."""
    if _event().get("stop_hook_active") or _nothing_changed():
        return 0
    code, out = _run(TESTS)
    if code != 0:
        return _block("Stop hook: pytest failed. Fix the suite before finishing.\n\n" + _tail(out))
    e2e = _e2e()
    return e2e if isinstance(e2e, int) else _review(e2e)


if __name__ == "__main__":
    sys.exit(main())
