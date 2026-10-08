"""``heimdall drift`` — actual agent behavior (telemetry) vs the architecture's promise (map).

Per-session table first (wandering is a session property; aggregates dilute it), then the
per-slice aggregate.
"""

from __future__ import annotations

import json
import os
from typing import TextIO

from ..model import MapModel


class _Session:
    __slots__ = ("edits", "read_kinds")

    def __init__(self) -> None:
        self.edits: set[str] = set()
        self.read_kinds: list[str] = []


def _pct(oob: int, total: int) -> str:
    return f"{100.0 * oob / total:.0f}"


def run(stdout: TextIO, stderr: TextIO, root: str) -> int:
    tele_p = os.path.join(root, ".heimdall", "telemetry.jsonl")
    map_p = os.path.join(root, ".heimdall", "map.json")
    if not (os.path.isfile(tele_p) and os.path.isfile(map_p)):
        stderr.write("need telemetry + map\n")
        return 1
    m = MapModel.load(map_p)

    sessions: dict[str, _Session] = {}  # insertion-ordered
    with open(tele_p, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            sid = rec.get("session") or "?"
            s = sessions.setdefault(sid, _Session())
            if rec.get("event") == "edit" and rec.get("slice") is not None:
                s.edits.add(str(rec["slice"]))
            elif rec.get("event") == "read":
                s.read_kinds.append(str(rec.get("kind") or ""))

    def allowed_for(s: _Session) -> set[str]:
        allowed = set(s.edits)
        for e in s.edits:
            info = m.slices.get(e)
            if info is not None:
                allowed.update(info.depends_on)
        return allowed

    def in_bounds(kind: str, s: _Session, allowed: set[str]) -> bool:
        return (
            kind in ("kernel", "app")
            or kind.startswith("shared:")
            or (kind.startswith("slice:") and kind[len("slice:") :] in s.edits)
            or (kind.startswith("contract:") and kind[len("contract:") :] in allowed)
        )

    # per-slice aggregate, Counter-style insertion order
    total_by_slice: dict[str, int] = {}
    oob_by_slice: dict[str, int] = {}
    for s in sessions.values():
        allowed = allowed_for(s)
        target = sorted(s.edits)[0] if s.edits else "?"
        for kind in s.read_kinds:
            total_by_slice[target] = total_by_slice.get(target, 0) + 1
            oob_by_slice.setdefault(target, 0)
            if not in_bounds(kind, s, allowed):
                oob_by_slice[target] += 1

    # per-session first (wandering is a session property; aggregates dilute it)
    stdout.write("session".ljust(10) + "slices edited".ljust(26) + "reads".rjust(7) + "oob".rjust(6) + "oob %".rjust(8) + "\n")
    for sid in sorted(sessions):
        s = sessions[sid]
        allowed = allowed_for(s)
        t = len(s.read_kinds)
        if t == 0:
            continue
        o = sum(1 for k in s.read_kinds if not in_bounds(k, s, allowed))
        edited = ",".join(sorted(s.edits)) if s.edits else "-"
        stdout.write(sid.ljust(10) + edited.ljust(26) + str(t).rjust(7) + str(o).rjust(6) + _pct(o, t).rjust(7) + "%\n")
    stdout.write("\n")
    stdout.write("slice".ljust(22) + "reads".rjust(7) + "out-of-bounds".rjust(15) + "oob %".rjust(8) + "\n")
    for sl in sorted(total_by_slice, key=lambda x: -oob_by_slice[x]):  # stable: ties keep first-seen order
        stdout.write(
            sl.ljust(22)
            + str(total_by_slice[sl]).rjust(7)
            + str(oob_by_slice[sl]).rjust(15)
            + _pct(oob_by_slice[sl], total_by_slice[sl]).rjust(7)
            + "%\n"
        )
    stdout.write("\n")
    stdout.write("rule of thumb: sustained oob% > 20 on a slice = re-cut that seam\n")
    return 0
