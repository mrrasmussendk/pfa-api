"""Request validation shared by every route in ``pfa.api.routes``.

Two kinds of check live at the HTTP edge, and only these:

- **shape and size** — unknown fields, lengths, counts, a total-work budget per request;
- **encoding hygiene** — control characters and NUL bytes that no text model should see.

Whether the *content* makes sense (a blank question, an empty passage) is the handler's
call and comes back as ``Result.failure`` → a ``domain-rejection`` problem. Keeping the two
apart means a validation failure always says *what field is wrong*, and a domain rejection
always says *why the request made no sense*.

Every failure here is a pydantic ``ValueError`` and therefore a ``validation-error`` problem
document with the field's location in ``errors``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

# C0 controls except TAB, LF, CR; DEL; and the C1 range. NUL is the one that bites in practice
# (tokenizers and databases both choke on it); the rest are never meaningful in prose.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


class StrictRequest(BaseModel):
    """Base for every request body: an unknown field is a 422, not a silent no-op. A client
    that sends ``questions`` instead of ``question`` learns about it on the first call."""

    model_config = ConfigDict(extra="forbid", str_max_length=1_000_000)


def reject_control_chars(value: str) -> str:
    m = _CONTROL_CHARS.search(value)
    if m:
        raise ValueError(f"contains a control character (U+{ord(m.group()):04X}) at position {m.start()}")
    return value


def text(max_length: int, *, min_length: int = 1) -> Any:
    """A text field: length-bounded, no control characters. Whitespace is *not* stripped
    here — whether whitespace-only text is acceptable is the domain's decision."""
    return Annotated[str, StringConstraints(min_length=min_length, max_length=max_length), AfterValidator(reject_control_chars)]


def check_total_chars(texts: Iterable[str], limit: int, *, what: str = "texts") -> None:
    """Bound the work one request can ask for: the sum of all text lengths, not just each one.
    256 passages of 200 000 characters is a valid shape and an hour of CPU."""
    total = sum(len(t) for t in texts)
    if total > limit:
        raise ValueError(f"{what} total {total:,} characters, limit is {limit:,}")
