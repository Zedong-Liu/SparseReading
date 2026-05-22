"""SparseRead public configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SparseReadMode = Literal["auto", "force", "force_sro", "native", "advisory"]


@dataclass(slots=True)
class SparseReadConfig:
    """User-facing SparseRead configuration."""

    mode: SparseReadMode = "auto"
    workspace: str | Path | None = None
    benefit_gate: bool = True

    def benefit_gate_override(self) -> str | None:
        if self.mode == "auto":
            return None
        if self.mode == "force":
            return "force_sro"
        if self.mode in {"force_sro", "native", "advisory"}:
            return self.mode
        raise ValueError(f"Unsupported SparseRead mode: {self.mode}")
