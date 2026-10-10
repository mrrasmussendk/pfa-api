"""The walls are facts, not judgments: Eitri's findings on the changed files block whatever the judge says, and
the comment says what went wrong, with the file, the line and the fix, before any numbers."""

from __future__ import annotations

from typing import Any

from conftest import TempProject, TempRepo
from review_support import DIFF

from heimdall.commands.review import build_state, parse_unified_diff, render_markdown, render_table, wall_findings
from heimdall.commands.review.policy import FAIL, Judgment, Row, decide
from heimdall.commands.review.state import Known

LEAK = {
    "path": "src/pfa/features/kvad/internal/leak.py",
    "line": 3,
    "rule": "EIT001",
    "message": "imports rune internals",
    "fix": "use the contract",
}


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


def _verdict(facts: dict[str, Any], *rows: Row) -> Any:
    return decide(Judgment(list(rows), {}, {}, "jev-1", {}), facts)


def test_eitri_findings_block_whatever_the_judge_says() -> None:
    v = _verdict(_facts(wall_violations=[LEAK]))

    assert v.outcome == "request_changes" and v.reasons[0].startswith("Eitri found 1 wall violation") and "eitri check" in v.advice[0]


def test_wall_findings_come_from_eitri_and_only_for_changed_files(project: TempProject) -> None:
    project.slice("rune", **{"internal/__init__.py": "", "internal/rune_engine.py": "class RuneEngine: ..."})
    leak = "from pfa.features.rune.internal.rune_engine import RuneEngine\n"
    project.slice("kvad", ["rune"], **{"internal/__init__.py": "", "internal/leak.py": leak, "internal/old_leak.py": leak})
    project.write("harness/rules/EIT001.md", "# EIT001\nWhy it matters.\n**Fix:** import the dependency's `contract` package instead.\n")
    changed = parse_unified_diff(
        "diff --git a/pfa/features/kvad/internal/leak.py b/pfa/features/kvad/internal/leak.py\n"
        "new file mode 100644\n--- /dev/null\n+++ b/pfa/features/kvad/internal/leak.py\n@@ -0,0 +1 @@\n" + "+" + leak
    )

    found = wall_findings(str(project.root), changed)

    assert [(f["path"], f["line"], f["rule"]) for f in found] == [("pfa/features/kvad/internal/leak.py", 1, "EIT001")]
    assert found[0]["fix"] == "import the dependency's `contract` package instead." and found[0]["doc"] == "harness/rules/EIT001.md"
    assert build_state(changed, None, None, Known(walls=found))["facts"]["wall_violations"] == found


def test_wall_findings_are_empty_without_a_slice_tree(repo: TempRepo) -> None:
    assert wall_findings(str(repo.root), parse_unified_diff(DIFF)) == []


def test_the_comment_says_what_went_wrong_and_what_to_do() -> None:
    facts = _facts(wall_violations=[LEAK])
    failing = Row("f1_swallows_error", "swallows_error", "src/pfa/features/kvad/internal/leak.py:3 load", 0.8, FAIL)
    v = _verdict(facts, failing)

    md, table = render_markdown(v, {"facts": facts}), render_table(v, facts)

    assert "### What went wrong" in md and "**Next:** fix what is named above and push" in md
    assert "**Eitri found 1 wall violation(s) in the changed files.** The change crosses a slice wall." in md
    assert "- `src/pfa/features/kvad/internal/leak.py:3 — EIT001: imports rune internals` **Fix:** use the contract" in md
    assert "**src/pfa/features/kvad/internal/leak.py:3 load swallows an error p=0.80.**" in md
    assert md.index("What went wrong") < md.index("| check | where | p |")  # the explanation comes before the numbers
    assert "leak.py:3 — EIT001" in table and "load swallows an error p=0.80: Handle the exception" in table


def test_a_comment_says_no_check_failed_and_that_a_person_merges() -> None:
    md = render_markdown(_verdict(_facts()), {"facts": _facts()})

    assert "**comment**" in md and "A person still decides the merge" in md
