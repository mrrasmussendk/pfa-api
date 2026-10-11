"""The PR comment reads top-down: a headline that says how many things to fix, each one as a place, what is wrong
and the fix, and everything else (what Claude cleared, the unsure notes, the per-file scores, what the judge was
told) folded away below."""

from __future__ import annotations

from typing import Any

import pytest

from heimdall.commands import review
from heimdall.commands.review import build_state
from heimdall.commands.review.comment import render_comment
from heimdall.commands.review.policy import FAIL, PASS, UNSURE, Judgment, Row, decide
from heimdall.commands.review.verify import CONFIRMED, REFUTED, Evidence, Finding, Ruling, settle

SOURCE = "def split(text, budget):\n    if budget < 1:\n        return []\n    return [text]\n"
EDGE = Row("f1_mishandles_edge", "mishandles_edge", "a.py:1 split", 0.8, FAIL)
SWALLOW = Row("f1_swallows_error", "swallows_error", "a.py:1 split", 0.5, UNSURE)
LEAK = {
    "path": "src/pfa/features/kvad/internal/leak.py",
    "line": 3,
    "rule": "EIT001",
    "message": "imports rune internals",
    "fix": "use the contract",
}
STATE = build_state([], None, None)


def _verdict(*rows: Row, facts: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> Any:
    return decide(Judgment(list(rows), files or {}, {}, "jev-1", {}), facts or {})


def _claude(*rulings: Ruling):
    return lambda inquiry: list(rulings)


def _comment(v: Any, facts: dict[str, Any] | None = None) -> str:
    return render_comment(v, {**STATE, "facts": {**STATE["facts"], **(facts or {})}})


def test_a_confirmed_finding_is_the_first_thing_read_with_its_place_and_fix() -> None:
    finding = Finding("a.py", "split", 0, "if budget < 1:", "a budget of 0 returns nothing.", "raise instead.")
    v = settle(
        _verdict(EDGE, SWALLOW),
        _claude(Ruling(CONFIRMED, "seen", (finding,)), Ruling(REFUTED, "it re-raises")),
        Evidence("t", [], {"a.py": SOURCE}),
    )

    md = _comment(v)

    assert "## 🛑 Heimdall review: 1 to fix before merging" in md
    assert "1. **`a.py:2` · `split`** — a budget of 0 returns nothing\n   **Fix:** raise instead" in md
    assert "Claude confirmed 1, cleared 1" in md
    assert "<details><summary>1 check Claude cleared</summary>" in md and "| it re-raises |" in md
    assert md.index("Fix before merging") < md.index("Claude cleared")  # the action before the evidence


def test_a_failing_check_in_the_agent_pipeline_reads_as_place_problem_fix() -> None:
    md = _comment(_verdict(EDGE))

    assert "1. **`a.py:1` · `split`** — mishandles an edge input\n   **Fix:** Find the empty, None or boundary input" in md
    assert "p=0.80" not in md.split("<details>")[0]  # probabilities are evidence, folded away, not in the action


def test_a_wall_violation_reads_as_its_line_rule_and_fix() -> None:
    md = _comment(_verdict(facts={"wall_violations": [LEAK]}), {"wall_violations": [LEAK]})

    assert "1. **`src/pfa/features/kvad/internal/leak.py:3`** — EIT001: imports rune internals\n   **Fix:** use the contract" in md


def test_a_file_below_the_clean_code_target_reads_as_the_file_and_its_fix() -> None:
    low = {"clean_code": {"type": "score", "score": 2.31}, "clean_code_limit": {"type": "choice", "probabilities": {"duplication": 0.7}}}

    md = _comment(_verdict(files={"src/pfa/api/routes/kvad.py": low}))

    assert "1. **`src/pfa/api/routes/kvad.py`** — clean code 2.31/3, below the target 2.7" in md
    assert "**Fix:** extract the logic written twice into one function" in md


def test_nothing_failing_says_so_and_folds_the_unsure_notes() -> None:
    md = _comment(_verdict(SWALLOW, Row("k", "untested", "a.py:1 split", 0.1, PASS)))

    assert "## 💬 Heimdall review: nothing to fix" in md and "A person still decides the merge." in md
    assert "<details><summary>1 unsure check</summary>" in md and "| `a.py:1` · `split` | may swallow an error | 0.50 |" in md


def test_a_wide_reach_asks_a_person_to_look() -> None:
    md = _comment(_verdict(facts={"kernel_changed": True}))

    assert "## 🙋 Heimdall review: a person should look" in md and "- **wide blast radius: the kernel changed" in md


def test_no_sentence_ends_in_a_doubled_full_stop() -> None:
    finding = Finding("a.py", "split", 0, "if budget < 1:", "untested.", "add a test.")
    v = settle(_verdict(EDGE), _claude(Ruling(CONFIRMED, "seen", (finding,))), Evidence("t", [], {"a.py": SOURCE}))

    assert ".." not in _comment(v) and ".." not in v.advice[0]


def test_a_check_about_the_change_as_a_whole_says_so_in_the_table() -> None:
    md = _comment(_verdict(Row("docs_stale", "docs_stale", "the change", 0.57, UNSURE)))

    assert "| the change | may change behaviour with no doc updated | 0.57 |" in md


def test_comment_explains_truncated_diff_and_craft_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review.state, "MAX_FILE_TOKENS", 1)
    diff = (
        "diff --git a/src/pfa/features/kvad/internal/change.py b/src/pfa/features/kvad/internal/change.py\n"
        "--- a/src/pfa/features/kvad/internal/change.py\n"
        "+++ b/src/pfa/features/kvad/internal/change.py\n"
        "@@ -1 +1,2 @@\n-old\n+new\n+another line\n"
    )
    state = build_state(review.parse_unified_diff(diff), None, None)
    state["facts"]["craft_files"] = ["src/pfa/features/kvad/internal/change.py", "src/pfa/api/routes/kvad.py"]
    state["facts"]["craft_truncated"] = ["src/pfa/features/kvad/internal/change.py"]

    md = render_comment(_verdict(), state)

    assert "were cut and the craft scores rate what the judge saw" in md
    assert "- judged whole for clean code, one call each: src/pfa/features/kvad/internal/change.py, src/pfa/api/routes/kvad.py" in md
    assert "- cut at the token budget when judged whole: src/pfa/features/kvad/internal/change.py" in md
