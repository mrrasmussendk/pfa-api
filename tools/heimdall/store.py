"""The ``.heimdall/`` folder as one value: where the map is read from and the telemetry appended to.
``heimdall hook`` and ``heimdall review`` both do both; one spelling here keeps them from drifting apart."""

from __future__ import annotations

import os
from collections.abc import Iterable

from .model import Finding, MapModel

MAP_FILE = "map.json"
TELEMETRY_FILE = "telemetry.jsonl"


class UnreadableMap(Exception):
    """``map.json`` exists but is not a map: the caller says so, since silence would hide a broken harness."""


def heimdall_dir(root: str) -> str:
    return os.path.join(root, ".heimdall")


def load_map(heimdall_dir: str) -> MapModel | None:
    """The map when there is one; ``None`` when the harness is not set up."""
    map_path = os.path.join(heimdall_dir, MAP_FILE)
    if not os.path.isfile(map_path):
        return None
    try:
        return MapModel.load(map_path)
    except (OSError, ValueError, TypeError) as e:
        raise UnreadableMap(str(e)) from e


def append_findings(heimdall_dir: str, findings: Iterable[Finding]) -> None:
    """One telemetry line per finding, creating the folder on first use."""
    os.makedirs(heimdall_dir, exist_ok=True)
    with open(os.path.join(heimdall_dir, TELEMETRY_FILE), "a", encoding="utf-8", newline="\n") as out:
        for f in findings:
            out.write(f.to_line() + "\n")
