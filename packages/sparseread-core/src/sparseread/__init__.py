"""Framework-neutral SparseRead public API."""

from sparseread.config import SparseReadConfig
from sparseread.wrapper import SparseRead, SparseReadAgentWrapper, wrap

__version__ = "0.1.0"

__all__ = [
    "SparseRead",
    "SparseReadAgentWrapper",
    "SparseReadConfig",
    "__version__",
    "wrap",
]
