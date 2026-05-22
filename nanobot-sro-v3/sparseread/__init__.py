"""Public SparseRead API.

SparseRead is the public package facade.  The current implementation is backed
by nanobot's internal Sparse Reading Orchestrator while the protocol is being
extracted into a framework-neutral package.
"""

from sparseread.config import SparseReadConfig
from sparseread.wrapper import SparseRead, SparseReadAgentWrapper, wrap

__all__ = [
    "SparseRead",
    "SparseReadAgentWrapper",
    "SparseReadConfig",
    "wrap",
]
