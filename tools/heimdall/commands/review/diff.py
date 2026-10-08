"""The diff: ``git diff`` output cut into one ``FileChange`` per file, generated files dropped."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from ...pathutil import norm
from .base import ReviewError

IGNORED = re.compile(r"(^|/)(\.heimdall/|.*\.egg-info/|.*\.lock$|.*\.min\.(js|css)$|.*\.(png|jpg|gif|ico|woff2?|pdf|svg)$)")
_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/(.+)$")


@dataclass
class FileChange:
    path: str
    status: str  # added | deleted | modified | renamed
    added: int = 0
    removed: int = 0
    hunks: list[str] = field(default_factory=list)
    binary: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.hunks)


def parse_unified_diff(diff: str) -> list[FileChange]:
    """``git diff`` output -> one ``FileChange`` per file, hunks kept verbatim."""
    files: list[FileChange] = []
    current: FileChange | None = None
    for line in diff.splitlines():
        m = _DIFF_GIT.match(line)
        if m:
            current = FileChange(path=m.group(2), status="modified")
            if m.group(1) != m.group(2):
                current.status = "renamed"
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
        elif line.startswith("Binary files"):
            current.binary = True
        elif line.startswith(("+++ ", "--- ")):
            continue
        elif line.startswith(("@@", " ", "+", "-", "\\")):
            if line.startswith("@@"):
                current.hunks.append(line)
            else:
                if line.startswith("+"):
                    current.added += 1
                elif line.startswith("-"):
                    current.removed += 1
                current.hunks.append(line)
    return [f for f in files if not IGNORED.search(norm(f.path))]


def git_diff(cwd: str, base: str | None) -> str:
    spec = [f"{base}...HEAD"] if base else ["HEAD"]
    try:
        out = subprocess.run(
            ["git", "diff", "--no-color", "--unified=3", "--no-ext-diff", *spec],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as e:
        raise ReviewError(f"git not available: {e}") from e
    if out.returncode != 0:
        raise ReviewError(f"git diff failed: {out.stderr.strip()}")
    return out.stdout
