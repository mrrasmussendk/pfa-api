"""Infrastructure failures a consumer of this slice may see. Not domain outcomes (those come
back as ``Result.failure``): the request was fine, the service could not serve it right now.
Handlers let these propagate; the HTTP edge decides what they mean on the wire."""

from __future__ import annotations


class EngineBusy(Exception):
    """The inference gate did not open in time. ``retry_after`` is a hint in seconds."""

    def __init__(self, waited: float, retry_after: int = 5) -> None:
        self.retry_after = retry_after
        super().__init__(f"embedding model is busy (waited {waited:.0f}s); retry in {retry_after}s")
