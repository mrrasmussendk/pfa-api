"""Feedback sensor: warns the agent in-loop when it edits a second slice in one session
(the classic scope-creep smell) or edits a frozen high fan-in contract.

The "which slices has this session already edited" lookup is served by the session store
(a tiny per-session state file) — O(1) per event, not a rescan of the telemetry log.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..model import EDIT_TOOLS, Finding, HeimdallContext, HookEvent
from ..pathutil import is_contract_path, slice_of

FAN_IN_FREEZE = 10


class BoundaryEditsSensor:
    name = "boundary_edits"

    def observe(self, e: HookEvent, ctx: HeimdallContext) -> Iterable[Finding]:
        if e.tool_name not in EDIT_TOOLS:
            return []
        m = ctx.map
        if m is None:
            return []
        path = e.edit_path
        s = slice_of(path, m)
        if s is None:
            return []

        finding = Finding(event="edit", path=path, slice=s)

        prior = ctx.sessions.get_edited_slices(e.session_or_q)
        if prior and s not in prior:
            finding.feedback = (
                f"you are now editing slice '{s}' after editing {sorted(prior)!r} — "
                "cross-slice changes should go through contracts; confirm this is intentional"
            )

        info = m.slices.get(s)
        if is_contract_path(path) and info is not None and info.fan_in >= FAN_IN_FREEZE:
            finding.feedback = (
                f"'{s}' Contract has fan-in {info.fan_in} — treat as frozen: "
                "additive changes only, breaking changes need an expand-contract fan-out"
            )

        ctx.sessions.add_edited_slice(e.session_or_q, s)
        return [finding]
