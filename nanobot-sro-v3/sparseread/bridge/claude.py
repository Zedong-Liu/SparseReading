"""Claude Code SparseRead JSONL bridge."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from nanobot.sparse_reading.benefit_gate import BenefitDecision
from nanobot.sparse_reading.detector import FileInfo
from sparseread.bridge.server import BridgePolicy, SparseReadBridgeServer, serve_bridge


CLAUDE_TEXT_ENFORCE_BYTES = 12_288  # 12 KB — same threshold as OpenClaw


def classify_claude_gate(info: FileInfo, decision: BenefitDecision) -> dict[str, Any]:
    """Claude Code gate classifier.

    Claude Code can block read_file via PreToolUse exit(2) and can inject
    additionalContext.  It cannot block grep/search directly (Claude Code
    handles those through a different mechanism), but it can block bash
    cat/head commands and redirect to sro_preview.

    Gate profile fields:
      - mode: native | advisory | enforce
      - hook_can_block_read: PreToolUse exit(2) can block this
      - hook_can_block_bash: PreToolUse exit(2) can block cat/head
      - hook_can_inject_context: additionalContext is available
      - trajectory: native | optional | one_collect_then_write | sro_first
      - reason: human-readable explanation
    """
    reason = decision.reason.lower()

    # Base profile includes both Claude-specific hook fields AND the shared
    # server fields that _force_collection_parent_gate and preflight check.
    profile: dict[str, Any] = {
        "mode": "native",
        "prompt_style": "native",
        # Shared server fields (used by _force_collection_parent_gate, preflight)
        "block_native_read": False,
        "block_native_search": False,
        "block_native_exec_dump": False,
        # Claude-specific hook capability fields
        "hook_can_block_read": False,
        "hook_can_block_bash": False,
        "hook_can_inject_context": False,
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
                "hook_can_inject_context": True,
                "nudge_native": True,
                "trajectory": "optional",
            }
        )
        return profile

    # command-security bundles → advisory with one-collect-then-write
    if info.type == "collection" and "command-security bundle" in reason:
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "closure_once",
                "block_native_read": False,
                "block_native_search": False,
                "block_native_exec_dump": False,
                "hook_can_block_read": False,
                "hook_can_block_bash": False,
                "hook_can_inject_context": True,
                "nudge_native": True,
                "trajectory": "one_collect_then_write",
                "reason": (
                    "Claude Code adaptation: command-security bundle has useful SR "
                    "closure facts; use one collection collect then write, but keep "
                    "small native template reads available"
                ),
            }
        )
        return profile

    # text/md files below enforce threshold → advisory (native read is cheaper)
    if info.type in {"txt", "text", "md"} and info.size_bytes < CLAUDE_TEXT_ENFORCE_BYTES:
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "optional",
                "hook_can_inject_context": True,
                "nudge_native": True,
                "trajectory": "optional",
                "reason": (
                    "Claude Code adaptation: text object is below adapter enforce "
                    "threshold; native read is cheaper than SparseRead negotiation"
                ),
            }
        )
        return profile

    # enforceable file types: PDF or large text/md
    enforceable_file = info.type == "pdf" or (
        info.type in {"txt", "text", "md"} and info.size_bytes >= CLAUDE_TEXT_ENFORCE_BYTES
    )

    # enforceable collections: audit bundles, PDF collections, multi-file text
    enforceable_collection = info.type == "collection" and (
        "audit bundle has code plus state/output evidence" in reason
        or "collection contains a long pdf/report" in reason
        or "multi-file text collection" in reason
        or "large audit/diagnosis bundle" in reason
        or "diagnosis bundle contains long log" in reason
    )

    if enforceable_file or enforceable_collection:
        profile.update(
            {
                "mode": "enforce",
                "prompt_style": "sro_first",
                # Shared server fields
                "block_native_read": True,
                "block_native_search": True,
                "block_native_exec_dump": True,
                # Claude-specific hook capability fields
                "hook_can_block_read": True,
                "hook_can_block_bash": True,
                "hook_can_inject_context": True,
                "nudge_native": True,
                "trajectory": "sro_first",
            }
        )
        return profile

    # remaining collections and large structured files → advisory
    profile.update(
        {
            "mode": "advisory",
            "prompt_style": "optional",
            "hook_can_inject_context": True,
            "nudge_native": True,
            "trajectory": "optional",
            "reason": f"Claude Code advisory adaptation: {decision.reason}",
        }
    )
    return profile


class ClaudeBridge(SparseReadBridgeServer):
    """Claude Code SparseRead bridge.

    Uses the Claude-specific gate classifier and a policy that reflects
    Claude Code's hook capabilities (PreToolUse block via exit(2),
    additionalContext injection, no grep/search blocking).
    """

    def __init__(self, *, workspace: str | Path | None, mode: str = "auto") -> None:
        super().__init__(
            workspace=workspace,
            mode=mode,
            classifier=classify_claude_gate,
            policy=BridgePolicy(
                platform="Claude Code",
                gate_key="claude_gate",
                ready_guard="claude_adapter_ready_once",
                allow_bounded_text_verify=False,
                guard_cards_after_ready=True,
            ),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead Claude Code JSONL bridge")
    parser.add_argument("--workspace", default=".", help="Claude Code workspace")
    parser.add_argument("--mode", default="auto", help="SparseRead mode")
    return serve_bridge(
        parser.parse_args(argv),
        lambda workspace, mode: ClaudeBridge(workspace=workspace, mode=mode),
    )


if __name__ == "__main__":
    raise SystemExit(main())
