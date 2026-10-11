"""Claude Code ``Stop`` hook: the agent may not finish until the tests pass, the change holds its promises,
the BDD scenarios the change reaches pass, and the review has run.

stdin: the Stop event JSON. In order, the first that blocks ends the stop:

1. ``pytest``, under coverage when coverage is installed.
2. The change, each against ``origin/main``: the changed application lines the tests ran
   (``tools/diff_coverage.py``), contracts changed additively (``eitri check --base``, EIT008), and the HTTP
   API still compatible (``tools/openapi_compat.py``).
3. ``heimdall e2e --base origin/main``: Jev picks the scenarios to rerun and says whether one is missing.
4. ``heimdall review --base origin/main``.

A failure in 1–3 or a ``request_changes`` verdict blocks the stop (exit 2, reason on stderr) so the agent keeps
working; ``escalate``, e2e that could not run (no Postman CLI, the service would not start) and a review that
could not run (no ``TYPESAFE_API_KEY``) are reported to the user without blocking, together with the session's
drift: how much of its reading fell outside the working set. Nothing to review (no diff from origin/main, clean
tree) exits 0 at once. ``stop_hook_active`` exits 0 so a block can never loop.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys

BASE = "origin/main"
PY = sys.executable
REVIEW = [PY, "-m", "heimdall", "review", "--base", BASE]
TESTS = [PY, "-m", "pytest", "-q"]
COVERED_TESTS = [PY, "-m", "coverage", "run", "-m", "pytest", "-q"]
COVERAGE_JSON = [PY, "-m", "coverage", "json", "-q"]
E2E = [PY, "-m", "heimdall", "e2e", "--base", BASE]
DRIFT = [PY, "-m", "heimdall", "drift", "--session"]
# Each check of the change, with what to do when it fails.
CHANGE = [
    (
        [PY, "tools/diff_coverage.py", "--base", BASE],
        "Stop hook: changed lines run under no test. Write the tests that run them (harness/guides/writing-tests.md).",
    ),
    (
        [PY, "-m", "eitri", "check", "--root", ".", "--base", BASE, "--no-info"],
        "Stop hook: a contract change breaks what still uses it. Change it in steps (harness/guides/changing-a-contract.md).",
    ),
    (
        [PY, "tools/openapi_compat.py", "--base", BASE],
        "Stop hook: the HTTP API breaks clients written against main. Keep the old shape beside the new, or raise the major version.",
    ),
]
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
    committed, _ = _run(["git", "diff", "--quiet", f"{BASE}...HEAD"])
    return not status and committed == 0


def _covered() -> bool:
    return importlib.util.find_spec("coverage") is not None


def _block(reason: str) -> int:
    sys.stderr.buffer.write((reason + "\n").encode("utf-8"))
    return 2


def _tell(message: str) -> int:
    sys.stdout.write(json.dumps({"systemMessage": message}) + "\n")
    return 0


def _tests(covered: bool) -> int | None:
    """The suite: the exit code of a block, or None when it is green."""
    code, out = _run(COVERED_TESTS if covered else TESTS)
    return _block("Stop hook: pytest failed. Fix the suite before finishing.\n\n" + _tail(out)) if code != 0 else None


def _change(covered: bool) -> int | None:
    """The change held to its promises: the exit code of the first block, or None when it holds them all.
    Without coverage the diff-coverage check is skipped: the suite ran without measuring."""
    checks = CHANGE if covered else CHANGE[1:]
    if covered:
        _run(COVERAGE_JSON)
    for cmd, reason in checks:
        code, out = _run(cmd)
        if code != 0:
            return _block(reason + "\n\n" + _tail(out))
    return None


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


def _drift(session: object) -> str:
    """The session's out-of-bounds reading, one line; nothing when there is no session or no telemetry."""
    if not isinstance(session, str) or not session:
        return ""
    code, out = _run([*DRIFT, session])
    return "\n" + out.splitlines()[-1] if code == 0 and out else ""


def _review(e2e: str, drift: str) -> int:
    """Jev's review of the committed diff: a block, or a message for the user that leads with the e2e line."""
    code, out = _run(REVIEW)
    if code == EXIT_REQUEST_CHANGES:
        return _block("Stop hook: heimdall review asked for changes. Fix what it names before finishing.\n\n" + _tail(out))
    if code == EXIT_ESCALATE:
        return _tell(e2e + "\nheimdall review: escalate — the judge wants a human to look.\n" + _tail(out) + drift)
    if code == EXIT_ERROR:
        return _tell(
            e2e + "\nheimdall review did not run (" + _tail(out) + "). The PR check will judge the branch once it is pushed." + drift
        )
    return _tell(e2e + "\npytest green; heimdall review: " + (_tail(out).splitlines()[0] if out else "ran.") + drift)


def _event() -> dict[str, object]:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return event if isinstance(event, dict) else {}


def main() -> int:
    """The suite, the change, the scenarios, then the review; the first that blocks ends the stop."""
    event = _event()
    if event.get("stop_hook_active") or _nothing_changed():
        return 0
    covered = _covered()
    blocked = _tests(covered)
    if blocked is None:
        blocked = _change(covered)
    if blocked is not None:
        return blocked
    e2e = _e2e()
    return e2e if isinstance(e2e, int) else _review(e2e, _drift(event.get("session_id")))


if __name__ == "__main__":
    sys.exit(main())
