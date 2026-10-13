"""``heimdall drift`` — actual agent behavior (telemetry) vs the architecture's promise (map).

Per-session table first (wandering is a session property; aggregates dilute it), then the
per-slice aggregate. ``heimdall drift --session <id>`` is one session's line instead, naming the
reads outside its working set: what the Stop hook tells the agent before it finishes.
"""

from __future__ import annotations

import json
import os
from typing import Any, TextIO

from ..model import MapModel

OOB_RULE = 20  # percent: sustained out-of-bounds reading above it on a slice means the seam is cut wrong
_LISTED = 8  # out-of-bounds paths named in a session summary


class _Session:
    __slots__ = ("edits", "reads")

    def __init__(self) -> None:
        self.edits: set[str] = set()
        self.reads: list[tuple[str, str]] = []  # (kind, path)

    @property
    def read_kinds(self) -> list[str]:
        return [kind for kind, _ in self.reads]


def _pct(oob: int, total: int) -> str:
    return f"{100.0 * oob / total:.0f}"


def _record(line: str) -> dict[str, Any] | None:
    """One telemetry line as a record, or None when it is not one (a line cut short by a killed hook, say)."""
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _load(tele_p: str) -> tuple[dict[str, _Session], int]:
    """Each session's edited slices and reads, in the order sessions first appear, and how many lines were not
    records."""
    sessions: dict[str, _Session] = {}
    unreadable = 0
    with open(tele_p, encoding="utf-8") as f:
        for line in f:
            rec = _record(line)
            if rec is None:
                unreadable += 1
                continue
            s = sessions.setdefault(str(rec.get("session") or "?"), _Session())
            if rec.get("event") == "edit" and rec.get("slice") is not None:
                s.edits.add(str(rec["slice"]))
            elif rec.get("event") == "read":
                s.reads.append((str(rec.get("kind") or ""), str(rec.get("path") or "")))
    return sessions, unreadable


def _allowed_for(s: _Session, m: MapModel) -> set[str]:
    """The slices whose contracts the session may read: the ones it edited, and what those depend on."""
    allowed = set(s.edits)
    for e in s.edits:
        info = m.slices.get(e)
        if info is not None:
            allowed.update(info.depends_on)
    return allowed


def _in_bounds(kind: str, s: _Session, allowed: set[str]) -> bool:
    return (
        kind in ("kernel", "app")
        or kind.startswith("shared:")
        or (kind.startswith("slice:") and kind[len("slice:") :] in s.edits)
        or (kind.startswith("contract:") and kind[len("contract:") :] in allowed)
    )


def _inputs(root: str, stderr: TextIO) -> tuple[dict[str, _Session], MapModel] | None:
    tele_p = os.path.join(root, ".heimdall", "telemetry.jsonl")
    map_p = os.path.join(root, ".heimdall", "map.json")
    if not (os.path.isfile(tele_p) and os.path.isfile(map_p)):
        stderr.write("need telemetry + map\n")
        return None
    sessions, unreadable = _load(tele_p)
    if unreadable:
        stderr.write(f"heimdall drift: left out {unreadable} telemetry line(s) that are not records\n")
    return sessions, MapModel.load(map_p)


def session_summary(s: _Session, m: MapModel) -> str:
    """One line for the agent: its reads, how many fell outside its working set, and which."""
    allowed = _allowed_for(s, m)
    oob = sorted({path for kind, path in s.reads if not _in_bounds(kind, s, allowed)})
    total = len(s.reads)
    count = sum(1 for kind, _ in s.reads if not _in_bounds(kind, s, allowed))
    if total == 0:
        return "heimdall drift: no reads recorded this session"
    line = f"heimdall drift: {count} of {total} reads this session ({_pct(count, total)}%) fell outside the working set"
    if not oob:
        return line
    named = ", ".join(oob[:_LISTED]) + (f" and {len(oob) - _LISTED} more" if len(oob) > _LISTED else "")
    hint = " — if this feature keeps needing them, its seam or a contract is missing something" if 100 * count > OOB_RULE * total else ""
    return f"{line}: {named}{hint}"


def _session_table(sessions: dict[str, _Session], m: MapModel, stdout: TextIO) -> None:
    stdout.write("session".ljust(10) + "slices edited".ljust(26) + "reads".rjust(7) + "oob".rjust(6) + "oob %".rjust(8) + "\n")
    for sid in sorted(sessions):
        s = sessions[sid]
        allowed = _allowed_for(s, m)
        t = len(s.reads)
        if t == 0:
            continue
        o = sum(1 for k in s.read_kinds if not _in_bounds(k, s, allowed))
        edited = ",".join(sorted(s.edits)) if s.edits else "-"
        stdout.write(sid.ljust(10) + edited.ljust(26) + str(t).rjust(7) + str(o).rjust(6) + _pct(o, t).rjust(7) + "%\n")
    stdout.write("\n")


def _slice_counts(sessions: dict[str, _Session], m: MapModel) -> tuple[dict[str, int], dict[str, int]]:
    """Reads and out-of-bounds reads per slice, each session's reads counted against the first slice it edited."""
    total_by_slice: dict[str, int] = {}
    oob_by_slice: dict[str, int] = {}
    for s in sessions.values():
        allowed = _allowed_for(s, m)
        target = sorted(s.edits)[0] if s.edits else "?"
        for kind in s.read_kinds:
            total_by_slice[target] = total_by_slice.get(target, 0) + 1
            oob_by_slice.setdefault(target, 0)
            if not _in_bounds(kind, s, allowed):
                oob_by_slice[target] += 1
    return total_by_slice, oob_by_slice


def _slice_table(sessions: dict[str, _Session], m: MapModel, stdout: TextIO) -> None:
    total_by_slice, oob_by_slice = _slice_counts(sessions, m)
    stdout.write("slice".ljust(22) + "reads".rjust(7) + "out-of-bounds".rjust(15) + "oob %".rjust(8) + "\n")
    for sl in sorted(total_by_slice, key=lambda x: -oob_by_slice[x]):  # stable: ties keep first-seen order
        oob, total = oob_by_slice[sl], total_by_slice[sl]
        stdout.write(sl.ljust(22) + str(total).rjust(7) + str(oob).rjust(15) + _pct(oob, total).rjust(7) + "%\n")
    stdout.write("\n")
    stdout.write(f"rule of thumb: sustained oob% > {OOB_RULE} on a slice = re-cut that seam\n")


def run_report(stdout: TextIO, stderr: TextIO, root: str) -> int:
    """Every session, then every slice."""
    inputs = _inputs(root, stderr)
    if inputs is None:
        return 1
    sessions, m = inputs
    _session_table(sessions, m, stdout)
    _slice_table(sessions, m, stdout)
    return 0


def run_session(stdout: TextIO, stderr: TextIO, root: str, session: str) -> int:
    """One session's line."""
    inputs = _inputs(root, stderr)
    if inputs is None:
        return 1
    sessions, m = inputs
    stdout.write(session_summary(sessions.get(session, _Session()), m) + "\n")
    return 0
