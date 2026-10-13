"""A synthetic application tree for the tooling's own tests, canaries and smoke test: two
features (``rune``, ``kvad`` depends on ``rune``), a kernel, a composition root and a tiny
HTTP edge, laid out the way the real service is (``pfa/features``, ``pfa/kernel``,
``pfa/application.py``, ``pfa/api/routes``).

The tooling must never depend on the API's real features (they will change); it is validated
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

from pfa.kernel import Result, RuneRef, StaveId


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

from pfa.features.rune.contract import Clarity, RuneReading
from pfa.kernel import Result, RuneRef, StaveId


class RuneEngine:
    def evaluate(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[RuneReading]:
        if not utterance.strip():
            return Result(False, None, "empty utterance")
        score = min(1.0, 0.1 * len(runes))
        return Result(True, RuneReading(stave_id, Clarity.CLEAR if score > 0.5 else Clarity.FAINT, score), None)
"""

_RUNE_SERVICE = """from typing import Sequence

from pfa.features.rune.contract import RuneReading
from pfa.kernel import Result, RuneRef, StaveId

from .rune_engine import RuneEngine


class RuneServiceImpl:
    def __init__(self) -> None:
        self._engine = RuneEngine()

    def read(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[RuneReading]:
        return self._engine.evaluate(stave_id, runes, utterance)
"""

_RUNE_MODULE = """from pfa.kernel import Registry

from .internal.rune_service import RuneServiceImpl


def register(r: Registry) -> None:
    r.register("rune", lambda: RuneServiceImpl())
"""

_KVAD_CONTRACT = """from dataclasses import dataclass
from typing import Protocol, Sequence

from pfa.kernel import Result, RuneRef, StaveId


@dataclass(frozen=True)
class Verse:
    stave_id: StaveId
    score: float


class KvadService(Protocol):
    def compose(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[Verse]: ...
"""

_KVAD_ENGINE = """from typing import Sequence

from pfa.features.kvad.contract import Verse
from pfa.kernel import Result, RuneRef, StaveId


class KvadEngine:
    def evaluate(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[Verse]:
        if not utterance.strip():
            return Result(False, None, "empty utterance")
        return Result(True, Verse(stave_id, min(1.0, 0.2 * len(runes))), None)
"""

_KVAD_SERVICE = """from typing import Sequence

from pfa.features.kvad.contract import Verse
from pfa.features.rune.contract import RuneService
from pfa.kernel import Result, RuneRef, StaveId

from .kvad_engine import KvadEngine


class KvadServiceImpl:
    def __init__(self, rune: RuneService | None = None) -> None:
        self._engine = KvadEngine()
        self._rune = rune

    def compose(self, stave_id: StaveId, runes: Sequence[RuneRef], utterance: str) -> Result[Verse]:
        return self._engine.evaluate(stave_id, runes, utterance)
"""

_KVAD_MODULE = """from pfa.features.rune.contract import RuneService
from pfa.kernel import Registry

from .internal.kvad_service import KvadServiceImpl


def register(r: Registry) -> None:
    def factory() -> object:
        rune = r.resolve("rune")
        assert isinstance(rune, RuneService)
        return KvadServiceImpl(rune)

    r.register("kvad", factory)
"""

_APPLICATION = """from pfa.features.kvad import module as kvad
from pfa.features.rune import module as rune
from pfa.kernel import Registry

FEATURES = (rune, kvad)


def build_registry() -> Registry:
    r = Registry()
    for feature in FEATURES:
        feature.register(r)
    return r
"""

_API_ROUTES = """from pfa.features.kvad.contract import KvadService, Verse
from pfa.kernel import Result, RuneRef, StaveId


def compose(service: KvadService, stave_id: str, utterance: str) -> Result[Verse]:
    return service.compose(StaveId(stave_id), [RuneRef("r", "m")], utterance)
"""

_PYPROJECT = """[tool.eitri]
slice_prefix = "pfa.features."
kernel = "pfa.kernel"
app_package = "pfa"
composition_root = "pfa.application"
token_budget = 15000

[tool.heimdall]
features = "pfa/features"
kernel = "pfa/kernel"
app = "pfa"
routes = "pfa/api/routes"
"""

FILES: dict[str, str] = {
    "pyproject.toml": _PYPROJECT,
    "pfa/__init__.py": "",
    "pfa/kernel/__init__.py": _KERNEL,
    "pfa/features/__init__.py": "",
    "pfa/features/rune/feature.json": json.dumps({"depends_on": []}, indent=2) + "\n",
    "pfa/features/rune/AGENTS.md": "# rune\n<!--heimdall:deps-->depends on: (none) + kernel<!--/heimdall:deps-->\n<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n",
    "pfa/features/rune/__init__.py": "",
    "pfa/features/rune/contract/__init__.py": "from .rune_service import Clarity, RuneReading, RuneService\n",
    "pfa/features/rune/contract/rune_service.py": _RUNE_CONTRACT,
    "pfa/features/rune/internal/__init__.py": "",
    "pfa/features/rune/internal/rune_engine.py": _RUNE_ENGINE,
    "pfa/features/rune/internal/rune_service.py": _RUNE_SERVICE,
    "pfa/features/rune/module.py": _RUNE_MODULE,
    "pfa/features/kvad/feature.json": json.dumps({"depends_on": ["rune"]}, indent=2) + "\n",
    "pfa/features/kvad/AGENTS.md": "# kvad\n<!--heimdall:deps-->depends on: rune + kernel<!--/heimdall:deps-->\n<!--heimdall:routes-->routes: (none)<!--/heimdall:routes-->\n",
    "pfa/features/kvad/__init__.py": "",
    "pfa/features/kvad/contract/__init__.py": "from .kvad_service import KvadService, Verse\n",
    "pfa/features/kvad/contract/kvad_service.py": _KVAD_CONTRACT,
    "pfa/features/kvad/internal/__init__.py": "",
    "pfa/features/kvad/internal/kvad_engine.py": _KVAD_ENGINE,
    "pfa/features/kvad/internal/kvad_service.py": _KVAD_SERVICE,
    "pfa/features/kvad/module.py": _KVAD_MODULE,
    # the application: composition root + HTTP edge. Consumes contracts, plugs in modules.
    "pfa/application.py": _APPLICATION,
    "pfa/api/__init__.py": "",
    "pfa/api/routes/__init__.py": "",
    "pfa/api/routes/kvad.py": _API_ROUTES,
}


def write_fixture_tree(root: Path) -> Path:
    """Write the fixture project under ``root`` (created if needed) and return it."""
    for rel, content in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    return root
