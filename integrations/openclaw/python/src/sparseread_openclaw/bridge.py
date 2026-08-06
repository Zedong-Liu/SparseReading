"""OpenClaw SparseRead JSONL bridge."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sparseread.bridge.server import BridgePolicy, SparseReadBridgeServer, serve_bridge
from sparseread.core.benefit_gate import BenefitDecision
from sparseread.core.detector import FileInfo

def classify_openclaw_gate(info: FileInfo, decision: BenefitDecision) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "mode": "native",
        "prompt_style": "native",
        "block_native_read": False,
        "block_native_search": False,
        "block_native_exec_dump": False,
        "nudge_native": False,
        "trajectory": "native",
        "reason": decision.reason,
        "decision_code": decision.code,
        "preview_recommended": decision.preview_recommended,
    }
    if decision.mode == "native":
        return profile
    if decision.mode == "advisory":
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "optional",
                "nudge_native": True,
                "trajectory": "optional",
            }
        )
        return profile
    profile.update(
        {
            "mode": "enforce",
            "prompt_style": "sro_first",
            "block_native_read": True,
            "block_native_search": True,
            "block_native_exec_dump": True,
            "nudge_native": True,
            "trajectory": "sro_first",
        }
    )
    return profile


class OpenClawBridge(SparseReadBridgeServer):
    def __init__(self, *, workspace: str | Path | None, mode: str = "auto") -> None:
        super().__init__(
            workspace=workspace,
            mode=mode,
            classifier=classify_openclaw_gate,
            policy=BridgePolicy(
                platform="OpenClaw",
                gate_key="openclaw_gate",
                ready_guard="openclaw_adapter_closure_once",
                allow_bounded_text_verify=False,
                guard_cards_after_ready=True,
                native_passthrough_include_search=True,
            ),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead OpenClaw JSONL bridge")
    parser.add_argument("--workspace", default=".", help="OpenClaw workspace")
    parser.add_argument("--mode", default="auto", help="SparseRead mode")
    return serve_bridge(parser.parse_args(argv), lambda workspace, mode: OpenClawBridge(workspace=workspace, mode=mode))


if __name__ == "__main__":
    raise SystemExit(main())
