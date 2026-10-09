"""``heimdall review`` — Jev (TypeSafe AI) as the judge of a diff, Heimdall as the deliberation.

Jev does not write review comments. It answers *typed* questions — yes/no (``noul``),
``choice``, ``score`` — with probabilities. This package is the System Two around that call,
one module per step:

- ``diff``       the ``git diff`` cut per file;
- ``state``      the bounded state Jev sees: the diff capped in size, the slice map, which
                 slices / contracts / tests the change touches, the task, and Eitri's verdict on
                 the changed files (``facts.wall_violations`` — the walls are decided by the
                 import graph, never by the model);
- ``craft``      the second pass: each edited application file read whole and judged on its
                 own, so clean code is scored on the file the change leaves behind and the
                 verdict names the file;
- ``questions``  the fixed, typed questions about correctness, clean code, tests, security,
                 scope and architecture fit — ``questions()`` about the diff, ``file_questions()``
                 about each file;
- ``transport``  the ``POST https://api.typesafe.ai/v1/systemone`` call, once for the diff and
                 once per file;
- ``policy``     thresholds in code, not in the model, producing ``approve`` · ``comment`` ·
                 ``request_changes`` · ``escalate``;
- ``report``     the table on stdout and the PR comment, which says what went wrong in words;
- ``command``    argument parsing, outputs, telemetry.

Exit codes: 0 approve/comment · 1 request_changes · 2 escalate · 3 could not review.
Stdlib only, like the rest of Heimdall; Eitri is imported for the walls when installed.
Jev's probabilities are a policy input, not proof: measure the thresholds against your own
merges before trusting them with the merge button.
"""

from __future__ import annotations

from . import craft, state
from .base import EXIT_ERROR, EXIT_ESCALATE, EXIT_OK, EXIT_REQUEST_CHANGES, OUTCOMES, ReviewError, Streams
from .command import USAGE, run
from .craft import CraftFile, craft_files, craft_state
from .diff import FileChange, git_diff, parse_unified_diff
from .policy import T, Verdict, decide
from .questions import file_questions, questions
from .report import render_markdown, render_table
from .state import build_state, function_shape, wall_findings
from .transport import API_KEY_ENV, API_URL, API_URL_ENV, DEFAULT_MODEL, Transport, http_transport

__all__ = [
    "API_KEY_ENV",
    "API_URL",
    "API_URL_ENV",
    "DEFAULT_MODEL",
    "EXIT_ERROR",
    "EXIT_ESCALATE",
    "EXIT_OK",
    "EXIT_REQUEST_CHANGES",
    "OUTCOMES",
    "USAGE",
    "CraftFile",
    "FileChange",
    "ReviewError",
    "Streams",
    "T",
    "Transport",
    "Verdict",
    "build_state",
    "craft",
    "craft_files",
    "craft_state",
    "decide",
    "file_questions",
    "function_shape",
    "git_diff",
    "http_transport",
    "parse_unified_diff",
    "questions",
    "render_markdown",
    "render_table",
    "run",
    "state",
    "wall_findings",
]
