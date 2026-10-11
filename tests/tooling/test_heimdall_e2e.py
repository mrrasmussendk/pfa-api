"""``heimdall e2e``: which scenarios the change reaches (Jev, or the slice map without a key), what the run
owes, and the command's exit codes. The network and the runner are replaced by fakes; the policy and the
command's decisions are what is tested."""

from __future__ import annotations

import io
from typing import Any

import pytest
from conftest import E2E_REQUEST, TempRepo, e2e_scenario, write_e2e_project

from heimdall.commands import e2e
from heimdall.commands.e2e import (
    EXIT_ERROR,
    EXIT_FAILED,
    EXIT_NEEDS_SCENARIOS,
    EXIT_OK,
    Change,
    E2EError,
    RunResult,
    Scenario,
    Seams,
    decide,
    drift_questions,
    fallback,
    questions,
)
from heimdall.commands.review import parse_unified_diff
from heimdall.model import MapModel


def _diff(*paths: str, line: int = 1) -> str:
    """Each path with its ``line`` replaced."""
    return "".join(
        f"diff --git a/{p} b/{p}\nindex 1111111..2222222 100644\n--- a/{p}\n+++ b/{p}\n@@ -{line} +{line} @@\n-old\n+new\n" for p in paths
    )


def _change(repo: TempRepo, *paths: str) -> Change:
    return Change.of(parse_unified_diff(_diff(*paths)), MapModel.load(str(repo.root / ".heimdall/map.json")))


def _answers(new: float | None = 0.1, **by_key: float) -> dict[str, Any]:
    out: dict[str, Any] = {} if new is None else {"needs_new_scenario": {"type": "noul", "noul": new}}
    out.update({key: {"type": "noul", "noul": p} for key, p in by_key.items()})
    return out


def _transport(answers: dict[str, Any], seen: list[dict[str, Any]] | None = None):
    def call(payload: dict[str, Any]) -> dict[str, Any]:
        if seen is not None:
            seen.append(payload)
        return {"model": "jev-1.13.0", "answers": answers}

    return call


def _runner(result: RunResult, ran: list[list[str]] | None = None):
    def run(scenarios: list[Scenario]) -> RunResult:
        if ran is not None:
            ran.append([s.id for s in scenarios])
        return result

    return run


def _run(repo: TempRepo, *args: str, seams: Seams) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = e2e.run(list(args), e2e.Streams(io.StringIO(""), out, err), str(repo.root), seams)
    return code, out.getvalue(), err.getvalue()


GREEN = RunResult(ok=True, output="2 requests, 0 failures")


# ---------------------------------------------------------------- the questions


def test_one_rerun_question_per_scenario_and_one_about_new_scenarios() -> None:
    qs = questions([e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")])

    assert list(qs) == ["rerun_0", "rerun_1", "needs_new_scenario"]
    assert "`scenarios[1]` (rune/carves, method API)" in qs["rerun_1"]["instructions"]


def test_a_scenario_whose_text_or_test_changed_is_asked_whether_the_two_still_agree() -> None:
    scenarios = [e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")]

    qs = questions(scenarios, [scenarios[1]])

    assert list(qs) == ["rerun_0", "rerun_1", "needs_new_scenario", "drifted_1"]
    assert "`scenarios[1].tests`" in qs["drifted_1"]["instructions"]


def test_the_drift_question_alone_names_only_the_edited_scenarios() -> None:
    scenarios = [e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")]

    assert list(drift_questions(scenarios, [scenarios[0]])) == ["drifted_0"]


# ---------------------------------------------------------------- the policy


def test_jev_reruns_a_scenario_at_the_threshold_and_skips_one_below(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scenarios = [e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")]
    answers = _answers(rerun_0=e2e.T["rerun"], rerun_1=e2e.T["rerun"] - 0.01)

    plan = decide(answers, scenarios, _change(repo, "src/pfa/features/kvad/internal/a.py"))

    assert [s.id for s in plan.rerun] == ["kvad/squares"]


def test_a_scenario_whose_own_test_changed_reruns_whatever_jev_says(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scenarios = [e2e_scenario("kvad/squares")]

    plan = decide(_answers(rerun_0=0.0), scenarios, _change(repo, "postman/collections/e2e/kvad/squares.request.yaml"))

    assert [s.id for s in plan.rerun] == ["kvad/squares"]


@pytest.mark.parametrize(("line", "reruns"), [(5, ["kvad/b"]), (2, []), (8, [])])
def test_only_a_scenario_whose_own_lines_of_the_feature_file_changed_reruns(line: int, reruns: list[str]) -> None:
    scenarios = [e2e_scenario("kvad/a", lines=(3, 4)), e2e_scenario("kvad/b", lines=(5, 7))]
    change = Change.of(parse_unified_diff(_diff("tests/e2e/kvad.feature", line=line)), None)

    plan = decide(_answers(rerun_0=0.0, rerun_1=0.0), scenarios, change)

    assert [s.id for s in plan.rerun] == reruns


def test_jev_finding_a_scenario_s_text_and_test_disagree_is_in_the_plan(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scenarios = [e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")]
    change = _change(repo, "postman/collections/e2e/kvad/squares.request.yaml", "postman/collections/e2e/rune/carves.request.yaml")

    plan = decide(_answers(drifted_0=e2e.T["drift"], drifted_1=e2e.T["drift"] - 0.01), scenarios, change)

    assert [(s.id, p) for s, p in plan.drifted] == [("kvad/squares", e2e.T["drift"])]


def test_jev_confident_a_new_scenario_is_needed_asks_for_one(repo: TempRepo) -> None:
    write_e2e_project(repo)

    plan = decide(_answers(new=0.8, rerun_0=0.0), [e2e_scenario("kvad/squares")], _change(repo, "src/pfa/features/kvad/internal/a.py"))

    assert plan.unnamed == 0.8


def test_a_touched_feature_without_scenarios_asks_for_one_whatever_jev_says(repo: TempRepo) -> None:
    write_e2e_project(repo)

    plan = decide(_answers(new=0.0, rerun_0=0.0), [e2e_scenario("kvad/squares")], _change(repo, "src/pfa/features/rune/internal/a.py"))

    assert plan.uncovered == ["rune"] and plan.unnamed is None


def test_without_a_key_scenarios_of_touched_features_and_their_dependents_rerun(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scenarios = [e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")]

    plan = fallback(scenarios, _change(repo, "src/pfa/features/rune/internal/a.py"))

    assert [s.id for s in plan.rerun] == ["kvad/squares", "rune/carves"]


def test_without_a_key_a_dependency_change_leaves_the_upstream_feature_alone(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scenarios = [e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")]

    plan = fallback(scenarios, _change(repo, "src/pfa/features/kvad/internal/a.py"))

    assert [s.id for s in plan.rerun] == ["kvad/squares"]


def test_without_a_key_a_kernel_change_reruns_every_scenario(repo: TempRepo) -> None:
    write_e2e_project(repo)
    scenarios = [e2e_scenario("kvad/squares"), e2e_scenario("rune/carves")]

    plan = fallback(scenarios, _change(repo, "src/pfa/kernel/bus.py"))

    assert [s.id for s in plan.rerun] == ["kvad/squares", "rune/carves"]


# ---------------------------------------------------------------- the command


def test_new_through_the_command_lists_what_it_wrote(repo: TempRepo) -> None:
    write_e2e_project(repo)

    args = ["new", "kvad/rounds", "--method", "API", "--given", "g", "--when", "I call POST /rounds", "--then", "t", "--then", "u"]

    code, out, _ = _run(repo, *args, seams=Seams())

    assert code == EXIT_OK and "postman/collections/e2e/kvad/rounds.request.yaml" in out
    assert repo.read_file("tests/e2e/kvad.feature").endswith("    Then t\n    And u\n")


def test_an_empty_diff_runs_nothing(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", "")
    ran: list[list[str]] = []

    code, out, _ = _run(repo, "--diff", "change.diff", seams=Seams(runner=_runner(GREEN, ran)))

    assert code == EXIT_OK and ran == [] and "nothing changed" in out


def test_jev_sees_the_scenarios_and_the_runner_gets_its_choice(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/internal/a.py"))
    seen: list[dict[str, Any]] = []
    ran: list[list[str]] = []
    seams = Seams(transport=_transport(_answers(rerun_0=0.9, rerun_1=0.1), seen), runner=_runner(GREEN, ran))

    code, _, _ = _run(repo, "--diff", "change.diff", seams=seams)

    assert code == EXIT_OK
    assert [s["id"] for s in seen[0]["state"]["scenarios"]] == ["kvad/squares", "rune/carves"]
    assert ran == [["kvad/squares"]]


def test_jev_reads_the_hunks_of_application_files_only(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/internal/a.py", "docs/guide.md"))
    seen: list[dict[str, Any]] = []

    _run(repo, "--diff", "change.diff", seams=Seams(transport=_transport(_answers(), seen), runner=_runner(GREEN)))

    sent = {f["path"]: "diff" in f for f in seen[0]["state"]["files"]}
    assert sent == {"src/pfa/features/kvad/internal/a.py": True, "docs/guide.md": False}


def test_a_change_outside_the_application_runs_only_the_scenarios_whose_files_changed(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("docs/guide.md", "tests/e2e/rune.feature", line=5))
    ran: list[list[str]] = []
    seams = Seams(transport=_transport(_answers(new=None, drifted_1=0.1)), runner=_runner(GREEN, ran))

    code, _, _ = _run(repo, "--diff", "change.diff", seams=seams)

    assert code == EXIT_OK and ran == [["rune/carves"]]


def test_an_edited_scenario_is_held_to_its_test_by_jev_even_without_application_code(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("postman/collections/e2e/rune/carves.request.yaml"))
    seen: list[dict[str, Any]] = []
    seams = Seams(transport=_transport(_answers(new=None, drifted_1=0.9), seen), runner=_runner(GREEN))

    code, out, _ = _run(repo, "--diff", "change.diff", seams=seams)

    assert list(seen[0]["questions"]) == ["drifted_1"]
    assert seen[0]["state"]["scenarios"][1]["tests"] == {"postman/collections/e2e/rune/carves.request.yaml": E2E_REQUEST}
    assert "tests" not in seen[0]["state"]["scenarios"][0]
    assert code == EXIT_NEEDS_SCENARIOS and "rune/carves's text and its test disagree" in out


def test_without_a_key_an_edited_scenario_is_held_to_its_test_by_its_clauses_alone(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("postman/collections/e2e/rune/carves.request.yaml"))

    code, out, _ = _run(repo, "--diff", "change.diff", seams=Seams(runner=_runner(GREEN), env={}))

    assert code == EXIT_OK and "only their clauses hold a scenario's text to its test" in out


def test_a_feature_s_notes_are_not_application_code_so_jev_is_not_asked(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/AGENTS.md"))
    seen: list[dict[str, Any]] = []
    seams = Seams(transport=_transport(_answers(new=0.9), seen), runner=_runner(GREEN))

    code, _, _ = _run(repo, "--diff", "change.diff", seams=seams)

    assert code == EXIT_OK and seen == []


def test_failing_scenarios_exit_with_the_runner_output(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/internal/a.py"))
    failed = RunResult(ok=False, output="1 failure: status 500")
    seams = Seams(transport=_transport(_answers(rerun_0=0.9, rerun_1=0.9)), runner=_runner(failed))

    code, out, _ = _run(repo, "--diff", "change.diff", seams=seams)

    assert code == EXIT_FAILED and "1 failure: status 500" in out


def test_a_needed_scenario_exits_with_the_command_that_writes_it(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/internal/a.py"))
    seams = Seams(transport=_transport(_answers(new=0.9, rerun_0=0.0, rerun_1=0.0)), runner=_runner(GREEN))

    code, out, _ = _run(repo, "--diff", "change.diff", seams=seams)

    assert code == EXIT_NEEDS_SCENARIOS and "heimdall e2e new <feature>/<name> --method API" in out


def test_an_uncovered_feature_is_named_in_the_command_that_writes_its_scenario(repo: TempRepo) -> None:
    write_e2e_project(repo)
    (repo.root / "tests/e2e/rune.feature").unlink()
    repo.write_file("change.diff", _diff("src/pfa/features/rune/internal/a.py"))

    code, out, _ = _run(repo, "--diff", "change.diff", seams=Seams(transport=_transport(_answers(rerun_0=0.0)), runner=_runner(GREEN)))

    assert code == EXIT_NEEDS_SCENARIOS and "heimdall e2e new rune/<name>" in out


def test_an_invalid_scenario_exits_asking_for_it_to_be_fixed(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/torn.feature", "{")
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/internal/a.py"))

    code, out, _ = _run(repo, "--diff", "change.diff", seams=Seams(transport=_transport(_answers()), runner=_runner(GREEN)))

    assert code == EXIT_NEEDS_SCENARIOS and "fix tests/e2e/torn.feature: line 1" in out


def test_a_green_run_is_remembered_so_the_same_change_is_not_run_again(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/internal/a.py"))
    ran: list[list[str]] = []
    seams = Seams(transport=_transport(_answers(rerun_0=0.9, rerun_1=0.0)), runner=_runner(GREEN, ran))
    _run(repo, "--diff", "change.diff", seams=seams)

    code, out, _ = _run(repo, "--diff", "change.diff", seams=seams)

    assert code == EXIT_OK and ran == [["kvad/squares"]] and "unchanged since the last green run" in out


def test_without_a_key_the_slice_map_decides(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/rune/internal/a.py"))
    ran: list[list[str]] = []

    code, out, _ = _run(repo, "--diff", "change.diff", seams=Seams(runner=_runner(GREEN, ran), env={}))

    assert code == EXIT_OK and ran == [["kvad/squares", "rune/carves"]] and "TYPESAFE_API_KEY is not set" in out


def test_a_runner_that_cannot_start_exits_could_not_run(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("change.diff", _diff("src/pfa/features/kvad/internal/a.py"))

    def broken(scenarios: list[Scenario]) -> RunResult:
        raise E2EError("postman not found on PATH")

    code, _, err = _run(repo, "--diff", "change.diff", seams=Seams(transport=_transport(_answers(rerun_0=0.9)), runner=broken))

    assert code == EXIT_ERROR and "postman not found on PATH" in err


def test_all_runs_every_scenario_without_a_change_or_jev(repo: TempRepo) -> None:
    write_e2e_project(repo)
    seen: list[dict[str, Any]] = []
    ran: list[list[str]] = []

    code, out, _ = _run(repo, "--all", seams=Seams(transport=_transport(_answers(), seen), runner=_runner(GREEN, ran)))

    assert code == EXIT_OK and seen == [] and ran == [["kvad/squares", "rune/carves"]] and "green" in out


def test_all_without_scenarios_says_there_are_none(repo: TempRepo) -> None:
    write_e2e_project(repo)
    (repo.root / "tests/e2e/kvad.feature").unlink()
    (repo.root / "tests/e2e/rune.feature").unlink()

    code, out, _ = _run(repo, "--all", seams=Seams(runner=_runner(GREEN)))

    assert code == EXIT_OK and "no scenarios under tests/e2e" in out and "reached by this change" not in out


def test_all_exits_failed_when_a_scenario_fails(repo: TempRepo) -> None:
    write_e2e_project(repo)

    code, out, _ = _run(repo, "--all", seams=Seams(runner=_runner(RunResult(ok=False, output="1 failure: status 500"))))

    assert code == EXIT_FAILED and "1 failure: status 500" in out


def test_all_asks_for_an_invalid_scenario_to_be_fixed(repo: TempRepo) -> None:
    write_e2e_project(repo)
    repo.write_file("tests/e2e/torn.feature", "{")

    code, out, _ = _run(repo, "--all", seams=Seams(runner=_runner(GREEN)))

    assert code == EXIT_NEEDS_SCENARIOS and "fix tests/e2e/torn.feature" in out


@pytest.mark.parametrize(
    "args", [["--nope"], ["--base", "x", "--diff", "y"], ["--all", "--base", "x"], ["new"], ["new", "kvad/x", "--given", "g"]]
)
def test_bad_arguments_are_a_usage_error(repo: TempRepo, args: list[str]) -> None:
    write_e2e_project(repo)

    code, _, err = _run(repo, *args, seams=Seams())

    assert code == EXIT_ERROR and "usage" in err


def test_a_base_that_git_would_read_as_an_option_is_refused(tmp_path) -> None:
    from heimdall.commands.e2e import working_diff

    with pytest.raises(E2EError, match="not an option"):
        working_diff(str(tmp_path), "--output=/tmp/x")
