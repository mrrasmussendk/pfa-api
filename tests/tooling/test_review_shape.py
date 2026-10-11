"""Function shape: the changed functions over 40 lines or 5 parameters, read from the AST, are facts in the state
and in the report. They never decide alone; the per-function checks do."""

from __future__ import annotations

from typing import Any

from conftest import SHAPED_SOURCE, TempRepo
from review_support import KVAD_ENGINE

from heimdall.commands.review import build_state, function_shape, parse_unified_diff, render_comment, render_table
from heimdall.commands.review.policy import Judgment, decide
from heimdall.commands.review.state import Known
from heimdall.model import MapModel

LONG_ONE = {"path": KVAD_ENGINE, "line": 1, "name": "long_one", "lines": 46, "params": 0, "over": ["lines"]}
WIDE = {"path": KVAD_ENGINE, "line": 60, "name": "wide", "lines": 2, "params": 7, "over": ["params"]}


def _shape_diff(path: str, new_line: int, text: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -{new_line},1 +{new_line},2 @@\n {text}\n+    pass\n"


def test_function_shape_names_only_the_changed_functions_over_the_limits(repo: TempRepo) -> None:
    repo.write_sample_map()
    repo.write_file(KVAD_ENGINE, SHAPED_SOURCE)
    repo.write_file("tests/api/test_long.py", SHAPED_SOURCE)
    m = MapModel.load(str(repo.root / ".heimdall" / "map.json"))
    root = str(repo.root)

    assert function_shape(root, parse_unified_diff(_shape_diff(KVAD_ENGINE, 10, "x8 = 8")), m) == [LONG_ONE]
    assert function_shape(root, parse_unified_diff(_shape_diff(KVAD_ENGINE, 50, "return a + b")), m) == []
    assert function_shape(root, parse_unified_diff(_shape_diff("tests/api/test_long.py", 10, "x8 = 8")), m) == []
    assert function_shape(root, parse_unified_diff(_shape_diff("src/pfa/kernel/gone.py", 10, "x8 = 8")), m) == []


def test_the_shape_facts_ride_in_the_state() -> None:
    state = build_state([], None, None, Known(shape=[LONG_ONE]))

    assert state["facts"]["function_shape"] == [LONG_ONE] and state["facts"]["function_limits"] == {"lines": 40, "params": 5}


def _facts(**over: Any) -> dict[str, Any]:
    base = {"files_changed": 1, "lines_added": 1, "lines_removed": 0, "slices_touched": ["kvad"], "cross_slice_change": False}
    base |= {"contracts_touched": {}, "routes_changed": False, "tests_changed": [], "docs_changed": False, "files_truncated": []}
    return base | {"function_shape": [LONG_ONE, WIDE], "function_limits": {"lines": 40, "params": 5}, **over}


def test_the_comment_and_the_table_list_the_functions_over_the_limits() -> None:
    facts = _facts()
    v = decide(Judgment([], {}, {}, "jev-1", {}), facts)

    md, table = render_comment(v, {"facts": facts}), render_table(v, facts)

    assert v.outcome == "comment"  # a long function is a fact for the reader, not a verdict
    assert "- changed functions over 40 lines or 5 parameters:" in md
    assert f"  - `{KVAD_ENGINE}:1 long_one (46 lines, 0 params)`" in md
    assert f"over the limits: {KVAD_ENGINE}:60 wide (2 lines, 7 params)" in table
