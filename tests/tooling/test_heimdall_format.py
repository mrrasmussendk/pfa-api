"""Byte-format contracts for the files Heimdall writes (port of FormatTests.cs)."""

from __future__ import annotations

from conftest import TempRepo

from brokkr.tokenization import estimate
from heimdall.model import Finding, HookEvent, MapModel


def test_finding_line_uses_json_dumps_separators_and_key_order() -> None:
    f = Finding(event="read", path="a/b.py", kind="slice:kvad", sensor="boundary_reads", ts=1751812345.5, session="s1")
    assert f.to_line() == (
        '{"event": "read", "path": "a/b.py", "kind": "slice:kvad", "sensor": "boundary_reads", "ts": 1751812345.5, "session": "s1"}'
    )
    err = Finding(sensor="heimdall", error="boom", ts=2.0)
    assert err.to_line() == '{"sensor": "heimdall", "error": "boom", "ts": 2.0}'


def test_finding_line_escapes_non_ascii() -> None:
    f = Finding(event="edit", feedback="— frozen —", ts=1.0)
    assert f.to_line() == '{"event": "edit", "feedback": "\\u2014 frozen \\u2014", "ts": 1.0}'


def test_hook_event_path_resolution() -> None:
    e = HookEvent.from_dict({"tool_name": "Grep", "tool_input": {"path": "x/y.py"}})
    assert e.read_path == "x/y.py"
    assert e.edit_path == ""  # boundary_edits has no `path` fallback
    assert e.session_or_q == "?"
    e2 = HookEvent.from_dict({"session_id": "", "tool_input": "not a dict"})
    assert e2.session_or_q == "?" and e2.tool_input == {}


def test_map_model_tolerates_missing_fields() -> None:
    m = MapModel.from_dict({"slices": {"kvad": {"depends_on": None}}})
    assert m.kernel == "kernel"
    assert m.slices["kvad"].depends_on == [] and m.slices["kvad"].fan_in == 0


def test_estimate_file_matches_estimator(repo: TempRepo) -> None:
    src = "class Thing:\n    def answer(self) -> int:\n        return 42\n"
    repo.write_file("src/thing.py", src)
    code, stdout, _ = repo.run("", "estimate", "src/thing.py")
    assert code == 0
    assert stdout.strip() == str(estimate(src))


def test_estimate_directory_sums_all_py_files(repo: TempRepo) -> None:
    repo.write_file("tree/a.py", "class A: ...")
    repo.write_file("tree/sub/b.py", "class B:\n    x = 123\n")
    repo.write_file("tree/ignored.txt", "not counted")
    repo.write_file("tree/__pycache__/c.py", "not counted either")
    expected = estimate("class A: ...") + estimate("class B:\n    x = 123\n")
    code, stdout, _ = repo.run("", "estimate", "tree")
    assert code == 0
    assert stdout.strip() == str(expected)


def test_estimate_missing_path_exit1(repo: TempRepo) -> None:
    code, _, stderr = repo.run("", "estimate", "nope.py")
    assert code == 1
    assert "no such file" in stderr


def test_estimate_usage_exit2(repo: TempRepo) -> None:
    assert repo.run("", "estimate")[0] == 2
