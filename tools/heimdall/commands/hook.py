"""``heimdall hook`` — the PostToolUse sensor runner.

stdin: one hook event JSON. Runs every registered sensor, appends findings to
``.heimdall/telemetry.jsonl``, and speaks feedback to the agent via stderr + exit 2.
Anything unparseable on stdin exits 0: a broken hook must never block the agent's tool call.
"""

from __future__ import annotations

import json
import time
from typing import TextIO

from ..model import Finding, HeimdallContext, HookEvent
from ..sensors import ALL
from ..session_store import FileSessionStore
from ..store import UnreadableMap, append_findings, heimdall_dir, load_map


def _observe(ev: HookEvent, ctx: HeimdallContext) -> tuple[list[Finding], list[str]]:
    """Every sensor over the event: the findings for the telemetry, and the feedback for the agent.
    A sensor that raises becomes a finding with an error, never a broken hook."""
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
    return findings, feedback


def run(stdin: TextIO, stderr: TextIO, root: str) -> int:
    try:
        raw = json.loads(stdin.read())
    except ValueError:
        return 0
    if not isinstance(raw, dict):
        return 0
    ev = HookEvent.from_dict(raw)

    folder = heimdall_dir(root)
    try:
        m = load_map(folder)
    except UnreadableMap as e:
        stderr.write(f"heimdall: unreadable .heimdall/map.json: {e}\n")
        return 1
    findings, feedback = _observe(ev, HeimdallContext(map=m, sessions=FileSessionStore(folder), root=root))

    if findings:
        append_findings(folder, findings)

    if feedback:
        stderr.write("Heimdall: " + " | ".join(feedback) + "\n")
        return 2  # exit 2 => Claude Code surfaces stderr to the agent
    return 0
