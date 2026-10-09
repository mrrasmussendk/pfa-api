"""``heimdall review``: diff -> bounded state -> one Jev call about the diff and one per edited application
file, whole -> policy -> outcome. The network is replaced by a fake transport; the policy and the state are
what is tested."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from conftest import SHAPED_SOURCE, TempProject, TempRepo

from brokkr import estimate
from heimdall.commands import review
from heimdall.commands.review import (
    EXIT_ERROR,
    EXIT_ESCALATE,
    EXIT_OK,
    EXIT_REQUEST_CHANGES,
    ReviewError,
    build_state,
    craft_files,
    craft_state,
    decide,
    file_questions,
    function_shape,
    parse_unified_diff,
    questions,
    render_markdown,
    render_table,
    wall_findings,
)
from heimdall.commands.review.state import area_of
from heimdall.model import MapModel

DIFF = """\
diff --git a/src/pfa/api/routes/kvad.py b/src/pfa/api/routes/kvad.py
index 1111111..2222222 100644
--- a/src/pfa/api/routes/kvad.py
+++ b/src/pfa/api/routes/kvad.py
@@ -1,4 +1,5 @@
 from fastapi import APIRouter
+from pfa.api.problems import Problem
 router = APIRouter(prefix="/kvad")
-    raise HTTPException(422)
+    raise Problem.domain_rejection(result.error)
diff --git a/src/pfa/features/rune/contract/rune_service.py b/src/pfa/features/rune/contract/rune_service.py
index 1111111..2222222 100644
--- a/src/pfa/features/rune/contract/rune_service.py
+++ b/src/pfa/features/rune/contract/rune_service.py
@@ -1,2 +1,3 @@
 class RuneService:
+    def new_member(self): ...
     pass
diff --git a/tests/api/test_kvad.py b/tests/api/test_kvad.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/api/test_kvad.py
@@ -0,0 +1,2 @@
+def test_it():
+    assert True
diff --git a/harness/guides/x.md b/harness/guides/x.md
index 1111111..2222222 100644
--- a/harness/guides/x.md
+++ b/harness/guides/x.md
@@ -1 +1 @@
-old
+new
diff --git a/poetry.lock b/poetry.lock
index 1111111..2222222 100644
--- a/poetry.lock
+++ b/poetry.lock
@@ -1 +1 @@
-a
+b
"""


def _map() -> MapModel:
    return MapModel.from_dict(
        {
            "kernel": "kernel",
            "kernel_dir": "src/pfa/kernel",
            "slices_dir": "src/pfa/features",
            "app_dir": "src/pfa",
            "routes_dir": "src/pfa/api/routes",
            "shared": ["http_common"],
            "slices": {
                "kvad": {"path": "src/pfa/features/kvad", "depends_on": ["rune"], "budget": 15000, "fan_in": 0},
                "rune": {"path": "src/pfa/features/rune", "depends_on": [], "budget": 15000, "fan_in": 12},
            },
        }
    )


def _answers(**over: Any) -> dict[str, Any]:
    """A clean, confident, mergeable review; override individual answers per test."""
    base: dict[str, Any] = {
        "safe_to_merge": {"type": "noul", "noul": 0.9},
        "needs_human_review": {"type": "noul", "noul": 0.1},
        "has_security_concern": {"type": "noul", "noul": 0.02},
        "tests_cover_change": {"type": "noul", "noul": 0.8},
        "stays_in_scope": {"type": "noul", "noul": 0.9},
        "respects_slice_walls": {"type": "noul", "noul": 0.95},
        "errors_use_problem_details": {"type": "noul", "noul": 0.95},
        "leaks_internals": {"type": "noul", "noul": 0.01},
        "correctness": {
            "type": "score",
            "score": 2.7,
            "legend": {"0": "Likely broken", "1": "Possibly defective", "2": "Probably correct", "3": "Clearly correct"},
            "probabilities": {"0": 0.0, "1": 0.05, "2": 0.2, "3": 0.75},
            "confidence": 0.8,
        },
        "single_purpose": {"type": "noul", "noul": 0.9},
        "lean_signatures": {"type": "noul", "noul": 0.9},
        "blast_radius": {
            "type": "score",
            "score": 1.0,
            "legend": {"0": "Contained", "1": "Moderate", "2": "Wide"},
            "probabilities": {"0": 0.1, "1": 0.8, "2": 0.1},
            "confidence": 0.8,
        },
        "docs_in_sync": {
            "type": "score",
            "score": 1.9,
            "legend": {"0": "Docs now stale", "1": "Partially updated", "2": "In sync"},
            "probabilities": {"0": 0.0, "1": 0.1, "2": 0.9},
            "confidence": 0.9,
        },
        "change_kind": {"type": "choice", "choice": "fix", "probabilities": {"fix": 0.8, "feature": 0.2}, "confidence": 0.8},
        "biggest_risk": {"type": "choice", "choice": "none", "probabilities": {"none": 0.7, "tests": 0.3}, "confidence": 0.6},
    }
    base.update(over)
    return base


def _file_answers(score: float = 2.9, limit: dict[str, Any] | None = None) -> dict[str, Any]:
    """One file's clean-code answers, as the craft pass gets them: exemplary unless told otherwise."""
    return {
        "clean_code": {
            "type": "score",
            "score": score,
            "legend": {"0": "Hard to follow", "1": "Acceptable", "2": "Clean", "3": "Exemplary"},
            "probabilities": {"0": 0.0, "1": 0.0, "2": 0.1, "3": 0.9},
            "confidence": 0.8,
        },
        "clean_code_limit": limit
        or {"type": "choice", "choice": "none", "probabilities": {"none": 0.9, "comments": 0.1}, "confidence": 0.8},
    }


def _transport(answers: dict[str, Any], seen: list[dict[str, Any]] | None = None, files: dict[str, dict[str, Any]] | None = None):
    """A fake Jev: ``answers`` for the diff call, ``files[path]`` (or exemplary) for each file judged whole."""

    def call(payload: dict[str, Any]) -> dict[str, Any]:
        if seen is not None:
            seen.append(payload)
        state = payload["state"]
        reply = (files or {}).get(state["file"]["path"], _file_answers()) if "file" in state else answers
        return {"model": "jev-1.13.0", "answers": reply, "usage": {"input_tokens": 1234, "output_tokens": 15}}

    return call


def _run(repo: TempRepo, *args: str, transport=None, env: dict[str, str] | None = None, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    streams = review.Streams(io.StringIO(stdin), out, err)
    code = review.run(list(args), streams, str(repo.root), transport=transport, env=env if env is not None else {})
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- the diff and the state


def test_diff_is_parsed_per_file_and_generated_files_are_ignored() -> None:
    files = parse_unified_diff(DIFF)
    assert [f.path for f in files] == [
        "src/pfa/api/routes/kvad.py",
        "src/pfa/features/rune/contract/rune_service.py",
        "tests/api/test_kvad.py",
        "harness/guides/x.md",
    ]
    routes = files[0]
    assert (routes.status, routes.added, routes.removed) == ("modified", 2, 1)
    assert files[2].status == "added"
    assert "raise Problem.domain_rejection(result.error)" in routes.text


def test_state_carries_heimdalls_facts_and_the_architecture() -> None:
    state = build_state(parse_unified_diff(DIFF), _map(), "Switch kvad errors to Problem Details")
    facts = state["facts"]
    assert facts["slices_touched"] == ["kvad", "rune"] and facts["cross_slice_change"] is True
    assert facts["contracts_touched"] == {"rune": {"fan_in": 12, "frozen": True}}
    assert facts["routes_changed"] is True and facts["source_changed"] is True and facts["docs_changed"] is True
    assert facts["code_changed"] is True  # the application's Python changed: the craft targets apply
    assert facts["state_tokens"] == facts["diff_tokens"] > 0 and facts["files_truncated"] == []  # small enough to be seen whole
    assert facts["state_budget_tokens"] == 28_000 and facts["judge_window_tokens"] == 32_000
    assert facts["tests_changed"] == ["tests/api/test_kvad.py"]
    areas = {e["path"]: e["area"] for e in state["files"]}
    assert areas["src/pfa/api/routes/kvad.py"] == "slice:kvad"
    assert areas["src/pfa/features/rune/contract/rune_service.py"] == "contract:rune"
    assert areas["tests/api/test_kvad.py"] == "tests" and areas["harness/guides/x.md"] == "docs"
    assert state["architecture"]["slices"]["kvad"]["depends_on"] == ["rune"]
    assert any("RFC 9457" in r for r in state["architecture"]["rules"])
    assert state["task"].startswith("Switch kvad")


def test_oversized_files_are_truncated_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review.state, "MAX_FILE_TOKENS", 10)
    state = build_state(parse_unified_diff(DIFF), _map(), None)
    assert "src/pfa/api/routes/kvad.py" in state["facts"]["files_truncated"]
    big = next(e for e in state["files"] if e["path"].endswith("routes/kvad.py"))
    assert "more lines not shown" in big["diff"]
    assert state["facts"]["state_tokens"] < state["facts"]["diff_tokens"]  # the comment says how much the judge saw
    md = render_markdown(decide(_answers(), state["facts"], "m", {}), state, questions())
    assert "tokens sent of a 28,000 budget (Jev reads 32,000 with the questions); the whole diff is ~" in md
    assert "file(s) were cut and the craft scores rate what the judge saw" in md


def test_the_craft_floor_is_about_the_application_not_the_tooling(repo: TempRepo) -> None:
    repo.write_file("tools/heimdall/x.py", "a = 2\n")
    tooling = parse_unified_diff(
        "diff --git a/tools/heimdall/x.py b/tools/heimdall/x.py\n--- a/tools/heimdall/x.py\n+++ b/tools/heimdall/x.py\n"
        "@@ -1 +1 @@\n-a = 1\n+a = 2\n"
    )
    facts = build_state(tooling, _map(), None)["facts"]
    assert facts["code_changed"] is False and facts["source_changed"] is False
    assert craft_files(str(repo.root), tooling, _map()) == []  # the tooling is never judged whole
    # with no file judged there is no clean-code score, so nothing to send back for prose
    assert decide(_answers(), facts, "m", {}).outcome == "approve"
    v = decide(_answers(safe_to_merge={"type": "noul", "noul": 0.3}), facts, "m", {})
    assert v.outcome == "escalate" and "against the target" not in v.advice[0]


def test_the_features_package_init_is_the_application_not_a_slice() -> None:
    assert area_of("src/pfa/features/__init__.py", _map()) == "app"
    assert area_of("src/pfa/features/kvad/internal/x.py", _map()) == "slice:kvad"


def test_exported_diagrams_are_dropped_from_the_diff() -> None:
    svg = "diff --git a/docs/diagrams/x.svg b/docs/diagrams/x.svg\n--- a/docs/diagrams/x.svg\n+++ b/docs/diagrams/x.svg\n@@ -1 +1 @@\n-<svg/>\n+<svg></svg>\n"
    assert [f.path for f in parse_unified_diff(svg + DIFF)] == [f.path for f in parse_unified_diff(DIFF)]


def test_the_budget_is_spent_on_the_source_before_tests_and_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    files = parse_unified_diff(DIFF)
    files.reverse()  # git lists the docs first; the judge must still see the code
    source = [f for f in files if f.path.startswith("src/")]
    monkeypatch.setattr(review.state, "MAX_STATE_TOKENS", sum(estimate(f.text) for f in source))
    state = build_state(files, _map(), None)
    assert [e["area"] for e in state["files"]] == ["slice:kvad", "contract:rune", "tests", "docs"]
    assert not any(p.startswith("src/") for p in state["facts"]["files_truncated"])


def test_questions_are_valid_system_one_requests() -> None:
    qs, fq = questions(), file_questions()
    for key, q in {**qs, **fq}.items():
        assert q["type"] in ("noul", "choice", "score"), key
        assert q["instructions"], key
        if q["type"] == "noul":
            assert set(q["criteria"]) == {"true", "false"}, key
        elif q["type"] == "score":
            assert isinstance(q["criteria"], list) and 2 <= len(q["criteria"]) <= 10, key
        else:
            assert isinstance(q["criteria"], dict) and 1 < len(q["criteria"]) <= 255, key
    assert {"safe_to_merge", "needs_human_review", "has_security_concern", "correctness"} <= set(qs)
    assert {"single_purpose", "lean_signatures"} <= set(qs)  # function size, parameters, single purpose
    # clean code is asked about each edited application file whole, never about the diff
    assert set(fq) == {"clean_code", "clean_code_limit"} and not set(fq) & set(qs)
    assert "`file.content`" in fq["clean_code"]["instructions"] and "`facts.changed_lines`" in fq["clean_code"]["instructions"]


# ---------------------------------------------------------------- the policy


def test_policy_approves_a_clean_confident_change() -> None:
    v = decide(_answers(), {"source_changed": True, "routes_changed": True}, "jev-1", {})
    assert v.outcome == "approve" and v.exit_code == EXIT_OK


def test_policy_blocks_on_security_walls_or_correctness() -> None:
    facts = {"source_changed": True, "routes_changed": True}
    assert decide(_answers(has_security_concern={"type": "noul", "noul": 0.6}), facts, "m", {}).outcome == "request_changes"
    assert decide(_answers(respects_slice_walls={"type": "noul", "noul": 0.2}), facts, "m", {}).outcome == "request_changes"
    v = decide(_answers(correctness={"type": "score", "score": 0.9, "confidence": 0.8}), facts, "m", {})
    assert v.outcome == "request_changes" and any("correctness" in r for r in v.reasons)
    assert decide(_answers(errors_use_problem_details={"type": "noul", "noul": 0.1}), facts, "m", {}).outcome == "request_changes"
    # the same low errors answer does not block when no route changed
    assert (
        decide(_answers(errors_use_problem_details={"type": "noul", "noul": 0.1}), {"source_changed": True}, "m", {}).outcome == "approve"
    )


def test_policy_escalates_when_a_human_is_wanted_or_the_model_is_on_the_fence() -> None:
    facts = {"source_changed": True}
    v = decide(_answers(needs_human_review={"type": "noul", "noul": 0.7}), facts, "m", {})
    assert v.outcome == "escalate" and v.exit_code == EXIT_ESCALATE
    assert decide(_answers(safe_to_merge={"type": "noul", "noul": 0.55}), facts, "m", {}).outcome == "escalate"


def test_policy_comments_when_tests_are_doubtful() -> None:
    facts = {"source_changed": True}
    v = decide(_answers(tests_cover_change={"type": "noul", "noul": 0.3}), facts, "m", {})
    assert v.outcome == "comment" and any("tests" in r for r in v.reasons)
    # docs-only change: tests are not required
    assert decide(_answers(tests_cover_change={"type": "noul", "noul": 0.1}), {"source_changed": False}, "m", {}).outcome == "approve"


def test_missing_answers_fall_back_to_neutral_not_crash() -> None:
    v = decide({}, {"source_changed": True}, "m", {})
    assert v.outcome == "escalate"  # safe_to_merge defaults to 0.5: on the fence


# ---------------------------------------------------------------- the command


def test_review_end_to_end_writes_reports_and_telemetry(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.write_file("change.diff", DIFF)
    seen: list[dict[str, Any]] = []
    code, out, err = _run(
        repo,
        "--diff",
        "change.diff",
        "--task",
        "kvad errors",
        "--json",
        "out/r.json",
        "--markdown",
        "out/r.md",
        transport=_transport(_answers(), seen),
    )
    assert code == EXIT_OK and err == ""
    assert out.startswith("outcome: approve")
    assert "correctness" in out and "model: jev-1.13.0" in out
    assert len(seen) == 1  # none of the diff's files is on disk, so nothing is judged whole
    payload = seen[0]
    assert payload["model"] == "jev-latest" and set(payload["questions"]) == set(questions())
    assert payload["state"]["facts"]["slices_touched"] == ["kvad", "rune"] and payload["state"]["facts"]["craft_files"] == []
    report = json.loads(repo.read_file("out/r.json"))
    assert report["outcome"] == "approve" and report["model"] == "jev-1.13.0" and report["usage"]["input_tokens"] == 1234
    assert report["files"] == {}
    md = repo.read_file("out/r.md")
    assert md.startswith("<!-- heimdall-review -->") and "**approve**" in md and "cross-slice" in md and "fan-in 1" in md
    line = json.loads(repo.telemetry.splitlines()[-1])
    assert line["event"] == "review" and line["kind"] == "approve" and line["slice"] == "kvad,rune" and line["sensor"] == "jev"


_ROUTE_SOURCE = "from fastapi import APIRouter\nfrom pfa.api.problems import Problem\nrouter = APIRouter(prefix='/kvad')\n"


def test_each_edited_application_file_is_judged_whole_in_its_own_call(repo: TempRepo) -> None:
    """The user's call (2026-10-09): clean code is judged on each edited file whole, not on the diff, so the
    verdict can say which file the judge deems wrong and the judge sees the full picture."""
    repo.write_sample_map()
    repo.write_file("change.diff", DIFF)
    repo.write_file("src/pfa/api/routes/kvad.py", _ROUTE_SOURCE)
    repo.write_file("tests/api/test_kvad.py", "def test_it():\n    assert True\n")  # tests are never judged whole
    seen: list[dict[str, Any]] = []
    dup = {"type": "choice", "choice": "duplication", "probabilities": {"duplication": 0.7, "none": 0.3}, "confidence": 0.7}
    low = {"src/pfa/api/routes/kvad.py": _file_answers(2.31, dup)}
    code, out, _ = _run(
        repo, "--diff", "change.diff", "--json", "out/r.json", "--markdown", "out/r.md", transport=_transport(_answers(), seen, low)
    )
    assert code == EXIT_REQUEST_CHANGES
    assert [("file" in p["state"]) for p in seen] == [False, True]  # the diff first, then the one file on disk
    craft = seen[1]
    assert craft["questions"] == file_questions() and craft["state"]["file"] == {
        "path": "src/pfa/api/routes/kvad.py",
        "area": "slice:kvad",
        "content": _ROUTE_SOURCE,
    }
    assert craft["state"]["facts"]["changed_lines"] == [2, 4] and craft["state"]["facts"]["truncated"] is False
    assert seen[0]["state"]["facts"]["craft_files"] == ["src/pfa/api/routes/kvad.py"]
    # the verdict names the file, and the advice says the whole file was rated and what limits it
    assert out.startswith("outcome: request_changes  (clean code 2.31/3 in src/pfa/api/routes/kvad.py below the target 2.7)")
    assert "The judge rates the whole file src/pfa/api/routes/kvad.py, not only this change's lines, 2.31 on 0–3" in out
    assert "its most probable limit: duplication (0.70): extract the logic written twice into one function" in out
    assert (
        "file (judged whole)" in out
        and "src/pfa/api/routes/kvad.py".ljust(56) + "Exemplary · 2.31/3 · conf 0.80".ljust(32) + "duplication · 0.70 · conf 0.70" in out
    )
    report = json.loads(repo.read_file("out/r.json"))
    assert report["files"] == low and report["usage"] == {"input_tokens": 2468, "output_tokens": 30}  # both calls added up
    md = repo.read_file("out/r.md")
    assert "**Clean code, file by file** (each file judged whole, not only its hunks):" in md
    assert "| `src/pfa/api/routes/kvad.py` | Exemplary · 2.31/3 · conf 0.80 | duplication · 0.70 · conf 0.70 |" in md
    assert "- judged whole for clean code, one call each: src/pfa/api/routes/kvad.py" in md
    # a file on target passes, and the approval line says how clean the lowest file is
    code, out, _ = _run(
        repo, "--diff", "change.diff", transport=_transport(_answers(), files={"src/pfa/api/routes/kvad.py": _file_answers(2.8)})
    )
    assert code == EXIT_OK and out.startswith("outcome: approve  (p(safe)=0.90 p(human)=0.10 clean=2.80/3 (src/pfa/api/routes/kvad.py))")


def test_a_failed_file_call_is_exit_3_not_a_verdict(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.write_file("change.diff", DIFF)
    repo.write_file("src/pfa/api/routes/kvad.py", _ROUTE_SOURCE)

    def diff_only(payload: dict[str, Any]) -> dict[str, Any]:
        if "file" in payload["state"]:
            raise ReviewError("TypeSafe API unreachable: boom")
        return {"model": "jev-1", "answers": _answers(), "usage": {}}

    code, _, err = _run(repo, "--diff", "change.diff", transport=diff_only)
    assert code == EXIT_ERROR and "unreachable" in err


def test_review_exit_codes_follow_the_outcome(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.write_file("change.diff", DIFF)
    code, out, _ = _run(repo, "--diff", "change.diff", transport=_transport(_answers(has_security_concern={"type": "noul", "noul": 0.9})))
    assert code == EXIT_REQUEST_CHANGES and out.startswith("outcome: request_changes")
    code, out, _ = _run(repo, "--diff", "change.diff", transport=_transport(_answers(needs_human_review={"type": "noul", "noul": 0.8})))
    assert code == EXIT_ESCALATE and out.startswith("outcome: escalate")


def test_review_reads_the_diff_from_stdin(repo: TempRepo) -> None:
    code, out, _ = _run(repo, "--diff", "-", transport=_transport(_answers()), stdin=DIFF)
    assert code == EXIT_OK and out.startswith("outcome:")


def test_dry_run_prints_the_payload_and_makes_no_call(repo: TempRepo) -> None:
    repo.write_file("change.diff", DIFF)

    def boom(_: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("must not be called")

    repo.write_file("src/pfa/api/routes/kvad.py", _ROUTE_SOURCE)
    code, out, _ = _run(repo, "--diff", "change.diff", "--dry-run", transport=boom)
    assert code == EXIT_OK
    calls = json.loads(out)
    diff = calls["diff"]
    assert diff["model"] == "jev-latest" and "safe_to_merge" in diff["questions"] and diff["state"]["facts"]["files_changed"] == 4
    assert [c["state"]["file"]["path"] for c in calls["files"]] == ["src/pfa/api/routes/kvad.py"]
    assert set(calls["files"][0]["questions"]) == {"clean_code", "clean_code_limit"}
    assert not repo.has_file(".heimdall/telemetry.jsonl")


def test_empty_diff_is_fine(repo: TempRepo) -> None:
    repo.write_file("empty.diff", "")
    code, out, _ = _run(repo, "--diff", "empty.diff", transport=_transport(_answers()))
    assert code == EXIT_OK and "nothing to review" in out


def test_missing_key_is_an_error_unless_allowed(repo: TempRepo) -> None:
    repo.write_file("change.diff", DIFF)
    code, _, err = _run(repo, "--diff", "change.diff", env={})
    assert code == EXIT_ERROR and "TYPESAFE_API_KEY" in err
    code, out, _ = _run(repo, "--diff", "change.diff", "--allow-missing-key", "--markdown", "r.md", env={})
    assert code == EXIT_OK and "skipping" in out and "skipped" in repo.read_file("r.md")


def test_api_failure_is_exit_3_not_a_verdict(repo: TempRepo) -> None:
    repo.write_file("change.diff", DIFF)

    def down(_: dict[str, Any]) -> dict[str, Any]:
        raise ReviewError("TypeSafe API unreachable: boom")

    code, _, err = _run(repo, "--diff", "change.diff", transport=down)
    assert code == EXIT_ERROR and "unreachable" in err
    code, _, err = _run(repo, "--diff", "change.diff", transport=lambda _p: {"model": "x"})
    assert code == EXIT_ERROR and "malformed" in err


def test_usage_errors_exit_2(repo: TempRepo) -> None:
    assert _run(repo, "--nope")[0] == 2
    assert _run(repo, "--base", "main", "--diff", "x")[0] == 2


def test_http_transport_retries_on_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request

    calls: list[Any] = []
    sleeps: list[float] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return json.dumps({"model": "jev-1", "answers": {}, "usage": {}}).encode()

    def fake_urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("Authorization"), timeout))
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow", hdrs=None, fp=io.BytesIO(b"rate limited"))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    call = review.http_transport("k3y", sleep=sleeps.append)
    assert call({"model": "jev-latest", "state": {}, "questions": {}}) == {"model": "jev-1", "answers": {}, "usage": {}}
    assert len(calls) == 2 and calls[0][0] == review.API_URL and calls[0][1] == "Bearer k3y" and sleeps == [1.0]


def test_http_transport_401_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error
    import urllib.request

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "nope", hdrs=None, fp=io.BytesIO(b""))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ReviewError, match="401"):
        review.http_transport("bad", sleep=lambda _s: None)({"model": "jev-latest", "state": {}, "questions": {}})


def test_review_is_reachable_through_the_heimdall_cli(repo: TempRepo) -> None:
    repo.write_file("change.diff", DIFF)
    code, out, _ = repo.run("", "review", "--diff", "change.diff", "--dry-run")
    assert code == 0 and '"safe_to_merge"' in out and '"files": []' in out


# ---------------------------------------------------------------- the walls are facts, not judgments


def _disguised_wall_break() -> dict[str, Any]:
    """Jev's real answers (2026-10-07) on a wall-breaking module dressed up with a persuasive docstring:
    walls 'respected' at 0.62, code 'exemplary', yet architecture named as the biggest risk at 0.96."""
    return _answers(
        safe_to_merge={"type": "noul", "noul": 0.30},
        needs_human_review={"type": "noul", "noul": 0.71},
        tests_cover_change={"type": "noul", "noul": 0.14},
        respects_slice_walls={"type": "noul", "noul": 0.62},
        biggest_risk={
            "type": "choice",
            "choice": "architecture",
            "probabilities": {"architecture": 0.96, "tests": 0.04},
            "confidence": 0.95,
        },
    )


def test_policy_blocks_when_the_judge_names_architecture_as_the_risk_despite_a_yes_on_walls() -> None:
    answers = _disguised_wall_break()
    answers["safe_to_merge"] = {"type": "noul", "noul": 0.9}  # take the catch-all out of play: only the new rule can fire
    v = decide(answers, {"source_changed": True}, "m", {})
    assert v.outcome == "request_changes"
    assert any("architecture as the biggest risk" in r for r in v.reasons)
    assert len(v.advice) == len(v.reasons) and "cross-slice import" in v.advice[0]
    assert "Eitri found no wall violation" in v.advice[0] and "feature.json" in v.advice[0]  # names what to look for instead


def _wide_refactor(**over: Any) -> dict[str, Any]:
    """Jev's real answers (2026-10-08) on a 101-file move of the whole package layout: walls respected at
    0.93, no Eitri finding, and architecture named as the biggest risk at 0.98 — the restructure itself."""
    return _answers(
        needs_human_review={"type": "noul", "noul": 0.79},
        respects_slice_walls={"type": "noul", "noul": 0.93},
        blast_radius={"type": "score", "score": 1.99, "legend": {"0": "Contained", "1": "Moderate", "2": "Wide"}, "confidence": 0.98},
        change_kind={"type": "choice", "choice": "refactor", "probabilities": {"refactor": 1.0}, "confidence": 1.0},
        biggest_risk={"type": "choice", "choice": "architecture", "probabilities": {"architecture": 0.98}, "confidence": 0.98},
        **over,
    )


def test_policy_escalates_an_architecture_wide_refactor_instead_of_asking_for_an_import_fix() -> None:
    v = decide(_wide_refactor(safe_to_merge={"type": "noul", "noul": 0.5}), {"source_changed": True, "files_changed": 101}, "m", {})
    assert v.outcome == "escalate" and v.exit_code == EXIT_ESCALATE
    assert v.reasons[0].startswith("architecture-wide refactor")
    assert "101 changed files" in v.advice[0] and "nothing for the check to fix" in v.advice[0]
    # the same label on a fix with a moderate blast radius is still the disguised-import case: block
    bug = _wide_refactor(safe_to_merge={"type": "noul", "noul": 0.5})
    bug["change_kind"] = _answers()["change_kind"]  # a fix, not a refactor
    assert decide(bug, {"source_changed": True}, "m", {}).outcome == "request_changes"


def test_not_safe_blocks_only_when_a_doubt_has_a_code_fix_and_says_what_it_is() -> None:
    facts = {"source_changed": True, "routes_changed": True, "files_changed": 101}
    v = decide(
        _wide_refactor(safe_to_merge={"type": "noul", "noul": 0.33}, errors_use_problem_details={"type": "noul", "noul": 0.30}),
        facts,
        "m",
        {},
    )
    assert v.outcome == "request_changes" and v.reasons[0] == "not safe to merge p(safe)=0.33"
    assert v.reasons[1] == "errors bypass Problem Details p(ok)=0.30"  # the standalone rule names it too
    why = v.advice[0]
    assert "To fix in code: it doubts every error leaves as a problem document (0.30): in each changed route raise `Problem`" in why
    assert (
        "For a reviewer to weigh: it names architecture as the one thing to check (0.98): Eitri found no wall violation, so the restructure itself"
        in why
    )
    assert "wants a person to look (needs_human_review 0.79)" in why and "blast radius is wide" in why
    assert "other rows" not in why


def test_not_safe_with_nothing_to_change_in_code_requires_a_human() -> None:
    facts = {"source_changed": True, "routes_changed": True, "files_changed": 101}
    # Jev's real answers on this PR (2026-10-08): an undecided 0.52 on the error path is not a code change
    v = decide(
        _wide_refactor(safe_to_merge={"type": "noul", "noul": 0.31}, errors_use_problem_details={"type": "noul", "noul": 0.52}),
        facts,
        "m",
        {},
    )
    assert v.outcome == "escalate" and v.exit_code == EXIT_ESCALATE
    assert "undecided whether every error leaves as a problem document (0.52)" in v.advice[0]
    assert v.reasons[0] == "not safe to merge p(safe)=0.31; nothing left to change in code"
    assert "nothing it doubts is a code change" in v.advice[0] and "the restructure itself" in v.advice[0]
    lonely = decide(_answers(safe_to_merge={"type": "noul", "noul": 0.2}), {"source_changed": True}, "m", {})
    assert lonely.outcome == "escalate" and "no other answer explains the doubt" in lonely.advice[0]


def test_a_named_risk_other_than_architecture_is_always_a_code_fix() -> None:
    """Jev's real answers on the logging PR (2026-10-08): p(safe)=0.12, correctness named at 0.82 while the
    correctness score said "probably correct". A label is about the diff, so it blocks with a code path."""
    answers = _answers(
        safe_to_merge={"type": "noul", "noul": 0.12},
        needs_human_review={"type": "noul", "noul": 0.88},
        biggest_risk={"type": "choice", "choice": "correctness", "probabilities": {"correctness": 0.82}, "confidence": 0.78},
    )
    v = decide(answers, {"source_changed": True, "files_changed": 12}, "m", {})
    assert v.outcome == "request_changes"
    assert "To fix in code: it names correctness as the one thing to check (0.82): walk the new branches" in v.advice[0]
    assert "For a reviewer to weigh: it wants a person to look (needs_human_review 0.88)" in v.advice[0]


def test_policy_blocks_on_eitri_findings_whatever_the_judge_says() -> None:
    facts = {
        "source_changed": True,
        "wall_violations": [
            {
                "path": "src/pfa/features/kvad/internal/leak.py",
                "line": 3,
                "rule": "EIT001",
                "message": "imports rune internals",
                "fix": "use the contract",
            }
        ],
    }
    v = decide(_answers(), facts, "m", {})  # a glowing review
    assert v.outcome == "request_changes" and v.reasons[0].startswith("Eitri found 1 wall violation")
    assert "eitri check" in v.advice[0]


def test_wall_findings_come_from_eitri_and_only_for_changed_files(project: TempProject) -> None:
    project.slice("rune", **{"internal/__init__.py": "", "internal/rune_engine.py": "class RuneEngine: ..."})
    project.slice(
        "kvad",
        ["rune"],
        **{
            "internal/__init__.py": "",
            "internal/leak.py": "from pfa.features.rune.internal.rune_engine import RuneEngine\n",
            "internal/old_leak.py": "from pfa.features.rune.internal.rune_engine import RuneEngine\n",
        },
    )
    project.write("harness/rules/EIT001.md", "# EIT001\nWhy it matters.\n**Fix:** import the dependency's `contract` package instead.\n")
    changed = parse_unified_diff(
        "diff --git a/pfa/features/kvad/internal/leak.py b/pfa/features/kvad/internal/leak.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/pfa/features/kvad/internal/leak.py\n@@ -0,0 +1 @@\n"
        "+from pfa.features.rune.internal.rune_engine import RuneEngine\n"
    )
    found = wall_findings(str(project.root), changed)
    assert [(f["path"], f["line"], f["rule"]) for f in found] == [("pfa/features/kvad/internal/leak.py", 1, "EIT001")]
    assert found[0]["fix"] == "import the dependency's `contract` package instead."  # read from the rule doc, never hardcoded
    assert found[0]["doc"] == "harness/rules/EIT001.md"
    state = build_state(changed, None, None, found)
    assert state["facts"]["wall_violations"] == found


def test_wall_findings_are_empty_without_a_slice_tree(repo: TempRepo) -> None:
    assert wall_findings(str(repo.root), parse_unified_diff(DIFF)) == []


def _facts(**over: Any) -> dict[str, Any]:
    """The facts of a one-file change inside kvad, as ``build_state`` would state them."""
    return {
        "files_changed": 1,
        "lines_added": 10,
        "lines_removed": 0,
        "slices_touched": ["kvad"],
        "cross_slice_change": False,
        "contracts_touched": {},
        "routes_changed": False,
        "source_changed": True,
        "tests_changed": [],
        "docs_changed": False,
        "files_truncated": [],
        "wall_violations": [],
        **over,
    }


def test_the_comment_says_what_went_wrong_and_what_to_do(repo: TempRepo) -> None:
    leak = {
        "path": "src/pfa/features/kvad/internal/leak.py",
        "line": 3,
        "rule": "EIT001",
        "message": "imports rune internals",
        "fix": "use the contract",
    }
    facts = _facts(wall_violations=[leak])
    v = decide(_disguised_wall_break(), facts, "jev-1", {})
    md = render_markdown(v, {"facts": facts}, questions())
    assert "### What went wrong" in md
    assert "**Eitri found 1 wall violation(s) in the changed files.** The change crosses a slice wall." in md
    assert "- `src/pfa/features/kvad/internal/leak.py:3 — EIT001: imports rune internals` **Fix:** use the contract" in md
    assert "**not safe to merge p(safe)=0.30.**" in md
    assert "**Next:** fix what is named above and push" in md
    assert md.index("What went wrong") < md.index("| question | answer |")  # the explanation comes before the numbers
    table = render_table(v, questions(), facts)
    assert "leak.py:3 — EIT001" in table
    # the Stop hook relays this table, so every reason must carry the advice that names the fix
    assert "  not safe to merge p(safe)=0.30: The judge does not consider the change mergeable as-is." in table
    assert table.index("mergeable as-is") < table.index("question".ljust(28))  # advice first, numbers after

    v = decide(_answers(needs_human_review={"type": "noul", "noul": 0.7}), facts | {"wall_violations": []}, "jev-1", {})
    md = render_markdown(v, {"facts": facts | {"wall_violations": []}}, questions())
    assert "### What the judge wants" in md and "needs-human-review" in md

    md = render_markdown(
        decide(_answers(), facts | {"wall_violations": []}, "jev-1", {}), {"facts": facts | {"wall_violations": []}}, questions()
    )
    assert "What went wrong" not in md and "What the judge wants" not in md


# ---------------------------------------------------------------- function shape: facts for the judge, named in the advice

_KVAD_ENGINE = "src/pfa/features/kvad/internal/kvad_engine.py"
_LONG_ONE = {"path": _KVAD_ENGINE, "line": 1, "name": "long_one", "lines": 46, "params": 0, "over": ["lines"]}
_WIDE = {"path": _KVAD_ENGINE, "line": 60, "name": "wide", "lines": 2, "params": 7, "over": ["params"]}


def _shape_diff(path: str, new_line: int, text: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -{new_line},1 +{new_line},2 @@\n {text}\n+    pass\n"


def test_function_shape_names_only_the_changed_functions_over_the_limits(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.write_file(_KVAD_ENGINE, SHAPED_SOURCE)
    m = MapModel.load(str(repo.root / ".heimdall" / "map.json"))
    touched_long = function_shape(str(repo.root), parse_unified_diff(_shape_diff(_KVAD_ENGINE, 10, "x8 = 8")), m)
    assert touched_long == [_LONG_ONE]
    assert function_shape(str(repo.root), parse_unified_diff(_shape_diff(_KVAD_ENGINE, 50, "return a + b")), m) == []
    # tests, deleted files and files that are not on disk contribute nothing
    repo.write_file("tests/api/test_long.py", SHAPED_SOURCE)
    assert function_shape(str(repo.root), parse_unified_diff(_shape_diff("tests/api/test_long.py", 10, "x8 = 8")), m) == []
    gone = parse_unified_diff(_shape_diff("src/pfa/kernel/gone.py", 10, "x8 = 8"))
    assert function_shape(str(repo.root), gone, m) == []
    state = build_state(gone, m, None, None, touched_long)
    assert state["facts"]["function_shape"] == touched_long and state["facts"]["function_limits"] == {"lines": 40, "params": 5}


def _shape_facts(**over: Any) -> dict[str, Any]:
    """The AST's facts about a change that touched one long and one wide function."""
    return {
        "source_changed": True,
        "code_changed": True,
        "function_shape": [_LONG_ONE, _WIDE],
        "function_limits": {"lines": 40, "params": 5},
        **over,
    }


def test_shape_facts_block_only_when_the_judge_agrees_and_then_name_the_function() -> None:
    no = {"type": "noul", "noul": 0.2}
    v = decide(_answers(single_purpose=no), _shape_facts(), "m", {})
    assert v.outcome == "request_changes" and v.reasons == ["functions do more than one thing p(single)=0.20"]
    assert "kvad_engine.py:1 long_one (46 lines, 0 params)" in v.advice[0] and "within 40 lines" in v.advice[0]
    assert "wide (2 lines, 7 params)" not in v.advice[0]  # the line limit names long functions, not wide ones
    v = decide(_answers(lean_signatures=no), _shape_facts(), "m", {})
    assert v.outcome == "request_changes" and v.reasons == ["signatures too wide p(lean)=0.20"]
    assert "kvad_engine.py:60 wide (2 lines, 7 params)" in v.advice[0] and "at most 5 parameters" in v.advice[0]
    # the judge's No without a function named by the AST is a note, not a block: nothing to point at
    v = decide(_answers(single_purpose=no), {"source_changed": True}, "m", {})
    assert v.outcome == "comment" and "functions may not be single-purpose p=0.20" in v.reasons
    # the AST's list without the judge's No is a fact in the comment, not a verdict: a flat table may stay whole
    assert decide(_answers(), _shape_facts(), "m", {}).outcome == "approve"


def test_clean_code_below_target_sends_the_agent_back_to_the_file_naming_the_limit() -> None:
    """The user's floor (2026-10-08): clean code 2.7 on 0–3, 90%, not to be lowered. Judged per file, whole
    (2026-10-09): below it the check fails naming the file, so the agent is retriggered and sent to a path, and
    the advice says what the judge found limiting and which function in that file to start with."""
    dup = {"type": "choice", "choice": "duplication", "probabilities": {"duplication": 0.7}, "confidence": 0.7}
    other = "src/pfa/features/kvad/internal/other.py"
    files = {other: _file_answers(2.9), _KVAD_ENGINE: _file_answers(2.28, dup)}
    v = decide(_answers(), _shape_facts(), "m", {}, files)
    assert v.outcome == "request_changes" and v.reasons == [f"clean code 2.28/3 in {_KVAD_ENGINE} below the target 2.7"]
    assert f"The judge rates the whole file {_KVAD_ENGINE}, not only this change's lines, 2.28 on 0–3 (3 is exemplary)" in v.advice[0]
    assert "the target is 2.7. In its answer, its most probable limit: duplication (0.70): extract the logic written twice" in v.advice[0]
    assert "— start with src/pfa/features/kvad/internal/kvad_engine.py:1 long_one (46 lines, 0 params)." in v.advice[0]
    assert v.files == files
    # a second file below the target is its own reason, lowest first, and the AST's functions are named per file
    files[other] = _file_answers(2.1, dup)
    v = decide(_answers(), _shape_facts(), "m", {}, files)
    assert v.reasons == [f"clean code 2.10/3 in {other} below the target 2.7", f"clean code 2.28/3 in {_KVAD_ENGINE} below the target 2.7"]
    assert "start with" not in v.advice[0] and "start with" in v.advice[1]
    # `none` wins the choice under a short score (PR #9: none at 0.40): the runner-up in the judge's probabilities is the fix
    none_first = {"type": "choice", "choice": "none", "probabilities": {"none": 0.40, "comments": 0.25, "style": 0.2}, "confidence": 0.3}
    v = decide(_answers(), _shape_facts(), "m", {}, {_KVAD_ENGINE: _file_answers(2.28, none_first)})
    assert "its most probable limit: comments (0.25): replace what-comments with why-comments" in v.advice[0]
    # no actionable label at all: every fix is listed, so a code change is still named
    v = decide(_answers(), _shape_facts(), "m", {}, {_KVAD_ENGINE: _file_answers(2.28, {"type": "choice", "choice": "none"})})
    assert "the judge named no single limit, so: extract the logic written twice" in v.advice[0] and "never swallow one" in v.advice[0]
    # a file the judge saw cut is said so
    v = decide(_answers(), _shape_facts(craft_truncated=[_KVAD_ENGINE]), "m", {}, {_KVAD_ENGINE: _file_answers(2.28, dup)})
    assert "The judge saw the file cut at the token budget, so the score rates what it saw" in v.advice[0]
    # exactly on target passes, and the approval line names the lowest of the files judged
    v = decide(_answers(), _shape_facts(), "m", {}, {_KVAD_ENGINE: _file_answers(2.7), other: _file_answers(2.9)})
    assert v.outcome == "approve" and v.reasons == [f"p(safe)=0.90 p(human)=0.10 clean>=2.70/3 ({_KVAD_ENGINE} lowest of 2 files)"]


def test_readability_is_not_scored() -> None:
    """Dropped from the verdict (2026-10-08): on application code the judge withheld its top level for the subject,
    not the craft, naming no limit across eight runs, so the score named nothing an edit could answer. A
    readability answer in the response changes nothing; the top-risk label `readability` still carries its fix."""
    hard = {"type": "score", "score": 1.0, "confidence": 0.8}
    assert "readability" not in questions() and "readability_limit" not in questions()
    assert decide(_answers(readability=hard), _shape_facts(), "m", {}).outcome == "approve"


def test_craft_doubt_behind_not_safe_names_the_file_the_limit_and_where_to_start() -> None:
    dup = {"type": "choice", "choice": "duplication", "probabilities": {"duplication": 0.8}, "confidence": 0.8}
    v = decide(_answers(safe_to_merge={"type": "noul", "noul": 0.3}), _shape_facts(), "m", {}, {_KVAD_ENGINE: _file_answers(1.0, dup)})
    assert v.outcome == "request_changes"
    assert f"it rates {_KVAD_ENGINE} 1.00/3 against the target 2.7: its most probable limit: duplication (0.80): extract the" in v.advice[0]
    assert "start with src/pfa/features/kvad/internal/kvad_engine.py:1 long_one (46 lines, 0 params)" in v.advice[0]
    label = {"type": "choice", "choice": "readability", "probabilities": {"readability": 0.9}, "confidence": 0.9}
    v = decide(_answers(safe_to_merge={"type": "noul", "noul": 0.3}, biggest_risk=label), _shape_facts(), "m", {})
    assert "it names readability as the one thing to check (0.90): rename what the judge could not follow" in v.advice[0]
    assert "— start with src/pfa/features/kvad/internal/kvad_engine.py:1 long_one" in v.advice[0]


def test_the_comment_and_the_table_list_the_functions_over_the_limits() -> None:
    facts = _facts(**_shape_facts())
    v = decide(_answers(), facts, "jev-1", {}, {_KVAD_ENGINE: _file_answers(2.9)})
    md = render_markdown(v, {"facts": facts}, questions())
    assert "- changed functions over 40 lines or 5 parameters:" in md
    assert "  - `src/pfa/features/kvad/internal/kvad_engine.py:1 long_one (46 lines, 0 params)`" in md
    assert "| `clean_code` |" not in md and "| `single_purpose` |" in md and "| `lean_signatures` |" in md
    assert (
        f"| `{_KVAD_ENGINE}` | Exemplary · 2.90/3 · conf 0.80 | none · 0.90 · conf 0.80 |" in md
    )  # clean code is a row per file, not a question
    assert "over the limits: src/pfa/features/kvad/internal/kvad_engine.py:60 wide (2 lines, 7 params)" in render_table(
        v, questions(), facts
    )


# ---------------------------------------------------------------- the craft pass: each edited application file, whole


def test_craft_files_are_the_applications_python_read_whole_with_the_lines_the_change_touched(repo: TempRepo) -> None:
    repo.write_file("src/pfa/api/routes/kvad.py", _ROUTE_SOURCE)
    repo.write_file("src/pfa/features/rune/contract/rune_service.py", "class RuneService:\n    def new_member(self): ...\n    pass\n")
    repo.write_file("tests/api/test_kvad.py", "def test_it():\n    assert True\n")
    repo.write_file("harness/guides/x.md", "new\n")
    found = craft_files(str(repo.root), parse_unified_diff(DIFF), _map())
    assert [(f.path, f.area, f.changed_lines) for f in found] == [
        ("src/pfa/api/routes/kvad.py", "slice:kvad", [2, 4]),
        ("src/pfa/features/rune/contract/rune_service.py", "contract:rune", [2]),
    ]
    assert found[0].content == _ROUTE_SOURCE
    # a deleted file and one not on disk contribute nothing
    gone = "diff --git a/src/pfa/kernel/gone.py b/src/pfa/kernel/gone.py\ndeleted file mode 100644\n--- a/src/pfa/kernel/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    repo.write_file("src/pfa/kernel/gone.py", "x = 1\n")
    assert craft_files(str(repo.root), parse_unified_diff(gone), _map()) == []
    assert craft_files(str(repo.root), parse_unified_diff(_shape_diff("src/pfa/kernel/missing.py", 1, "x = 1")), _map()) == []


def test_craft_state_is_the_file_whole_with_its_own_shape_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    from heimdall.commands.review import craft

    f = craft.CraftFile(_KVAD_ENGINE, "slice:kvad", SHAPED_SOURCE, [10])
    state = craft_state(f, "a task", [_LONG_ONE, {**_WIDE, "path": "src/pfa/features/kvad/internal/other.py"}])
    assert state["task"] == "a task" and state["file"] == {"path": _KVAD_ENGINE, "area": "slice:kvad", "content": SHAPED_SOURCE}
    facts = state["facts"]
    assert facts["changed_lines"] == [10] and facts["function_shape"] == [_LONG_ONE]  # only this file's functions
    assert facts["function_limits"] == {"lines": 40, "params": 5} and facts["truncated"] is False
    assert facts["file_tokens"] == facts["state_tokens"] == estimate(SHAPED_SOURCE)
    monkeypatch.setattr(craft, "MAX_CRAFT_FILE_TOKENS", 10)
    cut = craft_state(f, None, [])
    assert cut["facts"]["truncated"] is True and cut["facts"]["state_tokens"] < cut["facts"]["file_tokens"]
    assert cut["file"]["content"].endswith("more lines not shown]") and cut["task"] == "(no task statement given)"
