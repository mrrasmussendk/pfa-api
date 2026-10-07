"""``heimdall hook`` — the PostToolUse sensor runner.

stdin: one hook event JSON. Runs every registered sensor, appends findings to
``.heimdall/telemetry.jsonl``, and speaks feedback to the agent via stderr + exit 2.
Anything unparseable on stdin exits 0: a broken hook must never block the agent's tool call.
"""

from __future__ import annotations

import json
import os
import time
from typing import TextIO

from ..model import Finding, HeimdallContext, HookEvent, MapModel
from ..sensors import ALL
from ..session_store import FileSessionStore


def run(stdin: TextIO, stderr: TextIO, root: str) -> int:
    try:
        raw = json.loads(stdin.read())
    except ValueError:
        return 0
    if not isinstance(raw, dict):
        return 0
    ev = HookEvent.from_dict(raw)

    heimdall_dir = os.path.join(root, ".heimdall")
    map_path = os.path.join(heimdall_dir, "map.json")
    m: MapModel | None = None
    if os.path.isfile(map_path):
        try:
            m = MapModel.load(map_path)
        except (OSError, ValueError, TypeError) as e:
            stderr.write(f"heimdall: unreadable .heimdall/map.json: {e}\n")
            return 1

    ctx = HeimdallContext(map=m, sessions=FileSessionStore(heimdall_dir))
    findings: list[Finding] = []
    feedback: list[str] = []
    for sensor in ALL:
        try:
            for f in sensor.observe(ev, ctx):
                if f.sensor is None:
                    f.sensor = sensor.name
                f.ts = time.time()
                f.session = ev.session_or_q
                findings.append(f)
                if f.feedback:
                    feedback.append(f.feedback)
        except Exception as e:
            findings.append(Finding(sensor="heimdall", error=str(e), ts=time.time()))

    if findings:
        os.makedirs(heimdall_dir, exist_ok=True)
        with open(os.path.join(heimdall_dir, "telemetry.jsonl"), "a", encoding="utf-8", newline="\n") as out:
            for f in findings:
                out.write(f.to_line() + "\n")

    if feedback:
        stderr.write("Heimdall: " + " | ".join(feedback) + "\n")
        return 2  # exit 2 => Claude Code surfaces stderr to the agent
    return 0
