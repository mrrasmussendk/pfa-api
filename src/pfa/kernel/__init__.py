"""The kernel: the only package every feature may import. Keep it small — it is in
every agent's working set and importable from every contract."""

from .messaging import Bus, Command, Handler, Message, Query
from .primitives import Result

__all__ = ["Bus", "Command", "Handler", "Message", "Query", "Result"]
