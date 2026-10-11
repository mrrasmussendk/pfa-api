"""EIT008: ``eitri check --base`` holds every contract to its shape at the merge base. Each test commits a
tree, changes it in the working copy, and checks it against ``HEAD``."""

from __future__ import annotations

import io
import subprocess

import pytest
from conftest import TempProject

from eitri import EitriConfig, analyze
from eitri.analyzer import EitriError
from eitri.cli import run

CONTRACT = """
from dataclasses import dataclass


@dataclass(frozen=True)
class Split:
    text: str
    budget: int


def count(text: str) -> int: ...
"""
CONSUMER = "from pfa.features.rune.contract import Split, count\n\nx = Split('a', 1)\n"


def _git(project: TempProject, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project.root, check=True, capture_output=True)


def _committed(project: TempProject, consumer: str = CONSUMER) -> TempProject:
    """``rune`` with a contract, ``kvad`` consuming it with ``consumer``, all committed."""
    project.slice("rune", **{"contract/__init__.py": CONTRACT})
    project.slice("kvad", ["rune"], **{"internal/use.py": consumer})
    _git(project, "init", "-q")
    _git(project, "add", "-A")
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return project


def _breaks(project: TempProject, contract: str) -> list[str]:
    project.write("pfa/features/rune/contract/__init__.py", contract)
    return [d.message for d in analyze(project.root, EitriConfig(), base="HEAD") if d.id == "EIT008"]


def test_removing_a_field_a_consumer_uses_is_breaking(project: TempProject) -> None:
    hits = _breaks(_committed(project), CONTRACT.replace("    budget: int\n", ""))

    assert len(hits) == 1
    assert "`Split.budget` removed" in hits[0] and "pfa/features/kvad/internal/use.py" in hits[0]


def test_changing_a_signature_a_consumer_uses_is_breaking(project: TempProject) -> None:
    hits = _breaks(_committed(project), CONTRACT.replace("def count(text: str)", "def count(text: bytes)"))

    assert len(hits) == 1 and "`count` changed" in hits[0]


def test_adding_a_required_field_is_breaking(project: TempProject) -> None:
    hits = _breaks(_committed(project), CONTRACT.replace("    budget: int\n", "    budget: int\n    model: str\n"))

    assert len(hits) == 1 and "`Split.model` added without a default" in hits[0]


@pytest.mark.parametrize(
    "change",
    [
        ("    budget: int\n", "    budget: int\n    model: str = 'e5'\n"),
        ("def count(text: str)", "def count(text: str, strict: bool = False)"),
        ("\n\ndef count", "\n\nclass Joined:\n    parts: list[str]\n\n\ndef count"),
        ("    budget: int\n", "    budget: int = 512\n"),
    ],
    ids=["field-with-default", "trailing-defaulted-parameter", "new-class", "field-gains-default"],
)
def test_additions_are_free(project: TempProject, change: tuple[str, str]) -> None:
    assert _breaks(_committed(project), CONTRACT.replace(*change)) == []


def test_removing_a_member_nothing_uses_any_more_completes_the_migration(project: TempProject) -> None:
    hits = _breaks(_committed(project, consumer="from pfa.features.rune.contract import Split\n"), CONTRACT.split("\n\ndef count")[0])

    assert hits == []


def test_the_feature_s_own_routes_are_not_a_consumer_but_the_application_is(project: TempProject) -> None:
    _committed(project, consumer="")
    project.app(**{"api/routes/rune.py": CONSUMER, "application.py": "from pfa.features.rune.contract import count\n"})
    _git(project, "add", "-A")
    _git(project, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "edge")

    hits = _breaks(project, CONTRACT.replace("def count(text: str)", "def count(text: bytes)"))

    assert len(hits) == 1 and "pfa/application.py" in hits[0] and "routes/rune.py" not in hits[0]


def test_deleting_a_contract_module_removes_what_it_held(project: TempProject) -> None:
    _committed(project)
    (project.root / "pfa/features/rune/contract/__init__.py").unlink()

    hits = [d.message for d in analyze(project.root, EitriConfig(), base="HEAD") if d.id == "EIT008"]

    assert {h.split(":")[1].split(",")[0].strip() for h in hits} == {"`Split` removed with its module", "`count` removed with its module"}


def test_without_a_repository_the_check_cannot_run(project: TempProject) -> None:
    project.slice("rune", **{"contract/__init__.py": CONTRACT})

    with pytest.raises(EitriError, match="git merge-base failed"):
        analyze(project.root, EitriConfig(), base="HEAD")


def test_a_base_that_git_would_read_as_an_option_is_refused(project: TempProject) -> None:
    project.slice("rune", **{"contract/__init__.py": CONTRACT})

    with pytest.raises(EitriError, match="not an option"):
        analyze(project.root, EitriConfig(), base="--output=/tmp/x")


# ---------------------------------------------------------------- through the CLI


def test_eitri_check_base_reports_a_breaking_change_and_fails(project: TempProject) -> None:
    _committed(project)
    project.write("pfa/features/rune/contract/__init__.py", CONTRACT.replace("    budget: int\n", ""))
    out, err = io.StringIO(), io.StringIO()

    code = run(["check", "--root", str(project.root), "--base", "HEAD", "--no-info"], out, err)

    assert code == 1 and "error EIT008" in out.getvalue() and "`Split.budget` removed" in out.getvalue()


def test_eitri_check_base_outside_a_repository_fails_with_git_s_reason(project: TempProject) -> None:
    project.slice("rune", **{"contract/__init__.py": CONTRACT})
    out, err = io.StringIO(), io.StringIO()

    code = run(["check", "--root", str(project.root), "--base", "HEAD"], out, err)

    assert code == 1 and err.getvalue().startswith("eitri: git merge-base failed")
