"""The sensors Heimdall runs, in order. This is the whole discovery mechanism — an explicit
list, deliberately NOT a package scan, so discovery stays greppable (consistent with the
project's own rules: wiring is explicit, never reflected).

Adding a sensor: implement ``Sensor`` in this package and add one line to ``ALL``.
"""

from .boundary_edits import BoundaryEditsSensor
from .boundary_reads import BoundaryReadsSensor

ALL = (
    BoundaryEditsSensor(),
    BoundaryReadsSensor(),
)

__all__ = ["ALL", "BoundaryEditsSensor", "BoundaryReadsSensor"]
