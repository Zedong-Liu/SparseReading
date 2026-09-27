"""Host naming, workspace-relative paths, and unchanged core dispatch."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from sparseread.bridge.server import BridgePolicy, SparseReadBridgeServer
from sparseread.core.benefit_gate import BenefitDecision
from sparseread.core.detector import FileInfo


def classify_host_gate(info: FileInfo, decision: BenefitDecision) -> dict[str, Any]:
    # Only translate the core's decision into frontend controls, never re-score it.
    force = decision.mode == "force_sro"
    mode = "enforce" if force else decision.mode
    return {
        "mode": mode,
        "reason": decision.reason,
        "decision_code": decision.code,
        "preview_recommended": decision.preview_recommended,
        "block_native_read": force,
        "block_native_search": False,
        "block_native_exec_dump": force,
        "nudge_native": decision.mode != "native",
        "trajectory": "sro_first" if force else "optional" if mode == "advisory" else "native",
    }


class HostBridge(SparseReadBridgeServer):
    def __init__(self, *, host: str, workspace: str | Path, mode: str = "auto") -> None:
        if host not in {"codex", "pi"}:
            raise ValueError("host must be codex or pi")
        self.root = Path(workspace).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"workspace is not a directory: {self.root}")
        super().__init__(
            workspace=self.root,
            mode=mode,
            classifier=classify_host_gate,
            policy=BridgePolicy(
                platform="Codex" if host == "codex" else "Pi",
                gate_key="host_gate",
                ready_guard=f"{host}_adapter_ready_once",
                allow_bounded_text_verify=True,
                guard_cards_after_ready=False,
            ),
        )

    def _path(self, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("path must be a non-empty string")
        path = Path(value).expanduser()
        resolved = (path if path.is_absolute() else self.root / path).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("SparseRead target is outside the configured workspace; use native tools")
        return str(resolved)

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        request = deepcopy(request)
        params = request.get("params")
        if params is not None and not isinstance(params, dict):
            raise ValueError("params must be an object")
        if isinstance(params, dict):
            if "path" in params:
                params["path"] = self._path(params["path"])
            target = params.get("target")
            if isinstance(target, str):
                params["target"] = {"path": self._path(target)}
            elif isinstance(target, dict) and "path" in target:
                target["path"] = self._path(target["path"])
        return super().handle(request)
