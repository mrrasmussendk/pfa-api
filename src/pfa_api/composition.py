"""The composition root — the one place that may import every slice's ``internal/module.py``.

Wiring is an explicit, greppable list (never a package scan), so ``grep module`` tells
you exactly which slices are deployed. Each module registers its handlers on the one bus;
a slice that dispatches another slice's queries needs that slice registered too.

Each module also exposes ``ready()`` (component loaded? — never triggers a load) and
``warmup()`` (load now). ``readiness()`` and ``warmup_all()`` fan those out for ``/ready``
and the startup warm-up thread.
"""

from __future__ import annotations

from fastapi import APIRouter

from shared_kernel import Bus
from slices.chunking.internal import module as chunking
from slices.embeddings.internal import module as embeddings

MODULES = (chunking, embeddings)


def build_bus() -> Bus:
    bus = Bus()
    for module in MODULES:
        module.register(bus)
    return bus


def slice_routers() -> list[APIRouter]:
    return [module.router for module in MODULES]


def readiness() -> dict[str, bool]:
    """Every component's loaded flag, by name. Cheap: reads flags, loads nothing."""
    out: dict[str, bool] = {}
    for module in MODULES:
        out.update(module.ready())
    return out


def warmup_all() -> None:
    """Load every heavy component. Order matters: the tokenizer (MBs) before the model (GBs),
    so chunking becomes ready first and the slower load does not hide the faster one."""
    for module in MODULES:
        module.warmup()
