"""PFA — the application. ``pfa.application`` builds it: every feature under ``features/``
registered on one bus from ``kernel/``. ``pfa.api`` is its main entry point (HTTP).

No imports here on purpose: features import ``pfa.kernel``, so anything imported eagerly from
this package would run before the features exist and loop."""
