"""The sensors Heimdall runs, in order. This is the whole discovery mechanism — an explicit
list, deliberately NOT a package scan, so discovery stays greppable (consistent with the
project's own rules: wiring is explicit, never reflected).

Adding a sensor: implement ``Sensor`` in this package and add one line to ``ALL``.
"""

from .boundary_edits import BoundaryEditsSensor
from .boundary_reads import BoundaryReadsSensor
from .function_shape import FunctionShapeSensor

ALL = (
    BoundaryEditsSensor(),
    BoundaryReadsSensor(),
    FunctionShapeSensor(),
)

__all__ = ["ALL", "BoundaryEditsSensor", "BoundaryReadsSensor", "FunctionShapeSensor"]
