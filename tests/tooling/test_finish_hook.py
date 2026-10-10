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


def _stop(monkeypatch: pytest.MonkeyPatch, event: dict[str, object] | None = None, **codes: int) -> tuple[int, list[str]]:
    """Run the hook with each command answering ``codes[name]`` (0 when unnamed); the commands that ran, in order."""
    finish, ran = _load(), []

    def fake(cmd: list[str]) -> tuple[int, str]:
        name = "pytest" if "pytest" in cmd else cmd[3] if cmd[:3] == finish.REVIEW[:3] else "git"
        ran.append(name)
        return codes.get(name, 0), f"{name} output"

    monkeypatch.setattr(finish, "_run", fake)
    monkeypatch.setattr(finish, "_nothing_changed", lambda: False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event or {})))
    return finish.main(), ran


def test_a_red_suite_blocks_before_anything_else_runs(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, ran = _stop(monkeypatch, pytest=1)

    assert code == 2 and ran == ["pytest"] and "pytest failed" in capsys.readouterr().err


def test_a_missing_scenario_blocks_before_the_review(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, ran = _stop(monkeypatch, e2e=2)

    assert code == 2 and ran == ["pytest", "e2e"] and "asks for scenarios" in capsys.readouterr().err


def test_request_changes_blocks_the_stop(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, ran = _stop(monkeypatch, review=1)

    assert code == 2 and ran == ["pytest", "e2e", "review"] and "asked for changes" in capsys.readouterr().err


def test_escalate_is_told_to_the_user_without_blocking(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = _stop(monkeypatch, review=2)

    assert code == 0 and "escalate" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_a_review_that_could_not_run_is_told_to_the_user(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    code, _ = _stop(monkeypatch, review=3)

    assert code == 0 and "did not run" in json.loads(capsys.readouterr().out)["systemMessage"]


def test_an_active_stop_hook_never_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    code, ran = _stop(monkeypatch, {"stop_hook_active": True}, pytest=1)

    assert code == 0 and ran == []
