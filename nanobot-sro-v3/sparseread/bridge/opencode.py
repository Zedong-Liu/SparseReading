"""OpenCode SparseRead JSONL bridge."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from nanobot.sparse_reading.benefit_gate import BenefitDecision
from nanobot.sparse_reading.detector import FileInfo
from sparseread.bridge.server import BridgePolicy, SparseReadBridgeServer, serve_bridge


def classify_opencode_gate(info: FileInfo, decision: BenefitDecision) -> dict[str, Any]:
    reason = decision.reason.lower()
    profile: dict[str, Any] = {
        "mode": "native",
        "prompt_style": "native",
        "block_native_read": False,
        "nudge_native": False,
        "trajectory": "native",
        "reason": decision.reason,
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
    if info.type == "collection" and "command-security bundle" in reason:
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "closure_once",
                "block_native_read": False,
                "nudge_native": True,
                "trajectory": "one_collect_then_write",
                "reason": (
                    "command-security bundle has compact closure facts; in OpenCode prefer "
                    "one collection collect, but allow native reads for small templates and "
                    "named unresolved files"
                ),
            }
        )
        return profile
    enforceable_collection = (
        "audit bundle has code plus state/output evidence" in reason
        or "collection contains a long pdf/report" in reason
        or "multi-file text collection" in reason
        or "large audit/diagnosis bundle" in reason
        or "diagnosis bundle contains long log" in reason
    )
    if info.type == "collection" and not enforceable_collection:
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "optional",
                "nudge_native": True,
                "trajectory": "optional",
                "reason": f"OpenCode advisory adaptation: {decision.reason}",
            }
        )
        return profile
    profile.update(
        {
            "mode": "enforce",
            "prompt_style": "sro_first",
            "block_native_read": True,
            "nudge_native": True,
            "trajectory": "sro_first",
        }
    )
    return profile


class OpenCodeBridge(SparseReadBridgeServer):
    def __init__(self, *, workspace: str | Path | None, mode: str = "auto") -> None:
        super().__init__(
            workspace=workspace,
            mode=mode,
            classifier=classify_opencode_gate,
            policy=BridgePolicy(
                platform="OpenCode",
                gate_key="opencode_gate",
                ready_guard="opencode_adapter_ready_once",
                allow_bounded_text_verify=True,
                guard_cards_after_ready=False,
            ),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead OpenCode JSONL bridge")
    parser.add_argument("--workspace", default=".", help="OpenCode workspace/worktree")
    parser.add_argument("--mode", default="auto", help="SparseRead mode")
    return serve_bridge(parser.parse_args(argv), lambda workspace, mode: OpenCodeBridge(workspace=workspace, mode=mode))


if __name__ == "__main__":
    raise SystemExit(main())
