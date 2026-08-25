"""OpenClaw compatibility plugin for SparseRead."""

from typing import Any

__all__ = ["OpenClawBridge", "classify_openclaw_gate"]
__version__ = "0.1.1"


def __getattr__(name: str) -> Any:
    if name in __all__:
        from sparseread_openclaw import bridge

        return getattr(bridge, name)
    raise AttributeError(name)
