from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class Result(Generic[T]):
    """A domain outcome. Handlers return this instead of raising: the route decides the
    status code, the handler decides whether the request made sense."""

    ok: bool
    value: T | None
    error: str | None

    @staticmethod
    def success(v: T) -> Result[T]:
        return Result(True, v, None)

    @staticmethod
    def failure(e: str) -> Result[T]:
        return Result(False, None, e)
