"""PFA API — the deployable service. Slices live in ``slices/``; this package only composes them."""

from .app import create_app

__all__ = ["create_app"]
