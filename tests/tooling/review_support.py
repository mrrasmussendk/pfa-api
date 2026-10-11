"""What the review tests share: a diff across the architecture's areas, the slice map it is classified
against, a change that touches given lines, a fake Jev, and a way to run the command."""

from __future__ import annotations

import io
from typing import Any

from conftest import TempRepo

from heimdall.commands import review
from heimdall.commands.review import FileChange
from heimdall.model import MapModel

ROUTE_SOURCE = "from fastapi import APIRouter\nfrom pfa.api.problems import Problem\nrouter = APIRouter(prefix='/kvad')\n"
KVAD_ENGINE = "src/pfa/features/kvad/internal/kvad_engine.py"


def noul(p: float) -> dict[str, Any]:
    return {"type": "noul", "noul": p}


def file_answers(score: float = 2.9, limit: dict[str, Any] | None = None) -> dict[str, Any]:
    """One file's clean-code answers, as the craft pass gets them: exemplary unless told otherwise."""
    return {
        "clean_code": {
            "type": "score",
            "score": score,
            "legend": {"0": "Hard to follow", "1": "Acceptable", "2": "Clean", "3": "Exemplary"},
            "probabilities": {"0": 0.0, "1": 0.0, "2": 0.1, "3": 0.9},
            "confidence": 0.8,
        },
        "clean_code_limit": limit
        or {"type": "choice", "choice": "none", "probabilities": {"none": 0.9, "comments": 0.1}, "confidence": 0.8},
    }


def jev(answers: dict[str, Any] | None = None, seen: list[dict[str, Any]] | None = None, files: dict[str, dict[str, Any]] | None = None):
    """A fake Jev: ``answers`` for the checks (unanswered ones pass), ``files[path]`` (or exemplary) per file judged whole."""

    def call(payload: dict[str, Any]) -> dict[str, Any]:
        if seen is not None:
            seen.append(payload)
        state = payload["state"]
        reply = (files or {}).get(state["file"]["path"], file_answers()) if "file" in state else (answers or {})
        return {"model": "jev-1.13.0", "answers": reply, "usage": {"input_tokens": 1234, "output_tokens": 15}}

    return call


def run(repo: TempRepo, *args: str, transport=None, env: dict[str, str] | None = None, stdin: str = "", check=None) -> tuple[int, str, str]:
    """``heimdall review`` in ``repo`` with Jev (and, under ``--claude``, the verifier) replaced."""
    out, err = io.StringIO(), io.StringIO()
    streams = review.Streams(io.StringIO(stdin), out, err)
    code = review.run(list(args), streams, str(repo.root), review.Seams(transport, env if env is not None else {}, check))
    return code, out.getvalue(), err.getvalue()


DIFF = """\
diff --git a/src/pfa/api/routes/kvad.py b/src/pfa/api/routes/kvad.py
index 1111111..2222222 100644
--- a/src/pfa/api/routes/kvad.py
+++ b/src/pfa/api/routes/kvad.py
@@ -1,4 +1,5 @@
 from fastapi import APIRouter
+from pfa.api.problems import Problem
 router = APIRouter(prefix="/kvad")
-    raise HTTPException(422)
+    raise Problem.domain_rejection(result.error)
diff --git a/src/pfa/features/rune/contract/rune_service.py b/src/pfa/features/rune/contract/rune_service.py
index 1111111..2222222 100644
--- a/src/pfa/features/rune/contract/rune_service.py
+++ b/src/pfa/features/rune/contract/rune_service.py
@@ -1,2 +1,3 @@
 class RuneService:
+    def new_member(self): ...
     pass
diff --git a/tests/api/test_kvad.py b/tests/api/test_kvad.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/api/test_kvad.py
@@ -0,0 +1,2 @@
+def test_it():
+    assert True
diff --git a/harness/guides/x.md b/harness/guides/x.md
index 1111111..2222222 100644
--- a/harness/guides/x.md
+++ b/harness/guides/x.md
@@ -1 +1 @@
-old
+new
diff --git a/poetry.lock b/poetry.lock
index 1111111..2222222 100644
--- a/poetry.lock
+++ b/poetry.lock
@@ -1 +1 @@
-a
+b
"""


def sample_map() -> MapModel:
    return MapModel.from_dict(
        {
            "kernel": "kernel",
            "kernel_dir": "src/pfa/kernel",
            "slices_dir": "src/pfa/features",
            "app_dir": "src/pfa",
            "routes_dir": "src/pfa/api/routes",
            "shared": ["http_common"],
            "slices": {
                "kvad": {"path": "src/pfa/features/kvad", "depends_on": ["rune"], "budget": 15000, "fan_in": 0},
                "rune": {"path": "src/pfa/features/rune", "depends_on": [], "budget": 15000, "fan_in": 12},
            },
        }
    )


def touching(path: str, *lines: int, status: str = "modified") -> FileChange:
    """A change to ``path`` that adds the given line numbers of the new file."""
    hunks = [row for n in lines for row in (f"@@ -{n},0 +{n},1 @@", "+changed")]
    return FileChange(path=path, status=status, added=len(lines), hunks=hunks)
