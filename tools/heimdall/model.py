"""The sensor model — port of ``Heimdall.Sensors/Model.cs``.

Kept stdlib-only and import-light on purpose: ``heimdall hook`` runs as a fresh process on
EVERY agent tool call, so cold start is the whole latency budget.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

EDIT_TOOLS = ("Edit", "Write", "MultiEdit")
READ_TOOLS = ("Read", "Grep", "Glob")


@dataclass
class HookEvent:
    """A Claude Code PostToolUse hook event (the subset Heimdall reads)."""

    session_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HookEvent:
        ti = d.get("tool_input")
        return cls(
            session_id=_opt_str(d.get("session_id")),
            tool_name=_opt_str(d.get("tool_name")),
            tool_input=ti if isinstance(ti, dict) else {},
        )

    @property
    def session_or_q(self) -> str:
        return self.session_id or "?"

    @property
    def read_path(self) -> str:
        """file_path, then path, then "" — boundary_reads' path resolution."""
        return _opt_str(self.tool_input.get("file_path")) or _opt_str(self.tool_input.get("path")) or ""

    @property
    def edit_path(self) -> str:
        """file_path only — boundary_edits has no ``path`` fallback."""
        return _opt_str(self.tool_input.get("file_path")) or ""


def _opt_str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


@dataclass
class SliceInfo:
    path: str = ""
    depends_on: list[str] = field(default_factory=list)
    budget: int = 0
    fan_in: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SliceInfo:
        deps = d.get("depends_on") or []
        return cls(
            path=str(d.get("path", "")),
            depends_on=[str(x) for x in deps] if isinstance(deps, list) else [],
            budget=int(d.get("budget", 0) or 0),
            fan_in=int(d.get("fan_in", 0) or 0),
        )


@dataclass
class MapModel:
    """The feedforward map (.heimdall/map.json): dependency graph + budgets + fan-in."""

    kernel: str = "shared_kernel"
    slices_dir: str = ""
    slices: dict[str, SliceInfo] = field(default_factory=dict)
    # Packages every slice may read besides the kernel (``[tool.heimdall] shared`` in pyproject):
    # HTTP-edge helpers such as http_common. Reads of them are in-bounds, never drift.
    shared: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MapModel:
        raw = d.get("slices") or {}
        slices = {str(k): SliceInfo.from_dict(v) for k, v in raw.items() if isinstance(v, dict)}
        shared = d.get("shared") or []
        return cls(
            kernel=str(d.get("kernel", "shared_kernel")),
            slices_dir=str(d.get("slices_dir", "")),
            slices=slices,
            shared=[str(x) for x in shared] if isinstance(shared, list) else [],
        )

    @classmethod
    def load(cls, path: str) -> MapModel:
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


@dataclass
class Finding:
    """A sensor finding. Field order is the emission order of the telemetry line:
    event, path, kind, slice, feedback, sensor, error, ts, session. Nones are omitted."""

    event: str | None = None
    path: str | None = None
    kind: str | None = None
    slice: str | None = None
    feedback: str | None = None
    sensor: str | None = None
    error: str | None = None
    ts: float = 0.0
    session: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key in ("event", "path", "kind", "slice", "feedback", "sensor", "error"):
            v = getattr(self, key)
            if v is not None:
                out[key] = v
        out["ts"] = float(self.ts)
        if self.session is not None:
            out["session"] = self.session
        return out

    def to_line(self) -> str:
        return json.dumps(self.to_dict())


class SessionStore(Protocol):
    """Per-session record of which slices an agent has already edited this session."""

    def get_edited_slices(self, session: str) -> Collection[str]: ...

    def add_edited_slice(self, session: str, slice: str) -> None: ...


@dataclass
class HeimdallContext:
    """What a sensor sees: the parsed map (or None) and per-session edit history."""

    map: MapModel | None
    sessions: SessionStore


class Sensor(Protocol):
    """A Heimdall sensor. Adding one = implement this and add a line to ``sensors.ALL``."""

    name: str

    def observe(self, e: HookEvent, ctx: HeimdallContext) -> Iterable[Finding]: ...
