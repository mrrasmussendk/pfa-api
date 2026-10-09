"""The application: every feature registered on one bus. No HTTP anywhere in here.

This is the one place that imports a feature's ``module.py`` (its plug). Wiring is an
explicit, greppable list (never a package scan), so ``grep module`` tells you exactly which
features are deployed. Each module registers its handlers on the one bus; a feature that
dispatches another feature's queries needs that feature registered too.

Entry points (``pfa.api`` today; a CLI or a worker tomorrow) call ``build_bus()`` and expose
``readiness()`` / ``warmup_all()`` however suits them — ``GET /ready`` and the startup
warm-up thread, in the API's case.
"""

from __future__ import annotations

from pfa.features.chunking import module as chunking
from pfa.features.embeddings import module as embeddings
from pfa.kernel import Bus

FEATURES = (chunking, embeddings)


def build_bus() -> Bus:
    bus = Bus()
    for feature in FEATURES:
        feature.register(bus)
    return bus


def readiness() -> dict[str, bool]:
    """Every component's loaded flag, by name. Cheap: reads flags, loads nothing."""
    out: dict[str, bool] = {}
    for feature in FEATURES:
        out.update(feature.ready())
    return out


def warmup_all() -> None:
    """Load every heavy component. Order matters: the tokenizer (MBs) before the model (GBs),
    so chunking becomes ready first and the slower load does not hide the faster one."""
    for feature in FEATURES:
        feature.warmup()
