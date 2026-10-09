"""Eitri — the smith. Slice-boundary rules and the context-budget fitness function for Python."""

from .analyzer import EitriError, analyze
from .core import (
    ALL_DESCRIPTORS,
    EIT000,
    EIT001,
    EIT002,
    EIT003,
    EIT004,
    EIT005,
    EIT006,
    EIT100,
    EIT101,
    Descriptor,
    Diagnostic,
    EitriConfig,
    Severity,
)

__all__ = [
    "ALL_DESCRIPTORS",
    "EIT000",
    "EIT001",
    "EIT002",
    "EIT003",
    "EIT004",
    "EIT005",
    "EIT006",
    "EIT100",
    "EIT101",
    "Descriptor",
    "Diagnostic",
    "EitriConfig",
    "EitriError",
    "Severity",
    "analyze",
]
