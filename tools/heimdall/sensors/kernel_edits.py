"""Feedback sensor: the first time a session edits the kernel, tell the agent what the kernel costs. Every
feature's EIT100 working set includes the kernel's public surface, so a name added there is paid by all of
them. Once per session, so the agent hears it before its first kernel change and is not interrupted after."""

from __future__ import annotations

from collections.abc import Iterable

from ..model import EDIT_TOOLS, Finding, HeimdallContext, HookEvent
from ..pathutil import is_kernel_path

_TOLD = "kernel"  # remembered in the session's own kernel record, apart from the slices it edited


class KernelEditsSensor:
    name = "kernel_edits"

    def observe(self, e: HookEvent, ctx: HeimdallContext) -> Iterable[Finding]:
        m = ctx.map
        if e.tool_name not in EDIT_TOOLS or m is None or not is_kernel_path(e.edit_path, m):
            return []
        finding = Finding(event="edit", path=e.edit_path, kind="kernel")
        record = f"{e.session_or_q}-kernel"
        if _TOLD not in ctx.sessions.get_edited_slices(record):
            features = len(m.slices)
            finding.feedback = (
                f"you are editing the kernel: all {features} features load its public surface into their token budget "
                "(EIT100), so each public name you add costs every one of them. Keep it to primitives every feature "
                f"needs; anything one feature needs belongs in that feature. See {m.kernel_dir or m.kernel}/AGENTS.md"
            )
            ctx.sessions.add_edited_slice(record, _TOLD)
        return [finding]
