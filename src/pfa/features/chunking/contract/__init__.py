"""The chunking Contract: count tokens the way the model does, and split text to a token
budget on sentence boundaries. Dispatch on the bus; never import ``internal/``."""

from .queries import CountTokens, SplitText
from .results import Chunks

__all__ = ["Chunks", "CountTokens", "SplitText"]
