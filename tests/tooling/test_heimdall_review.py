"""``heimdall review``: diff -> bounded state -> one Jev call -> policy -> outcome.
The network is replaced by a fake transport; the policy and the state are what is tested."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from conftest import TempProject, TempRepo

from heimdall.commands import review
from heimdall.commands.review import (
    EXIT_ERROR,
    EXIT_ESCALATE,
    EXIT_OK,
    EXIT_REQUEST_CHANGES,
    ReviewError,
    build_state,
    decide,
    parse_unified_diff,
    questions,
    render_markdown,
    render_table,
    wall_findings,
)
from heimdall.model import MapModel

DIFF = """\
diff --git a/src/slices/kvad/internal/routes.py b/src/slices/kvad/internal/routes.py
index 1111111..2222222 100644
--- a/src/slices/kvad/internal/routes.py
+++ b/src/slices/kvad/internal/routes.py
@@ -1,4 +1,5 @@
 from fastapi import APIRouter
+from http_common import Problem
 router = APIRouter(prefix="/kvad")
-    raise HTTPException(422)
+    raise Problem.domain_rejection(result.error)
diff --git a/src/slices/rune/contract/rune_service.py b/src/slices/rune/contract/rune_service.py
index 1111111..2222222 100644
--- a/src/slices/rune/contract/rune_service.py
+++ b/src/slices/rune/contract/rune_service.py
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
diff --git a/docs/guides/x.md b/docs/guides/x.md
index 1111111..2222222 100644
--- a/docs/guides/x.md
+++ b/docs/guides/x.md
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
            "kernel": "shared_kernel",
            "slices_dir": "src/slices",
            "shared": ["http_common"],
            "slices": {
                "kvad": {"path": "src/slices/kvad", "depends_on": ["rune"], "budget": 15000, "fan_in": 0},
                "rune": {"path": "src/slices/rune", "depends_on": [], "budget": 15000, "fan_in": 12},
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
        "clean_code": {
            "type": "score",
            "score": 2.1,
            "legend": {"0": "Hard to follow", "1": "Acceptable", "2": "Clean", "3": "Exemplary"},
            "probabilities": {"0": 0.0, "1": 0.1, "2": 0.7, "3": 0.2},
            "confidence": 0.7,
        },
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


def _transport(answers: dict[str, Any], seen: list[dict[str, Any]] | None = None):
    def call(payload: dict[str, Any]) -> dict[str, Any]:
        if seen is not None:
            seen.append(payload)
        return {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 1234, "output_tokens": 15}}

    return call


def _run(repo: TempRepo, *args: str, transport=None, env: dict[str, str] | None = None, stdin: str = "") -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = review.run(list(args), io.StringIO(stdin), out, err, str(repo.root), transport=transport, env=env if env is not None else {})
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- the diff and the state


def test_diff_is_parsed_per_file_and_generated_files_are_ignored() -> None:
    files = parse_unified_diff(DIFF)
    assert [f.path for f in files] == [
        "src/slices/kvad/internal/routes.py",
        "src/slices/rune/contract/rune_service.py",
        "tests/api/test_kvad.py",
        "docs/guides/x.md",
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
    assert facts["tests_changed"] == ["tests/api/test_kvad.py"]
    areas = {e["path"]: e["area"] for e in state["files"]}
    assert areas["src/slices/kvad/internal/routes.py"] == "slice:kvad"
    assert areas["src/slices/rune/contract/rune_service.py"] == "contract:rune"
    assert areas["tests/api/test_kvad.py"] == "tests" and areas["docs/guides/x.md"] == "docs"
    assert state["architecture"]["slices"]["kvad"]["depends_on"] == ["rune"]
    assert any("RFC 9457" in r for r in state["architecture"]["rules"])
    assert state["task"].startswith("Switch kvad")


def test_oversized_files_are_truncated_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review.state, "MAX_FILE_CHARS", 120)
    state = build_state(parse_unified_diff(DIFF), _map(), None)
    assert "src/slices/kvad/internal/routes.py" in state["facts"]["files_truncated"]
    big = next(e for e in state["files"] if e["path"].endswith("routes.py"))
    assert "more diff lines not shown" in big["diff"]


def test_questions_are_valid_system_one_requests() -> None:
    qs = questions()
    for key, q in qs.items():
        assert q["type"] in ("noul", "choice", "score"), key
        assert q["instructions"], key
        if q["type"] == "noul":
            assert set(q["criteria"]) == {"true", "false"}, key
        elif q["type"] == "score":
            assert isinstance(q["criteria"], list) and 2 <= len(q["criteria"]) <= 10, key
        else:
            assert isinstance(q["criteria"], dict) and 1 < len(q["criteria"]) <= 255, key
    assert {"safe_to_merge", "needs_human_review", "has_security_concern", "clean_code", "correctness"} <= set(qs)


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


def test_policy_comments_when_tests_are_doubtful_or_code_is_merely_acceptable() -> None:
    facts = {"source_changed": True}
    v = decide(_answers(tests_cover_change={"type": "noul", "noul": 0.3}), facts, "m", {})
    assert v.outcome == "comment" and any("tests" in r for r in v.reasons)
    assert decide(_answers(clean_code={"type": "score", "score": 0.9, "confidence": 0.7}), facts, "m", {}).outcome == "comment"
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
    assert "clean_code" in out and "model: jev-1.13.0" in out
    payload = seen[0]
    assert payload["model"] == "jev-latest" and set(payload["questions"]) == set(questions())
    assert payload["state"]["facts"]["slices_touched"] == ["kvad", "rune"]
    report = json.loads(repo.read_file("out/r.json"))
    assert report["outcome"] == "approve" and report["model"] == "jev-1.13.0" and report["usage"]["input_tokens"] == 1234
    md = repo.read_file("out/r.md")
    assert md.startswith("<!-- heimdall-review -->") and "**approve**" in md and "cross-slice" in md and "fan-in 1" in md
    line = json.loads(repo.telemetry.splitlines()[-1])
    assert line["event"] == "review" and line["kind"] == "approve" and line["slice"] == "kvad,rune" and line["sensor"] == "jev"


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

    code, out, _ = _run(repo, "--diff", "change.diff", "--dry-run", transport=boom)
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["model"] == "jev-latest" and "safe_to_merge" in payload["questions"] and payload["state"]["facts"]["files_changed"] == 4
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
    assert code == 0 and '"safe_to_merge"' in out


# ---------------------------------------------------------------- the walls are facts, not judgments


def _disguised_wall_break() -> dict[str, Any]:
    """Jev's real answers (2026-10-07) on a wall-breaking module dressed up with a persuasive docstring:
    walls 'respected' at 0.62, code 'exemplary', yet architecture named as the biggest risk at 0.96."""
    return _answers(
        safe_to_merge={"type": "noul", "noul": 0.30},
        needs_human_review={"type": "noul", "noul": 0.71},
        tests_cover_change={"type": "noul", "noul": 0.14},
        respects_slice_walls={"type": "noul", "noul": 0.62},
        clean_code={"type": "score", "score": 2.5, "confidence": 0.5},
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


def test_policy_blocks_on_eitri_findings_whatever_the_judge_says() -> None:
    facts = {
        "source_changed": True,
        "wall_violations": [
            {
                "path": "src/slices/kvad/internal/leak.py",
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
            "internal/leak.py": "from slices.rune.internal.rune_engine import RuneEngine\n",
            "internal/old_leak.py": "from slices.rune.internal.rune_engine import RuneEngine\n",
        },
    )
    project.write("docs/rules/EIT001.md", "# EIT001\nWhy it matters.\n**Fix:** import the dependency's `contract` package instead.\n")
    changed = parse_unified_diff(
        "diff --git a/slices/kvad/internal/leak.py b/slices/kvad/internal/leak.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/slices/kvad/internal/leak.py\n@@ -0,0 +1 @@\n"
        "+from slices.rune.internal.rune_engine import RuneEngine\n"
    )
    found = wall_findings(str(project.root), changed)
    assert [(f["path"], f["line"], f["rule"]) for f in found] == [("slices/kvad/internal/leak.py", 1, "EIT001")]
    assert found[0]["fix"] == "import the dependency's `contract` package instead."  # read from the rule doc, never hardcoded
    assert found[0]["doc"] == "docs/rules/EIT001.md"
    state = build_state(changed, None, None, found)
    assert state["facts"]["wall_violations"] == found


def test_wall_findings_are_empty_without_a_slice_tree(repo: TempRepo) -> None:
    assert wall_findings(str(repo.root), parse_unified_diff(DIFF)) == []


def test_the_comment_says_what_went_wrong_and_what_to_do(repo: TempRepo) -> None:
    facts = {
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
        "wall_violations": [
            {
                "path": "src/slices/kvad/internal/leak.py",
                "line": 3,
                "rule": "EIT001",
                "message": "imports rune internals",
                "fix": "use the contract",
            }
        ],
    }
    v = decide(_disguised_wall_break(), facts, "jev-1", {})
    md = render_markdown(v, {"facts": facts}, questions())
    assert "### What went wrong" in md
    assert "**Eitri found 1 wall violation(s) in the changed files.** The change crosses a slice wall." in md
    assert "- `src/slices/kvad/internal/leak.py:3 — EIT001: imports rune internals` **Fix:** use the contract" in md
    assert "**not safe to merge p(safe)=0.30.**" in md
    assert "**Next:** fix what is named above and push" in md
    assert md.index("What went wrong") < md.index("| question | answer |")  # the explanation comes before the numbers
    table = render_table(v, questions(), facts)
    assert "leak.py:3 — EIT001" in table

    v = decide(_answers(needs_human_review={"type": "noul", "noul": 0.7}), facts | {"wall_violations": []}, "jev-1", {})
    md = render_markdown(v, {"facts": facts | {"wall_violations": []}}, questions())
    assert "### What the judge wants" in md and "needs-human-review" in md

    md = render_markdown(
        decide(_answers(), facts | {"wall_violations": []}, "jev-1", {}), {"facts": facts | {"wall_violations": []}}, questions()
    )
    assert "What went wrong" not in md and "What the judge wants" not in md
