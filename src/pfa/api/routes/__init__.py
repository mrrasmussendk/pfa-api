"""The HTTP edge. One module per feature (``<feature>.py``: pydantic models, an ``APIRouter``
with ``prefix="/<feature>"``, Problem responses) plus the cross-cutting service routes (``health``).

A route consumes a feature exactly as another feature would: it imports the feature's
``contract`` and dispatches its messages on the bus. It never imports ``internal/`` or
``module.py``; it never calls a handler. ``ROUTERS`` is the explicit, greppable list
``create_app()`` mounts."""

from . import chunking, embeddings, health

ROUTERS = (health.router, chunking.router, embeddings.router)

__all__ = ["ROUTERS", "chunking", "embeddings", "health"]
