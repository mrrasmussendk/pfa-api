"""The craft pass: each edited application Python file is read whole and judged on its own, so clean code is
scored on the file the change leaves behind. Below the author's floor of 2.7 the review names the file, what
limits it, and blocks."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import SHAPED_SOURCE, TempRepo
from review_support import DIFF, KVAD_ENGINE, ROUTE_SOURCE, file_answers, sample_map

from brokkr import estimate
from heimdall.commands.review import craft_files, craft_state, parse_unified_diff
from heimdall.commands.review.policy import Judgment, decide

LONG_ONE = {"path": KVAD_ENGINE, "line": 1, "name": "long_one", "lines": 46, "params": 0, "over": ["lines"]}
DUP = {"type": "choice", "choice": "duplication", "probabilities": {"duplication": 0.7}, "confidence": 0.7}
OTHER = "src/pfa/features/kvad/internal/other.py"


def _decide(files: dict[str, dict[str, Any]], facts: dict[str, Any] | None = None) -> Any:
    return decide(Judgment([], files, {}, "jev", {}), facts or {})


def test_craft_files_are_the_applications_python_read_whole_with_the_lines_the_change_touched(repo: TempRepo) -> None:
    repo.write_file("src/pfa/api/routes/kvad.py", ROUTE_SOURCE)
    repo.write_file("src/pfa/features/rune/contract/rune_service.py", "class RuneService:\n    def new_member(self): ...\n    pass\n")
    repo.write_file("tests/api/test_kvad.py", "def test_it():\n    assert True\n")
    repo.write_file("harness/guides/x.md", "new\n")

    found = craft_files(str(repo.root), parse_unified_diff(DIFF), sample_map())

    assert [(f.path, f.area, f.changed_lines) for f in found] == [
        ("src/pfa/api/routes/kvad.py", "slice:kvad", [2, 4]),
        ("src/pfa/features/rune/contract/rune_service.py", "contract:rune", [2]),
    ]
    assert found[0].content == ROUTE_SOURCE


def test_a_deleted_file_or_one_not_on_disk_is_not_judged_whole(repo: TempRepo) -> None:
    gone = "diff --git a/src/pfa/kernel/gone.py b/src/pfa/kernel/gone.py\ndeleted file mode 100644\n--- a/src/pfa/kernel/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    missing = "diff --git a/src/pfa/kernel/missing.py b/src/pfa/kernel/missing.py\n--- a/src/pfa/kernel/missing.py\n+++ b/src/pfa/kernel/missing.py\n@@ -1 +1 @@\n-a\n+b\n"
    repo.write_file("src/pfa/kernel/gone.py", "x = 1\n")

    assert craft_files(str(repo.root), parse_unified_diff(gone + missing), sample_map()) == []


def test_the_tooling_is_never_judged_whole(repo: TempRepo) -> None:
    repo.write_file("tools/heimdall/x.py", "a = 2\n")
    tooling = "diff --git a/tools/heimdall/x.py b/tools/heimdall/x.py\n--- a/tools/heimdall/x.py\n+++ b/tools/heimdall/x.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"

    assert craft_files(str(repo.root), parse_unified_diff(tooling), sample_map()) == []


def test_craft_state_is_the_file_whole_with_its_own_shape_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    from heimdall.commands.review import craft

    f = craft.CraftFile(KVAD_ENGINE, "slice:kvad", SHAPED_SOURCE, [10])
    state = craft_state(f, "a task", [LONG_ONE, {**LONG_ONE, "path": OTHER}])
    assert state["file"] == {"path": KVAD_ENGINE, "area": "slice:kvad", "content": SHAPED_SOURCE}
    assert state["facts"]["changed_lines"] == [10] and state["facts"]["function_shape"] == [LONG_ONE]  # only this file's
    assert state["facts"]["file_tokens"] == state["facts"]["state_tokens"] == estimate(SHAPED_SOURCE)

    monkeypatch.setattr(craft, "MAX_CRAFT_FILE_TOKENS", 10)
    cut = craft_state(f, None, [])
    assert cut["facts"]["truncated"] is True and cut["file"]["content"].endswith("more lines not shown]")


def test_clean_code_below_the_target_blocks_naming_the_file_and_what_limits_it() -> None:
    """The author's floor (2026-10-08): clean code 2.7 on 0–3, 90%, not to be lowered, judged per file, whole."""
    v = _decide({OTHER: file_answers(2.9), KVAD_ENGINE: file_answers(2.28, DUP)})

    assert v.outcome == "request_changes" and v.reasons == [f"clean code 2.28/3 in {KVAD_ENGINE} below the target 2.7"]
    assert f"The judge rates the whole file {KVAD_ENGINE} 2.28 on 0–3 (3 is exemplary); the target is 2.7." in v.advice[0]
    assert "its most probable limit: duplication (0.70): extract the logic written twice" in v.advice[0]


def test_each_file_below_the_target_is_its_own_block_lowest_first() -> None:
    v = _decide({OTHER: file_answers(2.1, DUP), KVAD_ENGINE: file_answers(2.28, DUP)})

    assert v.reasons == [f"clean code 2.10/3 in {OTHER} below the target 2.7", f"clean code 2.28/3 in {KVAD_ENGINE} below the target 2.7"]


def test_the_runner_up_label_is_the_fix_when_none_wins_and_every_fix_when_there_is_none() -> None:
    """``none`` wins the choice under a short score (PR #9: none at 0.40); the runner-up is the fix."""
    none_first = {"type": "choice", "choice": "none", "probabilities": {"none": 0.40, "comments": 0.25, "style": 0.2}}

    assert "its most probable limit: comments (0.25)" in _decide({KVAD_ENGINE: file_answers(2.28, none_first)}).advice[0]
    bare = _decide({KVAD_ENGINE: file_answers(2.28, {"type": "choice", "choice": "none"})}).advice[0]
    assert "the judge named no single limit, so: extract the logic written twice" in bare and "never swallow one" in bare


def test_a_file_the_judge_saw_cut_is_said_so_and_one_on_target_passes() -> None:
    cut = _decide({KVAD_ENGINE: file_answers(2.28, DUP)}, {"craft_truncated": [KVAD_ENGINE]})

    assert "The judge saw the file cut at the token budget" in cut.advice[0]
    assert _decide({KVAD_ENGINE: file_answers(2.7)}).outcome == "comment"
