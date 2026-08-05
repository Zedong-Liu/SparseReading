"""Claude Code SparseRead JSONL bridge.

This adapter follows the same shape as the OpenCode/OpenClaw adapters: a thin
bridge over the framework-neutral ``sparseread-core`` runtime.  Claude Code
specifics (MCP transport, PreToolUse/PostToolUse hook capabilities) live in
``claude_mcp`` and ``hook``; the core gate, episode controller, readers, and
denoise layer are shared unchanged.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from sparseread.bridge.server import BridgePolicy, SparseReadBridgeServer, serve_bridge
from sparseread.core.benefit_gate import BenefitDecision
from sparseread.core.detector import FileInfo


CLAUDE_TEXT_ENFORCE_BYTES = 12_288  # 12 KB adapter-level floor, same spirit as OpenClaw

# Decision codes that have a validated sparse-reading path on Claude Code.
# The classifier keys off structured fields, never off filenames or reasons.
_CLAUDE_ENFORCE_CODES = {
    "long_document",
    "long_document_selective",
    "collection_long_document",
    "multi_file_evidence",
    "structured_analysis_plan",
}
_CLAUDE_ONE_COLLECT_CODES = {"structured_analysis_plan"}
_CLAUDE_TEXT_KINDS = {"text", "txt", "md", "markdown", "rst"}


def classify_claude_gate(info: FileInfo, decision: BenefitDecision) -> dict[str, Any]:
    """Map a core BenefitDecision to the Claude Code hook/MCP gate profile.

    Claude Code can block ``read_file`` via PreToolUse exit(2), block
    ``bash`` cat/head-style dumps, and inject ``additionalContext``.  The
    profile keeps those Claude-specific capability fields while the routing
    itself comes from the shared core decision.
    """
    profile: dict[str, Any] = {
        "mode": "native",
        "prompt_style": "native",
        # Shared server fields (used by _force_collection_parent_gate/preflight).
        "block_native_read": False,
        "block_native_search": False,
        "block_native_exec_dump": False,
        # Claude-specific hook capability fields.
        "hook_can_block_read": False,
        "hook_can_block_bash": False,
        "hook_can_inject_context": False,
        "nudge_native": False,
        "trajectory": "native",
        "reason": decision.reason,
        "decision_code": decision.code,
        "scope_kind": decision.scope_kind,
        "preview_recommended": decision.preview_recommended,
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

    # force_sro below: keep native reads for small text where negotiation is
    # more expensive than the object itself (Claude adapter-level floor).
    if info.type in _CLAUDE_TEXT_KINDS and info.size_bytes < CLAUDE_TEXT_ENFORCE_BYTES:
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "optional",
                "hook_can_inject_context": True,
                "nudge_native": True,
                "trajectory": "optional",
                "reason": (
                    "Claude adapter floor: text object is below the enforce "
                    "threshold; native read is cheaper than SRO negotiation"
                ),
            }
        )
        return profile

    if decision.code in _CLAUDE_ENFORCE_CODES or info.type == "pdf":
        trajectory = (
            "one_collect_then_write"
            if decision.code in _CLAUDE_ONE_COLLECT_CODES
            else "sro_first"
        )
        prompt_style = "closure_once" if decision.code in _CLAUDE_ONE_COLLECT_CODES else "sro_first"
        profile.update(
            {
                "mode": "enforce",
                "prompt_style": prompt_style,
                "block_native_read": True,
                "block_native_search": True,
                "block_native_exec_dump": True,
                "hook_can_block_read": True,
                "hook_can_block_bash": True,
                "hook_can_inject_context": True,
                "nudge_native": True,
                "trajectory": trajectory,
            }
        )
        return profile

    # Remaining force_sro shapes are treated as advisory on Claude until a
    # validated trajectory exists for them.
    profile.update(
        {
            "mode": "advisory",
            "prompt_style": "optional",
            "hook_can_inject_context": True,
            "nudge_native": True,
            "trajectory": "optional",
            "reason": f"Claude adapter advisory adaptation: {decision.reason}",
        }
    )
    return profile


class ClaudeBridge(SparseReadBridgeServer):
    """Claude Code SparseRead bridge.

    Uses the shared core runtime and a Claude-specific gate classifier that
    reflects Claude Code's hook capabilities (PreToolUse exit(2),
    additionalContext injection, no direct grep/search blocking).
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
