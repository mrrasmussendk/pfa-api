"""The state Jev is given: the diff cut per file, the facts Heimdall already knows, the changed functions the
checks are about, all held to the budget, with vendored and generated files left out."""

from __future__ import annotations

import json

import pytest
from conftest import TempRepo
from review_support import DIFF, run, sample_map

from brokkr import estimate
from heimdall.commands import review
from heimdall.commands.review import EXIT_OK, Known, build_state, parse_unified_diff
from heimdall.commands.review.state import area_of
from heimdall.commands.review.vendored import is_vendored


def test_diff_is_parsed_per_file_and_generated_files_are_ignored() -> None:
    files = parse_unified_diff(DIFF)

    assert [f.path for f in files] == [
        "src/pfa/api/routes/kvad.py",
        "src/pfa/features/rune/contract/rune_service.py",
        "tests/api/test_kvad.py",
        "harness/guides/x.md",
    ]
    assert (files[0].status, files[0].added, files[0].removed) == ("modified", 2, 1) and files[2].status == "added"
    assert "raise Problem.domain_rejection(result.error)" in files[0].text


def test_state_carries_heimdalls_facts_and_the_architecture() -> None:
    state = build_state(parse_unified_diff(DIFF), sample_map(), "Switch kvad errors to Problem Details")

    facts = state["facts"]
    assert facts["slices_touched"] == ["kvad", "rune"] and facts["cross_slice_change"] is True
    assert facts["contracts_touched"] == {"rune": {"fan_in": 12, "frozen": True}}
    assert facts["routes_changed"] is True and facts["source_changed"] is True and facts["docs_changed"] is True
    assert facts["kernel_changed"] is False and facts["shared_changed"] is False
    assert facts["state_budget_tokens"] == 28_000 and facts["judge_window_tokens"] == 32_000
    assert facts["tests_changed"] == ["tests/api/test_kvad.py"]
    areas = {e["path"]: e["area"] for e in state["files"]}
    assert (
        areas["src/pfa/api/routes/kvad.py"] == "slice:kvad" and areas["src/pfa/features/rune/contract/rune_service.py"] == "contract:rune"
    )
    assert areas["tests/api/test_kvad.py"] == "tests" and areas["harness/guides/x.md"] == "docs"
    assert state["architecture"]["slices"]["kvad"]["depends_on"] == ["rune"] and state["task"].startswith("Switch kvad")


def test_the_kernel_and_the_application_outside_the_features_are_facts() -> None:
    kernel = "diff --git a/src/pfa/kernel/bus.py b/src/pfa/kernel/bus.py\n--- a/src/pfa/kernel/bus.py\n+++ b/src/pfa/kernel/bus.py\n@@ -1 +1 @@\n-a\n+b\n"
    app = "diff --git a/src/pfa/application.py b/src/pfa/application.py\n--- a/src/pfa/application.py\n+++ b/src/pfa/application.py\n@@ -1 +1 @@\n-a\n+b\n"

    assert build_state(parse_unified_diff(kernel), sample_map(), None)["facts"]["kernel_changed"] is True
    assert build_state(parse_unified_diff(app), sample_map(), None)["facts"]["shared_changed"] is True


def test_the_changed_functions_ride_in_the_state_by_id() -> None:
    functions = {"f1": {"path": "a.py", "line": 1, "name": "f", "route": False, "source": "def f(): ..."}}

    state = build_state(parse_unified_diff(DIFF), sample_map(), None, Known(functions=functions))

    assert state["functions"] == functions


def test_oversized_files_are_truncated_and_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review.state, "MAX_FILE_TOKENS", 10)

    state = build_state(parse_unified_diff(DIFF), sample_map(), None)

    assert "src/pfa/api/routes/kvad.py" in state["facts"]["files_truncated"]
    assert "more lines not shown" in next(e for e in state["files"] if e["path"].endswith("routes/kvad.py"))["diff"]
    assert state["facts"]["state_tokens"] < state["facts"]["diff_tokens"]


@pytest.mark.parametrize(
    ("path", "area"),
    [
        ("src/pfa/features/__init__.py", "app"),
        ("src/pfa/features/kvad/internal/x.py", "slice:kvad"),
        ("src/pfa/features/kvad/AGENTS.md", "docs"),
    ],
)
def test_each_path_has_its_area(path: str, area: str) -> None:
    assert area_of(path, sample_map()) == area


def _doc_diff(n: int) -> str:
    return "".join(
        f"diff --git a/docs/d{i}.md b/docs/d{i}.md\n--- a/docs/d{i}.md\n+++ b/docs/d{i}.md\n@@ -1 +1 @@\n-old\n+new line {i}\n"
        for i in range(n)
    )


def test_the_whole_state_fits_the_budget_however_many_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review.state, "MAX_STATE_TOKENS", 3_000)

    state = build_state(parse_unified_diff(DIFF + _doc_diff(300)), sample_map(), None)

    assert estimate(json.dumps(state, ensure_ascii=False)) <= 3_000
    assert state["facts"]["files_changed"] == 304 and state["facts"]["files_not_shown"] > 0
    assert any(e["path"] == "src/pfa/api/routes/kvad.py" for e in state["files"])  # the code is the last to go


def test_the_budget_is_spent_on_the_source_before_tests_and_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    files = parse_unified_diff(DIFF)
    files.reverse()  # git lists the docs first; the judge must still see the code
    source = [f for f in files if f.path.startswith("src/")]
    monkeypatch.setattr(
        review.state, "MAX_STATE_TOKENS", estimate(json.dumps(build_state(source, sample_map(), None), ensure_ascii=False)) + 60
    )

    state = build_state(files, sample_map(), None)

    assert [e["area"] for e in state["files"]][:2] == ["slice:kvad", "contract:rune"]
    assert state["facts"]["files_not_shown"] + len(state["files"]) == 4


def test_vendored_files_are_left_out_of_the_review(repo: TempRepo) -> None:
    repo.write_file(".gitattributes", "* text=auto\npostman/skills/** linguist-vendored\n")
    vendored = "diff --git a/postman/skills/x/SKILL.md b/postman/skills/x/SKILL.md\n--- a/postman/skills/x/SKILL.md\n+++ b/postman/skills/x/SKILL.md\n@@ -1 +1 @@\n-a\n+b\n"
    repo.write_file("change.diff", vendored + DIFF)

    code, out, _ = run(repo, "--diff", "change.diff", "--dry-run")

    facts = json.loads(out)["diff"]["state"]["facts"]
    assert code == EXIT_OK and facts["files_changed"] == 4 and facts["vendored_files"] == 1


@pytest.mark.parametrize(
    ("path", "pattern", "vendored"),
    [
        ("postman/skills/x/SKILL.md", "postman/skills/**", True),
        ("postman/collections/e2e/a.yaml", "postman/skills/**", False),
        ("third_party/lib/a.py", "/third_party/", True),
        (".postman/skills.json", ".postman/skills.json", True),
        ("web/dist/app.min.js", "*.min.js", True),
        ("gen/a.py", "**/gen/**", True),
        ("x/gen/a.py", "**/gen/**", True),
        ("src/pfa/app.py", "*.min.js", False),
    ],
)
def test_vendored_patterns_match_as_git_does(path: str, pattern: str, vendored: bool) -> None:
    assert is_vendored(path, [pattern]) is vendored


def test_exported_diagrams_are_dropped_from_the_diff() -> None:
    svg = "diff --git a/docs/diagrams/x.svg b/docs/diagrams/x.svg\n--- a/docs/diagrams/x.svg\n+++ b/docs/diagrams/x.svg\n@@ -1 +1 @@\n-<svg/>\n+<svg></svg>\n"

    assert [f.path for f in parse_unified_diff(svg + DIFF)] == [f.path for f in parse_unified_diff(DIFF)]
