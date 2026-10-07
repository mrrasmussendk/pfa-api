"""Shared HTTP-edge helpers: FastAPI/pydantic code that more than one slice's ``internal/``
needs. This package is *not* the kernel — it knows FastAPI — so contracts never import it
(EIT003) and it never appears in a slice's budget. Only ``internal/routes.py`` and the
composition root import it."""

from .problems import (
    DOMAIN_REJECTION,
    MODEL_BUSY,
    NOT_READY,
    PROBLEM_MEDIA_TYPE,
    VALIDATION_ERROR,
    Problem,
    ProblemDetails,
    ProblemResponse,
    install_problem_details,
)
from .validation import StrictRequest, check_total_chars, reject_control_chars, text

__all__ = [
    "DOMAIN_REJECTION",
    "MODEL_BUSY",
    "NOT_READY",
    "PROBLEM_MEDIA_TYPE",
    "VALIDATION_ERROR",
    "Problem",
    "ProblemDetails",
    "ProblemResponse",
    "StrictRequest",
    "check_total_chars",
    "install_problem_details",
    "reject_control_chars",
    "text",
]
