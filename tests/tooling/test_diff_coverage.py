"""``tools/diff_coverage.py``: which changed application lines the tests ran, and the share against the floor."""

from __future__ import annotations

from diff_coverage import FileReport, changed_lines, percent, report

DIFF = (
    "diff --git a/src/pfa/a.py b/src/pfa/a.py\n--- a/src/pfa/a.py\n+++ b/src/pfa/a.py\n"
    "@@ -3,0 +4,3 @@\n+x\n+y\n+z\n@@ -10 +13 @@\n-old\n+new\n"
    "diff --git a/tests/t.py b/tests/t.py\n--- a/tests/t.py\n+++ b/tests/t.py\n@@ -1 +1 @@\n-a\n+b\n"
    "diff --git a/src/pfa/gone.py b/src/pfa/gone.py\n--- a/src/pfa/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    "diff --git a/src/pfa/README.md b/src/pfa/README.md\n--- a/src/pfa/README.md\n+++ b/src/pfa/README.md\n@@ -1 +1 @@\n-a\n+b\n"
)


def test_only_added_lines_of_application_python_count() -> None:
    assert changed_lines(DIFF) == {"src/pfa/a.py": {4, 5, 6, 13}}


def test_lines_coverage_cannot_run_are_left_out_and_unrun_ones_are_missed() -> None:
    coverage = {"src/pfa/a.py": {"executed_lines": [4, 6], "missing_lines": [13, 20]}}

    (r,) = report({"src/pfa/a.py": {4, 5, 6, 13}}, coverage)

    assert (r.executable, r.missed) == ({4, 6, 13}, {13})


def test_a_changed_file_no_test_imports_is_wholly_untested() -> None:
    (r,) = report({"src/pfa/new.py": {1, 2, 3}}, {})

    assert r.missed == {1, 2, 3} and percent([r]) == 0.0


def test_the_share_is_over_every_changed_executable_line() -> None:
    reports = [FileReport("a", frozenset({1, 2, 3, 4}), frozenset({4})), FileReport("b", frozenset({1, 2, 3, 4, 5, 6}), frozenset())]

    assert percent(reports) == 90.0


def test_a_change_without_executable_lines_is_fully_covered() -> None:
    assert report({"src/pfa/a.py": {1}}, {"src/pfa/a.py": {"executed_lines": [], "missing_lines": []}}) == []
    assert percent([]) == 100.0


def test_missed_lines_are_named_as_ranges() -> None:
    assert FileReport("a", frozenset(), frozenset({3, 4, 5, 9, 11, 12})).ranges == "3-5, 9, 11-12"
