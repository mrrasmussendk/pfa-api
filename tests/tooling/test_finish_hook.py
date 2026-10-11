"""The ``Stop`` hook (``.claude/hooks/finish.py``): which outcome of the suite, the scenarios and the review
blocks the stop, and which is only told to the user. The commands are replaced by a fake that answers by name."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "finish.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("finish_hook", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The checks of the change, in the order the hook runs them after a green suite.
CHANGE = ["coverage-json", "diff-coverage", "eitri", "openapi"]


def _name(cmd: list[str]) -> str:
    """What a command is, by what it runs."""
    text = " ".join(cmd)
    for needle, name in (
        ("pytest", "pytest"),
        ("coverage json", "coverage-json"),
        ("diff_coverage", "diff-coverage"),
        ("eitri", "eitri"),
        ("openapi_compat", "openapi"),
        ("heimdall drift", "drift"),
        ("heimdall e2e", "e2e"),
        ("heimdall review", "review"),
    ):
        if needle in text:
            return name
    return "git"


def _stop(
    monkeypatch: pytest.MonkeyPatch, event: dict[str, object] | None = None, covered: bool = True, **codes: int
) -> tuple[int, list[str]]:
    """Run the hook with each command answering ``codes[name]`` (0 when unnamed); the commands that ran, in order."""
    finish, ran = _load(), []

    def fake(cmd: list[str]) -> tuple[int, str]:
        name = _name(cmd)
        ran.append(name)
        return codes.get(name.replace("-", "_"), 0), f"{name} output"

    monkeypatch.setattr(finish, "_run", fake)
    monkeypatch.setattr(finish, "_nothing_changed", lambda: False)
    monkeypatch.setattr(finish, "_covered", lambda: covered)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event or {})))
    return finish.main(), ran


def test_a_red_suite_blocks_before_anything_else_runs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, ran = _stop(monkeypatch, pytest=1)

    assert code == 2 and ran == ["pytest"] and "pytest failed" in capsys.readouterr().err


def test_a_missing_scenario_blocks_before_the_review(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, ran = _stop(monkeypatch, e2e=2)

    assert code == 2 and ran == ["pytest", *CHANGE, "e2e"] and "asks for scenarios" in capsys.readouterr().err


def test_request_changes_blocks_the_stop(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, ran = _stop(monkeypatch, review=1)

    assert code == 2 and ran == ["pytest", *CHANGE, "e2e", "review"] and "asked for changes" in capsys.readouterr().err


def test_escalate_is_told_to_the_user_without_blocking(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = _stop(monkeypatch, review=2)

    assert code == 0 and "escalate" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_a_review_that_could_not_run_is_told_to_the_user(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = _stop(monkeypatch, review=3)

    assert code == 0 and "did not run" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_an_active_stop_hook_never_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    code, ran = _stop(monkeypatch, {"stop_hook_active": True}, pytest=1)

    assert code == 0 and ran == []


@pytest.mark.parametrize(
    ("failing", "says"),
    [("diff_coverage", "run under no test"), ("eitri", "contract change breaks"), ("openapi", "HTTP API breaks clients")],
)
def test_a_change_that_breaks_a_promise_blocks_before_the_scenarios(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failing: str, says: str
) -> None:
    code, ran = _stop(monkeypatch, **{failing: 1})

    assert code == 2 and "e2e" not in ran and says in capsys.readouterr().err


def test_without_coverage_the_suite_runs_plain_and_the_diff_coverage_check_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    code, ran = _stop(monkeypatch, covered=False)

    assert code == 0 and ran[:3] == ["pytest", "eitri", "openapi"]


def test_the_session_s_drift_is_told_to_the_user_with_the_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, ran = _stop(monkeypatch, {"session_id": "s1"})

    assert code == 0 and "drift" in ran and "drift output" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_without_a_session_no_drift_is_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    _, ran = _stop(monkeypatch)

    assert "drift" not in ran


def test_a_review_block_names_the_failures_even_when_unsure_rows_would_fill_the_tail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    finish = _load()
    review = "swallows_error (fail)   a.py:1 f   0.75\n" + "".join(f"untested (unsure)   b.py:{i} g   0.45\n" for i in range(100))
    monkeypatch.setattr(finish, "_run", lambda cmd: (1, review) if "heimdall review" in " ".join(cmd) else (0, ""))
    monkeypatch.setattr(finish, "_nothing_changed", lambda: False)
    monkeypatch.setattr(finish, "_covered", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    code = finish.main()

    err = capsys.readouterr().err
    assert code == 2 and "swallows_error (fail)" in err and "(unsure)" not in err
