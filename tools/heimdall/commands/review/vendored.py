"""Files the review leaves out: what ``.gitattributes`` marks ``linguist-vendored`` or ``linguist-generated``.
Someone else wrote them, so a judgment of them changes nothing in the PR; GitHub collapses the same files in
a pull request's diff. Read here: one pattern per line with the attribute set; a later line that unsets it
is not honoured."""

from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path

from ...pathutil import norm

_ATTRIBUTES = ("linguist-vendored", "linguist-generated")


def vendored_patterns(cwd: str) -> list[str]:
    """The ``.gitattributes`` patterns that mark a file vendored or generated."""
    try:
        lines = Path(cwd, ".gitattributes").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    patterns = []
    for line in lines:
        pattern, *attrs = line.split() or ["#"]
        if not pattern.startswith("#") and any(a in _ATTRIBUTES or a in (f"{n}=true" for n in _ATTRIBUTES) for a in attrs):
            patterns.append(pattern)
    return patterns


def is_vendored(path: str, patterns: list[str]) -> bool:
    """Whether ``path`` matches any of the vendored ``patterns``."""
    p = norm(path)
    return any(_matches(p, pattern) for pattern in patterns)


def _matches(path: str, pattern: str) -> bool:
    """Git's matching, for the shapes a vendored marker takes: ``dir/**`` or ``dir/`` under the root, ``**/dir/**``
    at any depth, the root included, a path, or a bare name matched anywhere."""
    if pattern.startswith("**/"):
        return _matches(path, pattern[3:]) or fnmatchcase(path, pattern)
    anchored = pattern.lstrip("/")
    if anchored.endswith("/"):
        return path.startswith(anchored)
    if "/" in anchored:
        return fnmatchcase(path, anchored)
    return fnmatchcase(path.rsplit("/", 1)[-1], anchored)
