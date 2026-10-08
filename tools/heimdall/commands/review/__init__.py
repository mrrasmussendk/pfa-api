"""``heimdall review`` — Jev (TypeSafe AI) as the judge of a diff, Heimdall as the deliberation.

Jev does not write review comments. It answers *typed* questions — yes/no (``noul``),
``choice``, ``score`` — with probabilities. This package is the System Two around that call,
one module per step:

- ``diff``       the ``git diff`` cut per file;
- ``state``      the bounded state Jev sees: the diff capped in size, the slice map, which
                 slices / contracts / tests the change touches, the task, and Eitri's verdict on
                 the changed files (``facts.wall_violations`` — the walls are decided by the
                 import graph, never by the model);
- ``questions``  the fixed, typed questions about correctness, clean code, tests, security,
                 scope and architecture fit;
- ``transport``  the one ``POST https://api.typesafe.ai/v1/systemone`` call;
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

from . import state
from .base import EXIT_ERROR, EXIT_ESCALATE, EXIT_OK, EXIT_REQUEST_CHANGES, OUTCOMES, ReviewError, Streams
from .command import USAGE, run
from .diff import FileChange, git_diff, parse_unified_diff
from .policy import T, Verdict, decide
from .questions import questions
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
    "FileChange",
    "ReviewError",
    "Streams",
    "T",
    "Transport",
    "Verdict",
    "build_state",
    "decide",
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
