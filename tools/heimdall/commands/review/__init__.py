"""``heimdall review`` — Jev (TypeSafe AI) checks a diff for faults, Heimdall decides, and in the PR
pipeline Claude verifies what Jev flagged.

Jev does not write review comments. It answers *typed* questions — yes/no (``noul``),
``choice``, ``score`` — with probabilities, and its guidance is to ask narrow questions per item. So
every question is one fault check about one place. This package is the System Two around that call,
one module per step:

- ``diff``       the ``git diff`` cut per file;
- ``vendored``   what ``.gitattributes`` marks vendored or generated, left out of the review;
- ``items``      the changed functions, each read whole from the working tree, routes marked;
- ``state``      the bounded state Jev sees: the diff capped in size, the slice map, which
                 slices / contracts / tests the change touches, the task, and Eitri's verdict on
                 the changed files (``facts.wall_violations`` — the walls are decided by the
                 import graph, never by the model);
- ``craft``      the second pass: each edited application file read whole and judged on its
                 own, so clean code is scored on the file the change leaves behind and the
                 verdict names the file;
- ``questions``  the fault checks built per item — ``ask()`` per function, per route and about
                 the change; ``file_questions()`` about each file's clean code;
- ``transport``  the ``POST https://api.typesafe.ai/v1/systemone`` call, once for the diff and
                 once per file;
- ``policy``     each answer a row at its place; thresholds in code produce ``comment`` ·
                 ``request_changes`` · ``escalate``, never an approval: a person merges;
- ``verify``     the PR pipeline: Claude confirms or refutes each failing or unsure check, with
                 evidence found in the file (``claude`` makes the call);
- ``report``     the table on stdout, which the Stop hook hands the agent;
- ``comment``    the PR comment: what to fix first, each a place and a fix; the evidence folded below;
- ``command``    argument parsing, outputs, telemetry.

Exit codes: 0 comment · 1 request_changes · 2 escalate · 3 could not review.
Jev's call is stdlib only; Claude's uses the ``anthropic`` SDK. Eitri is imported for the walls when installed.
Jev's probabilities are a policy input, not proof: measure the thresholds against your own
merges before trusting them with the merge button.
"""

from __future__ import annotations

from . import craft, state
from .base import EXIT_ERROR, EXIT_ESCALATE, EXIT_OK, EXIT_REQUEST_CHANGES, OUTCOMES, ReviewError, Streams
from .command import USAGE, Seams, run
from .comment import render_comment
from .craft import CraftFile, craft_files, craft_state
from .diff import FileChange, git_diff, parse_unified_diff
from .policy import Judgment, Row, T, Verdict, decide, evaluate
from .questions import Asked, ask, file_questions
from .report import render_table
from .state import Known, build_state, function_shape, wall_findings
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
    "Asked",
    "CraftFile",
    "FileChange",
    "Judgment",
    "Known",
    "ReviewError",
    "Row",
    "Seams",
    "Streams",
    "T",
    "Transport",
    "Verdict",
    "ask",
    "build_state",
    "craft",
    "craft_files",
    "craft_state",
    "decide",
    "evaluate",
    "file_questions",
    "function_shape",
    "git_diff",
    "http_transport",
    "parse_unified_diff",
    "render_comment",
    "render_table",
    "run",
    "state",
    "wall_findings",
]
