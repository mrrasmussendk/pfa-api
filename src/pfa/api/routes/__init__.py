"""The HTTP edge. One module per feature (``<feature>.py``: pydantic models, an ``APIRouter``
with ``prefix="/<feature>"``, Problem responses) plus the cross-cutting service routes (``health``).

A route consumes a feature exactly as another feature would: it imports the feature's
``contract`` and dispatches its messages on the bus. It never imports ``internal/`` or
``module.py``; it never calls a handler. ``ROUTERS`` is the explicit, greppable list
``create_app()`` mounts; ``TAGS`` is each module's ``TAG``, the description its group
carries in the OpenAPI document, in the order the document lists them."""

from . import chunking, embeddings, health

ROUTERS = (health.router, chunking.router, embeddings.router)
TAGS = (health.TAG, chunking.TAG, embeddings.TAG)

__all__ = ["ROUTERS", "TAGS", "chunking", "embeddings", "health"]
