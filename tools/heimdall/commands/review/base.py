"""Shared by every step: the exit codes, the outcomes, and the one error that means "no verdict"."""

from __future__ import annotations

EXIT_OK, EXIT_REQUEST_CHANGES, EXIT_ESCALATE, EXIT_ERROR = 0, 1, 2, 3
OUTCOMES = ("approve", "comment", "request_changes", "escalate")


class ReviewError(Exception):
    """Could not review (exit 3): the review is not an answer about the code."""
