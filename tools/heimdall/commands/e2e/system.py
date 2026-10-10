"""The system under test: what the project says in ``SYSTEM`` — how to start it, when it is ready, and which tool
tests each method — and the running system for the length of a run. A project whose scenarios start what they
need (a CLI, a library) leaves ``start`` out, and the tools get no base URL."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import SYSTEM, E2EError
from .tools import TOOLS

SERVICE_LOG = ".heimdall/e2e-service.log"
STARTUP_SECONDS = 60.0


@dataclass(frozen=True)
class System:
    tools: dict[str, str]  # method ("API", "UI", "service", ...) -> the tool its scenarios are scaffolded with
    start: tuple[str, ...] = ()  # argv; "{python}" is this interpreter, "{port}" the free port picked for the run
    port_env: str = ""  # the variable the started system reads its port from
    ready: str = "/"  # the path that answers once the system is up

    @property
    def methods(self) -> list[str]:
        return list(self.tools)


def _strings(value: Any, what: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise E2EError(f"{SYSTEM}: `{what}` must be a list of strings")
    return tuple(value)


def _tools(data: dict[str, Any]) -> dict[str, str]:
    tools = data.get("tools")
    if not isinstance(tools, dict) or not tools or not all(isinstance(v, str) for v in tools.values()):
        raise E2EError(f'{SYSTEM}: `tools` must map each method to a tool, e.g. {{"API": "postman"}}')
    unknown = sorted(set(tools.values()) - set(TOOLS))
    if unknown:
        raise E2EError(f"{SYSTEM}: no runner for {unknown!r}; Heimdall runs {sorted(TOOLS)!r}")
    return dict(tools)


def _start(data: dict[str, Any]) -> tuple[str, ...]:
    """The start command, when the started system can learn the port the run picked for it."""
    start = _strings(data.get("start", []), "start")
    if start and not data.get("port_env") and not any("{port}" in a for a in start):
        raise E2EError(f"{SYSTEM}: `start` cannot learn the port the run picks: set `port_env`, or pass {{port}} in `start`")
    return start


def load_system(root: str) -> System:
    try:
        data = json.loads(Path(root, SYSTEM).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise E2EError(
            f"{SYSTEM} is missing or unreadable ({e}): it says how to start the system and which tool tests each method"
        ) from None
    if not isinstance(data, dict):
        raise E2EError(f"{SYSTEM}: not a JSON object")
    return System(_tools(data), _start(data), str(data.get("port_env", "")), str(data.get("ready", "/")))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, proc: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise E2EError(f"the system exited with {proc.returncode} before {url} answered; see {SERVICE_LOG}")
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    raise E2EError(f"{url} did not answer within {STARTUP_SECONDS:.0f}s; see {SERVICE_LOG}")


def _stop(proc: subprocess.Popen[bytes]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@contextmanager
def running(system: System, cwd: str) -> Iterator[str]:
    """The base URL of the started system, stopped afterwards; ``""`` when the project starts nothing."""
    if not system.start:
        yield ""
        return
    port = str(_free_port())
    argv = [a.replace("{python}", sys.executable).replace("{port}", port) for a in system.start]
    env = {**os.environ, **({system.port_env: port} if system.port_env else {})}
    os.makedirs(os.path.join(cwd, os.path.dirname(SERVICE_LOG)), exist_ok=True)
    with open(os.path.join(cwd, SERVICE_LOG), "wb") as log:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        except OSError as e:
            raise E2EError(f"cannot start {argv[0]}: {e}") from None
        try:
            base_url = f"http://127.0.0.1:{port}"
            _wait_ready(base_url + system.ready, proc)
            yield base_url
        finally:
            _stop(proc)
