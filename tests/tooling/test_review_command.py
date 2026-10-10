"""``heimdall review`` end to end, with Jev and the verifier replaced. The agent pipeline (no ``--claude``)
blocks on Jev's failing checks; the PR pipeline (``--claude``) blocks only on what Claude confirms. Each
block names the function, and a clean review is a comment: a person always decides the merge."""

from __future__ import annotations

import json
from typing import Any

from conftest import TempRepo
from review_support import DIFF, ROUTE_SOURCE, file_answers, jev, noul, run

from heimdall.commands import review
from heimdall.commands.review import EXIT_ERROR, EXIT_ESCALATE, EXIT_OK, EXIT_REQUEST_CHANGES, ReviewError, file_questions
from heimdall.commands.review.verify import CONFIRMED, REFUTED, Finding, Ruling

HANDLER = ROUTE_SOURCE + "def add(name):\n    raise Problem.domain_rejection(name)\n"
WHERE = "src/pfa/api/routes/kvad.py:4 add"


def _project(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.write_file("change.diff", DIFF)
    repo.write_file("src/pfa/api/routes/kvad.py", HANDLER)


def _rules(verdict: str, evidence: str = "raise Problem.domain_rejection(name)"):
    """A verifier that rules ``verdict`` on every doubt, quoting ``evidence`` from the route module."""
    finding = Finding("src/pfa/api/routes/kvad.py", "add", 0, evidence, "the error leaves raw", "raise Problem")
    seen: list[Any] = []

    def check(inquiry: Any) -> list[Ruling]:
        seen.append(inquiry)
        return [Ruling(verdict, "read the code", (finding,) if verdict == CONFIRMED else ())] * len(inquiry.doubts)

    return check, seen


def test_a_clean_review_is_a_comment_with_every_check_asked_per_function(repo: TempRepo) -> None:
    _project(repo)
    seen: list[dict[str, Any]] = []

    code, out, err = run(repo, "--diff", "change.diff", "--json", "out/r.json", "--markdown", "out/r.md", transport=jev({}, seen))

    assert code == EXIT_OK and err == "" and out.startswith("outcome: comment  (no check failed (")
    checks = seen[0]
    assert {"f1_mixes_concerns", "f1_error_not_problem", "out_of_scope", "docs_stale"} <= set(checks["questions"])
    assert checks["state"]["functions"]["f1"]["name"] == "add" and checks["state"]["functions"]["f1"]["route"] is True
    report = json.loads(repo.read_file("out/r.json"))
    assert report["outcome"] == "comment" and any(c["where"] == WHERE for c in report["checks"])
    assert "**comment**" in repo.read_file("out/r.md")
    assert json.loads(repo.telemetry.splitlines()[-1])["kind"] == "comment"


def test_the_agent_pipeline_blocks_on_a_failing_check_naming_the_function(repo: TempRepo) -> None:
    _project(repo)

    code, out, _ = run(repo, "--diff", "change.diff", transport=jev({"f1_error_not_problem": noul(0.9)}))

    assert code == EXIT_REQUEST_CHANGES
    assert out.startswith(f"outcome: request_changes  ({WHERE} answers an error without Problem p=0.90)")
    assert "Raise `Problem` on every error path" in out


def test_the_pr_pipeline_blocks_only_on_what_claude_confirms(repo: TempRepo) -> None:
    _project(repo)
    failing = jev({"f1_error_not_problem": noul(0.9)})

    code, out, _ = run(repo, "--diff", "change.diff", "--claude", transport=failing, check=_rules(CONFIRMED)[0])
    assert code == EXIT_REQUEST_CHANGES and "src/pfa/api/routes/kvad.py:5 add: the error leaves raw. Fix: raise Problem." in out

    code, out, _ = run(repo, "--diff", "change.diff", "--claude", transport=failing, check=_rules(REFUTED)[0])
    assert code == EXIT_OK and "Claude refuted:" in out

    code, out, _ = run(repo, "--diff", "change.diff", "--claude", transport=failing)
    assert code == EXIT_ESCALATE and out.startswith(f"outcome: escalate  (unverified: {WHERE}")


def test_the_pr_pipeline_puts_unsure_checks_to_claude_too(repo: TempRepo) -> None:
    _project(repo)
    check, seen = _rules(CONFIRMED)

    code, _, _ = run(repo, "--diff", "change.diff", "--claude", transport=jev({"f1_swallows_error": noul(0.5)}), check=check)

    assert [d.reason for d in seen[0].doubts] == [f"{WHERE} may swallow an error p=0.50"] and code == EXIT_REQUEST_CHANGES


def test_claude_verifies_only_in_the_pr_pipeline(repo: TempRepo, monkeypatch: Any) -> None:
    _project(repo)
    check, seen = _rules(REFUTED)
    failing = jev({"f1_error_not_problem": noul(0.9)})

    code, _, _ = run(repo, "--diff", "change.diff", transport=failing, check=check, env={"ANTHROPIC_API_KEY": "sk-test"})
    assert code == EXIT_REQUEST_CHANGES and seen == []  # a local run, key or not, never calls Claude

    monkeypatch.setattr(review.command, "claude_check", check)
    run(repo, "--diff", "change.diff", "--claude", transport=failing, env={"ANTHROPIC_API_KEY": "sk-test"})
    assert len(seen) == 1


def test_a_wide_blast_radius_escalates(repo: TempRepo) -> None:
    repo.write_sample_map()
    kernel = "diff --git a/src/pfa/kernel/bus.py b/src/pfa/kernel/bus.py\n--- a/src/pfa/kernel/bus.py\n+++ b/src/pfa/kernel/bus.py\n@@ -1 +1 @@\n-a\n+b\n"
    repo.write_file("change.diff", kernel)

    code, out, _ = run(repo, "--diff", "change.diff", transport=jev())

    assert code == EXIT_ESCALATE and "wide blast radius: the kernel changed" in out


def test_each_edited_application_file_is_judged_whole_in_its_own_call(repo: TempRepo) -> None:
    _project(repo)
    seen: list[dict[str, Any]] = []
    dup = {"type": "choice", "choice": "duplication", "probabilities": {"duplication": 0.7, "none": 0.3}, "confidence": 0.7}

    code, out, _ = run(repo, "--diff", "change.diff", transport=jev({}, seen, {"src/pfa/api/routes/kvad.py": file_answers(2.31, dup)}))

    assert code == EXIT_REQUEST_CHANGES and [("file" in p["state"]) for p in seen] == [False, True]
    assert seen[1]["questions"] == file_questions() and seen[1]["state"]["file"]["content"] == HANDLER
    assert out.startswith("outcome: request_changes  (clean code 2.31/3 in src/pfa/api/routes/kvad.py below the target 2.7)")
    assert "its most probable limit: duplication (0.70): extract the logic written twice into one function" in out


def test_a_failed_file_call_is_exit_3_not_a_verdict(repo: TempRepo) -> None:
    _project(repo)

    def diff_only(payload: dict[str, Any]) -> dict[str, Any]:
        if "file" in payload["state"]:
            raise ReviewError("TypeSafe API unreachable: boom")
        return {"model": "jev-1", "answers": {}, "usage": {}}

    code, _, err = run(repo, "--diff", "change.diff", transport=diff_only)
    assert code == EXIT_ERROR and "unreachable" in err


def test_review_reads_the_diff_from_stdin(repo: TempRepo) -> None:
    code, out, _ = run(repo, "--diff", "-", transport=jev(), stdin=DIFF)

    assert code == EXIT_OK and out.startswith("outcome: comment")


def test_dry_run_prints_the_calls_and_makes_no_call(repo: TempRepo) -> None:
    _project(repo)

    def boom(_: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("must not be called")

    code, out, _ = run(repo, "--diff", "change.diff", "--dry-run", transport=boom)

    calls = json.loads(out)
    assert code == EXIT_OK and "f1_mixes_concerns" in calls["diff"]["questions"] and calls["diff"]["state"]["facts"]["functions_asked"] == 1
    assert [c["state"]["file"]["path"] for c in calls["files"]] == ["src/pfa/api/routes/kvad.py"]
    assert not repo.has_file(".heimdall/telemetry.jsonl")


def test_empty_diff_is_fine(repo: TempRepo) -> None:
    repo.write_file("empty.diff", "")

    code, out, _ = run(repo, "--diff", "empty.diff", transport=jev())

    assert code == EXIT_OK and "nothing to review" in out


def test_missing_key_is_an_error_unless_allowed(repo: TempRepo) -> None:
    repo.write_file("change.diff", DIFF)

    code, _, err = run(repo, "--diff", "change.diff", env={})
    assert code == EXIT_ERROR and "TYPESAFE_API_KEY" in err
    code, out, _ = run(repo, "--diff", "change.diff", "--allow-missing-key", "--markdown", "r.md", env={})
    assert code == EXIT_OK and "skipping" in out and "skipped" in repo.read_file("r.md")


def test_api_failure_is_exit_3_not_a_verdict(repo: TempRepo) -> None:
    repo.write_file("change.diff", DIFF)

    def down(_: dict[str, Any]) -> dict[str, Any]:
        raise ReviewError("TypeSafe API unreachable: boom")

    assert run(repo, "--diff", "change.diff", transport=down)[0] == EXIT_ERROR
    code, _, err = run(repo, "--diff", "change.diff", transport=lambda _p: {"model": "x"})
    assert code == EXIT_ERROR and "malformed" in err


def test_usage_errors_exit_2(repo: TempRepo) -> None:
    assert run(repo, "--nope")[0] == 2 and run(repo, "--base", "main", "--diff", "x")[0] == 2


def test_review_is_reachable_through_the_heimdall_cli(repo: TempRepo) -> None:
    repo.write_file("change.diff", DIFF)

    code, out, _ = repo.run("", "review", "--diff", "change.diff", "--dry-run")

    assert code == 0 and '"out_of_scope"' in out and '"files": []' in out


def test_checks_too_many_for_one_request_go_in_batches_and_every_answer_counts(repo: TempRepo, monkeypatch: Any) -> None:
    _project(repo)
    monkeypatch.setattr(review.command, "MAX_REQUEST_TOKENS", 1)  # no room: every question goes in a batch of its own
    seen: list[dict[str, Any]] = []
    last = "f1_unsafe_input"  # the last function check, so it lands in a later batch than the first

    code, out, _ = run(repo, "--diff", "change.diff", transport=jev({last: noul(0.9)}, seen))

    batches = [p for p in seen if "file" not in p["state"]]
    assert len(batches) > 1 and sum(len(b["questions"]) for b in batches) == len({k for b in batches for k in b["questions"]})
    assert code == EXIT_REQUEST_CHANGES and "passes unvalidated input" in out


def test_an_empty_diff_still_leaves_a_comment_saying_so(repo: TempRepo) -> None:
    repo.write_file("empty.diff", "")

    code, _, _ = run(repo, "--diff", "empty.diff", "--markdown", "out/r.md", transport=jev())

    md = repo.read_file("out/r.md")
    assert code == EXIT_OK and md.startswith("<!-- heimdall-review -->") and "nothing to review" in md
    assert "the base already has every change" in md
