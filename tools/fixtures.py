"""A synthetic two-slice project tree for the tooling's own tests, canaries and smoke test.

The tooling must never depend on the API's real slices (they will change); it is validated
against this fixed fixture, and the API is validated against the tooling in tests/api.
"""

from __future__ import annotations

import json
from pathlib import Path

_KERNEL = """from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class StaveId:
    value: str


@dataclass(frozen=True)
class RuneRef:
    row: str
    mark: str


@dataclass(frozen=True)
class Result(Generic[T]):
    ok: bool
    value: T | None
    error: str | None


class Registry:
    def __init__(self) -> None:
        self._map: dict[str, Callable[[], object]] = {}

    def register(self, key: str, factory: Callable[[], object]) -> None:
        self._map[key] = factory

    def resolve(self, key: str) -> object:
        return self._map[key]()
"""

_RUNE_CONTRACT = """from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

from shared_kernel import Result, RuneRef, StaveId


class Clarity(Enum):
    FAINT = 0
    CLEAR = 1


@dataclass(frozen=True)
class RuneReading:
    stave_id: StaveId
    clarity: Clarity
    score: float


class RuneService(Protocol):
    def read(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[RuneReading]: ...
"""

_RUNE_ENGINE = """from typing import Sequence

from shared_kernel import Result, RuneRef, StaveId
from slices.rune.contract import Clarity, RuneReading


class RuneEngine:
    def evaluate(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[RuneReading]:
        if not utterance.strip():
            return Result(False, None, "empty utterance")
        score = min(1.0, 0.1 * len(runes))
        return Result(True, RuneReading(stave_id, Clarity.CLEAR if score > 0.5 else Clarity.FAINT, score), None)
"""

_RUNE_SERVICE = """from typing import Sequence

from shared_kernel import Result, RuneRef, StaveId
from slices.rune.contract import RuneReading

from .rune_engine import RuneEngine


class RuneServiceImpl:
    def __init__(self) -> None:
        self._engine = RuneEngine()

    def read(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[RuneReading]:
        return self._engine.evaluate(stave_id, runes, utterance)
"""

_RUNE_MODULE = """from shared_kernel import Registry

from .rune_service import RuneServiceImpl


def register(r: Registry) -> None:
    r.register("rune", lambda: RuneServiceImpl())
"""

_KVAD_CONTRACT = """from dataclasses import dataclass
from typing import Protocol, Sequence

from shared_kernel import Result, RuneRef, StaveId


@dataclass(frozen=True)
class Verse:
    stave_id: StaveId
    score: float


class KvadService(Protocol):
    def compose(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[Verse]: ...
"""

_KVAD_ENGINE = """from typing import Sequence

from shared_kernel import Result, RuneRef, StaveId
from slices.kvad.contract import Verse


class KvadEngine:
    def evaluate(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[Verse]:
        if not utterance.strip():
            return Result(False, None, "empty utterance")
        return Result(True, Verse(stave_id, min(1.0, 0.2 * len(runes))), None)
"""

_KVAD_SERVICE = """from typing import Sequence

from shared_kernel import Result, RuneRef, StaveId
from slices.kvad.contract import Verse
from slices.rune.contract import RuneService

from .kvad_engine import KvadEngine


class KvadServiceImpl:
    def __init__(self, rune: RuneService | None = None) -> None:
        self._engine = KvadEngine()
        self._rune = rune

    def compose(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[Verse]:
        return self._engine.evaluate(stave_id, runes, utterance)
"""

_KVAD_MODULE = """from shared_kernel import Registry
from slices.rune.contract import RuneService

from .kvad_service import KvadServiceImpl


def register(r: Registry) -> None:
    def factory() -> object:
        rune = r.resolve("rune")
        assert isinstance(rune, RuneService)
        return KvadServiceImpl(rune)

    r.register("kvad", factory)
"""

_PYPROJECT = """[tool.eitri]
slice_prefix = "slices."
kernel = "shared_kernel"
token_budget = 15000
"""

FILES: dict[str, str] = {
    "pyproject.toml": _PYPROJECT,
    "shared_kernel/__init__.py": _KERNEL,
    "slices/__init__.py": "",
    "slices/rune/slice.json": json.dumps({"depends_on": []}, indent=2) + "\n",
    "slices/rune/AGENTS.md": "# rune\n<!--heimdall:deps-->depends on: (none) + shared_kernel<!--/heimdall:deps-->\n<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n",
    "slices/rune/__init__.py": "",
    "slices/rune/contract/__init__.py": "from .rune_service import Clarity, RuneReading, RuneService\n",
    "slices/rune/contract/rune_service.py": _RUNE_CONTRACT,
    "slices/rune/internal/__init__.py": "",
    "slices/rune/internal/rune_engine.py": _RUNE_ENGINE,
    "slices/rune/internal/rune_service.py": _RUNE_SERVICE,
    "slices/rune/internal/module.py": _RUNE_MODULE,
    "slices/kvad/slice.json": json.dumps({"depends_on": ["rune"]}, indent=2) + "\n",
    "slices/kvad/AGENTS.md": "# kvad\n<!--heimdall:deps-->depends on: rune + shared_kernel<!--/heimdall:deps-->\n<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n",
    "slices/kvad/__init__.py": "",
    "slices/kvad/contract/__init__.py": "from .kvad_service import KvadService, Verse\n",
    "slices/kvad/contract/kvad_service.py": _KVAD_CONTRACT,
    "slices/kvad/internal/__init__.py": "",
    "slices/kvad/internal/kvad_engine.py": _KVAD_ENGINE,
    "slices/kvad/internal/kvad_service.py": _KVAD_SERVICE,
    "slices/kvad/internal/module.py": _KVAD_MODULE,
}


def write_fixture_tree(root: Path) -> Path:
    """Write the fixture project under ``root`` (created if needed) and return it."""
    for rel, content in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    return root
