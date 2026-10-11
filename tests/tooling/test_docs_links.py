"""Every relative link in the repository's Markdown resolves: to a file or folder that exists and, with a
``#fragment``, to a heading in it. The guides and docs are the agents' feedforward; a dead link is an
instruction that leads nowhere."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKIP = (".git", ".venv", "node_modules", ".heimdall", ".pytest_cache", "postman/skills")  # postman/skills is vendored
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$", re.M)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)


def _docs() -> list[Path]:
    return sorted(p for p in REPO.rglob("*.md") if not any(p.relative_to(REPO).as_posix().startswith(s) for s in SKIP))


def slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, each space a hyphen."""
    text = re.sub(r"`|\*\*|\[([^\]]*)\]\([^)]*\)", r"\1", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    return {slug(h) for h in _HEADING.findall(_FENCE.sub("", path.read_text(encoding="utf-8")))}


def broken_links(doc: Path) -> list[str]:
    text = _FENCE.sub("", doc.read_text(encoding="utf-8"))
    out = []
    for m in _LINK.finditer(text):
        target = m.group(1) or m.group(2)
        if re.match(r"^[a-z]+:", target):
            continue
        path_part, _, fragment = target.partition("#")
        dest = (doc.parent / path_part).resolve() if path_part else doc
        if not dest.exists():
            out.append(f"{target}: no such file")
        elif fragment and dest.suffix == ".md" and fragment not in _anchors(dest):
            out.append(f"{target}: no heading #{fragment}")
    return out


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.relative_to(REPO).as_posix())
def test_every_relative_link_resolves(doc: Path) -> None:
    assert broken_links(doc) == []


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("Eitri — the walls", "eitri--the-walls"),
        ("1 · Feedforward — what the agent reads", "1--feedforward--what-the-agent-reads"),
        ("CI/CD — the gate and the judgment", "cicd--the-gate-and-the-judgment"),
        ("Run `heimdall map`", "run-heimdall-map"),
    ],
)
def test_anchors_are_github_s(heading: str, anchor: str) -> None:
    assert slug(heading) == anchor


def test_a_missing_file_and_a_missing_heading_are_both_broken(tmp_path: Path) -> None:
    (tmp_path / "guide.md").write_text("# Guide\n\n## Steps\n", encoding="utf-8")
    doc = tmp_path / "README.md"
    doc.write_text(
        "[ok](guide.md#steps) [gone](nowhere.md) [bad](guide.md#missing) [web](https://x.dev/#a) [self](#top)\n\n# Top\n"
        "```\n[in a fence](ignored.md)\n```\n",
        encoding="utf-8",
    )

    assert broken_links(doc) == ["nowhere.md: no such file", "guide.md#missing: no heading #missing"]
