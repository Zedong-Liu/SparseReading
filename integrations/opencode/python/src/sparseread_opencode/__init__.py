"""OpenCode compatibility plugin for SparseRead."""

from typing import Any

__all__ = ["OpenCodeBridge", "classify_opencode_gate"]
__version__ = "0.1.0"


def __getattr__(name: str) -> Any:
    if name in __all__:
        from sparseread_opencode import bridge

        return getattr(bridge, name)
    raise AttributeError(name)
