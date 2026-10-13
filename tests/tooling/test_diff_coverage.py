"""``tools/diff_coverage.py``: which changed application lines the tests ran, and the share against the floor."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from diff_coverage import FileReport, changed_lines, coverage_files, diff_since, percent, report

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


# ---------------------------------------------------------------- the change, from git


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root, check=True, capture_output=True)


def test_the_change_holds_edited_tracked_lines_and_every_untracked_file_whatever_its_name(tmp_path: Path) -> None:
    (tmp_path / "src/pfa").mkdir(parents=True)
    (tmp_path / "src/pfa/old.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "src/pfa/two words.py").write_text("a = 1\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "src/pfa/old.py").write_text("a = 1\nb = 3\nc = 4\n", encoding="utf-8")
    (tmp_path / "src/pfa/æble kage.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (tmp_path / "src/pfa/two words.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (tmp_path / "notes.py").write_text("outside = True\n", encoding="utf-8")

    changed = changed_lines(diff_since("HEAD", tmp_path))

    assert changed == {"src/pfa/old.py": {2, 3}, "src/pfa/two words.py": {2}, "src/pfa/æble kage.py": {1, 2}}


def test_a_base_that_git_would_read_as_an_option_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not an option"):
        diff_since("--output=/tmp/x", tmp_path)


def test_coverage_keys_become_repo_relative_posix_paths_however_coverage_wrote_them(tmp_path: Path) -> None:
    data = {"executed_lines": [1], "missing_lines": []}
    keys = {"src/pfa/a.py": data, "src\\pfa\\b.py": data, str(tmp_path / "src" / "pfa" / "c.py"): data, "/elsewhere/d.py": data}
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps({"files": keys}), encoding="utf-8")

    assert set(coverage_files(report_path, tmp_path)) == {"src/pfa/a.py", "src/pfa/b.py", "src/pfa/c.py", "/elsewhere/d.py"}


def test_git_is_never_handed_an_option_it_was_not_written_to_pass(tmp_path: Path) -> None:
    from diff_coverage import _git

    with pytest.raises(SystemExit, match="refusing git diff"):
        _git(tmp_path, "diff", "--output=/tmp/x")
