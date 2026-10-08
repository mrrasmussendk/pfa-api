"""Classifies every file the agent reads: in-slice / contract / kernel / app / shared / out-of-bounds.
A slice's HTTP edge (``<routes>/<slice>.py``) is in-slice; the rest of the application package
(composition root, entry points) is ``app`` and always in-bounds.
Pure observation — logs, never interrupts. Drift analysis happens in ``heimdall drift``."""

from __future__ import annotations

from collections.abc import Iterable

from ..model import READ_TOOLS, Finding, HeimdallContext, HookEvent
from ..pathutil import app_of, is_contract_path, is_kernel_path, shared_of, slice_of

CODE_SUFFIXES = (".py", ".pyi", ".md")
MANIFEST = "feature.json"


class BoundaryReadsSensor:
    name = "boundary_reads"

    def observe(self, e: HookEvent, ctx: HeimdallContext) -> Iterable[Finding]:
        if e.tool_name not in READ_TOOLS:
            return []
        m = ctx.map
        if m is None:
            return []
        path = e.read_path
        if not (path.endswith(CODE_SUFFIXES) or path.replace("\\", "/").endswith("/" + MANIFEST) or path == MANIFEST):
            return []

        s = slice_of(path, m)
        shared = shared_of(path, m)
        if is_kernel_path(path, m):
            kind = "kernel"
        elif s is None and shared is not None:
            kind = "shared:" + shared
        elif s is not None:
            kind = "slice:" + s
        elif app_of(path, m) is not None:
            kind = "app"
        else:
            kind = "outside"
        if s is not None and is_contract_path(path):
            kind = "contract:" + s

        return [Finding(event="read", path=path, kind=kind)]
