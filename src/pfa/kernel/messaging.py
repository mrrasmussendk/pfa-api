"""CQRS primitives: a message is a frozen dataclass naming one use case; a handler is the one
callable that serves it; the bus routes a message to its handler.

A slice's Contract is its messages and result types. Consumers (routes, other slices)
dispatch a message and get a ``Result`` back — they never see a handler or a service class.
``grep EmbedPassages`` therefore finds every caller of that use case.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .primitives import Result


class Message:
    """Base for all messages. Subclass as a frozen dataclass in a slice's ``contract/``."""


class Command(Message):
    """A message that changes state. Handled by exactly one handler."""


class Query(Message):
    """A message that reads state or computes an answer, without side effects."""


Handler = Callable[[Any], Result[Any]]


class Bus:
    """Routes each message type to its single handler. Registration is explicit (``module.py``),
    never scanned — the wiring stays greppable."""

    def __init__(self) -> None:
        self._handlers: dict[type[Message], Handler] = {}

    def register(self, message_type: type[Message], handler: Handler, *, replace: bool = False) -> None:
        if message_type in self._handlers and not replace:
            raise ValueError(f"{message_type.__name__} already has a handler — one use case, one handler")
        self._handlers[message_type] = handler

    def handles(self, message_type: type[Message]) -> bool:
        return message_type in self._handlers

    def dispatch(self, message: Message) -> Result[Any]:
        try:
            handler = self._handlers[type(message)]
        except KeyError:
            raise LookupError(f"no handler registered for {type(message).__name__}") from None
        return handler(message)
