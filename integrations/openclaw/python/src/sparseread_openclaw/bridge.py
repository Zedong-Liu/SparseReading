"""OpenClaw SparseRead JSONL bridge."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sparseread.bridge.server import BridgePolicy, SparseReadBridgeServer, serve_bridge
from sparseread.core.benefit_gate import BenefitDecision
from sparseread.core.detector import FileInfo

OPENCLAW_TEXT_ENFORCE_BYTES = 12_288


def classify_openclaw_gate(info: FileInfo, decision: BenefitDecision) -> dict[str, Any]:
    reason = decision.reason.lower()
    profile: dict[str, Any] = {
        "mode": "native",
        "prompt_style": "native",
        "block_native_read": False,
        "block_native_search": False,
        "block_native_exec_dump": False,
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
                "block_native_search": False,
                "block_native_exec_dump": False,
                "nudge_native": True,
                "trajectory": "one_collect_then_write",
                "reason": (
                    "command-security bundle has useful SR closure facts, but OpenClaw should "
                    "keep small native template and unresolved-slot reads available; use one "
                    "collection collect, then write"
                ),
            }
        )
        return profile
    if info.type in {"txt", "text", "md"} and info.size_bytes < OPENCLAW_TEXT_ENFORCE_BYTES:
        profile.update(
            {
                "reason": (
                    "OpenClaw native adaptation: text/log object is below adapter enforce threshold; "
                    "native read is cheaper than SparseRead negotiation"
                ),
            }
        )
        return profile
    enforceable_file = info.type == "pdf" or (
        info.type in {"txt", "text", "md"} and info.size_bytes >= OPENCLAW_TEXT_ENFORCE_BYTES
    )
    enforceable_collection = info.type == "collection" and (
        "audit bundle has code plus state/output evidence" in reason
        or "collection contains a long pdf/report" in reason
        or "multi-file text collection" in reason
    )
    if enforceable_file or enforceable_collection:
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
    profile.update(
        {
            "mode": "advisory",
            "prompt_style": "optional",
            "nudge_native": True,
            "trajectory": "optional",
            "reason": f"OpenClaw advisory adaptation: {decision.reason}",
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
            ),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead OpenClaw JSONL bridge")
    parser.add_argument("--workspace", default=".", help="OpenClaw workspace")
    parser.add_argument("--mode", default="auto", help="SparseRead mode")
    return serve_bridge(parser.parse_args(argv), lambda workspace, mode: OpenClawBridge(workspace=workspace, mode=mode))


if __name__ == "__main__":
    raise SystemExit(main())
