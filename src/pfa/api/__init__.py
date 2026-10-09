"""The HTTP entry point into the application: ``create_app()`` in ``app.py``, one routes
module per feature under ``routes/``, RFC 9457 problem details in ``problems.py``, request
hygiene in ``validation.py``. It consumes features through their contracts and the bus, like
any other caller; the application itself is built by ``pfa.application``.

No imports here on purpose (``routes/`` imports ``problems``; an eager ``app`` import would loop)."""
