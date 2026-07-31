"""SparseRead MCP Server for Claude Code.

Exposes SRO (Sparse Reading Orchestrator) tools to Claude Code via the MCP
protocol.  Claude Code connects to this server through its MCP configuration
in .claude/settings.json.

Usage:
  uv run --project nanobot-sro-v3 python -m sparseread.bridge.claude_mcp --workspace .

The server wraps the ClaudeBridge (a SparseReadBridgeServer) and translates
MCP tool calls into bridge requests.  Tool descriptions serve as a "pseudo
system prompt" — Claude reads them as tool metadata and learns when to use
SRO vs native reads.

Architecture:
  Claude Code (MCP client)
    │  stdio JSON-RPC (MCP protocol)
    ▼
  SparseReadClaudeMCP (this file)
    │  in-process method calls
    ▼
  ClaudeBridge (sparseread.bridge.claude)
    │  delegate to shared server
    ▼
  SparseReadBridgeServer (sparseread.bridge.server)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from sparseread.bridge.claude import ClaudeBridge


# ---------------------------------------------------------------------------
# Tool definitions — each description doubles as Claude Code guidance
# ---------------------------------------------------------------------------

SRO_PREVIEW_TOOL = Tool(
    name="sro_preview",
    description=(
        "⚠️ PRIMARY SparseRead ENTRYPOINT for large files, PDFs, and directories. "
        "Use INSTEAD OF read_file when the target is: "
        "(1) a PDF file of any size, "
        "(2) a text/markdown/log file larger than ~12KB, "
        "(3) a directory with 3+ files, "
        "(4) a CSV/JSON/structured data file over ~8KB. "
        "Returns a compact preview with structure overview, content samples, "
        "key signals, a raw_ref for full-content fallback, and next_action "
        "guidance. After preview: if the preview is sufficient, answer directly; "
        "if you need specific evidence, call sro_read with the returned "
        "artifact_id and a concrete HintSpec. "
        "Do NOT call read_file or Bash cat/head on the same path after a preview "
        "— the preview already tells you what you need."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute or workspace-relative path to the file, PDF, or "
                    "directory to preview."
                ),
            },
            "artifact_id": {
                "type": "string",
                "description": (
                    "Existing SparseRead artifact ID (from a prior preview or "
                    "card). Use this for follow-up previews; omit for first look."
                ),
            },
        },
    },
)

SRO_READ_TOOL = Tool(
    name="sro_read",
    description=(
        "TARGETED sparse evidence extraction AFTER sro_preview. "
        "Call only when the preview is insufficient and you need specific facts. "
        "Modes: "
        "scout = quick structural scan; "
        "focus = extract a single specific fact/needle; "
        "collect = multi-slot evidence collection (provide explicit slots in hint); "
        "refine = narrow a prior result; "
        "verify = confirm a candidate answer against source. "
        "The hint MUST contain a 'goal' describing what evidence you need. "
        "For collect mode, also provide 'slots' (list of {id, question} objects) "
        "or 'needles' (list of strings to find). "
        "CRITICAL: when the result contains 'protocol_next: write_file_now' or "
        "slot_digest.overall_status is 'ready', STOP reading — write the "
        "deliverable immediately. Do NOT verify, refine, or re-read resolved slots."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "target": {
                "type": "object",
                "description": (
                    'Use {"artifact_id": "<id>"} for follow-up reads on a previewed '
                    'artifact, or {"path": "<path>"} for an initial direct read.'
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["scout", "focus", "collect", "refine", "verify"],
                "description": "Reading mode: scout, focus, collect, refine, or verify.",
            },
            "hint": {
                "type": "object",
                "description": (
                    "HintSpec with 'goal' (required: what evidence you need), "
                    "plus optional 'needles' (list of strings to locate), "
                    "'slots' (list of {id, question} for collect mode), "
                    "'want' (fact/list/count/schema/table/verbatim), "
                    "'scope' (new = only unscanned parts, all = full artifact), "
                    "and 'type_hint' (text/csv/json/pdf/collection)."
                ),
            },
        },
        "required": ["target", "mode", "hint"],
    },
)

SRO_CARD_TOOL = Tool(
    name="sro_card",
    description=(
        "COMPATIBILITY/DEBUG: Return a SparseRead FileCard with metadata, "
        "recommended reading mode, and Claude Code gate profile. "
        "Production flows should use sro_preview instead — it provides the "
        "same card metadata plus structure, samples, and signals in one call."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file, document, or directory to inspect.",
            },
        },
        "required": ["path"],
    },
)

SRO_RAW_TOOL = Tool(
    name="sro_raw",
    description=(
        "FALLBACK: Retrieve original content behind a raw_ref returned by "
        "sro_preview. Use ONLY when the preview and targeted reads are "
        "insufficient — for example, when you need to see exact formatting "
        "or a very specific unfiltered byte range. "
        "Prefer sro_read for evidence extraction; raw is for last-resort "
        "full-content access."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "raw_ref": {
                "type": "string",
                "description": "The raw_ref string returned by sro_preview.",
            },
            "range": {
                "type": "object",
                "description": "Optional byte range: {start: int, end: int}.",
            },
            "selector": {
                "type": "string",
                "description": "Optional case-insensitive line selector string.",
            },
        },
        "required": ["raw_ref"],
    },
)

SRO_DECIDE_TOOL = Tool(
    name="sro_decide",
    description=(
        "DIAGNOSTIC: Inspect a path and return the SparseRead gate decision "
        "plus Claude Code adapter gate profile. Shows whether SparseRead "
        "would enforce, advise, or stay native for this path, and why. "
        "Useful for understanding why a file was blocked or nudged."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to inspect for gate decision.",
            },
        },
        "required": ["path"],
    },
)

SRO_TRACE_TOOL = Tool(
    name="sro_trace",
    description=(
        "Return the SparseRead session trace: all preview/read/card/decide "
        "events, native tool events, gate decisions, usage stats, and "
        "adapter guard hits. Call at the end of a session or when debugging "
        "SparseRead behavior."
    ),
    inputSchema={"type": "object", "properties": {}},
)

SRO_PREFLIGHT_TOOL = Tool(
    name="sro_preflight",
    description=(
        "Scan the workspace and return high-confidence evidence targets "
        "that SparseRead recommends previewing first. Call once at the "
        "start of a complex multi-file task to discover which paths are "
        "best handled by SparseRead."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "max_candidates": {
                "type": "integer",
                "description": "Max entries to scan (1-64, default 24).",
            },
            "max_results": {
                "type": "integer",
                "description": "Max handoff targets to return (1-5, default 3).",
            },
        },
    },
)

SRO_USAGE_TOOL = Tool(
    name="sro_usage",
    description=(
        "Return detailed SparseRead token consumption metrics for the "
        "current session. Provides per-operation token estimates (full-file "
        "token cost vs SR response token cost), cumulative session savings, "
        "context window retention percentage, and top savings by artifact. "
        "All metrics are calculated from within SparseRead — no API key or "
        "host-platform internals needed. Call at the end of a session, "
        "periodically during long sessions, or whenever you need to report "
        "quantitative token savings from using SparseRead."
    ),
    inputSchema={"type": "object", "properties": {}},
)

ALL_TOOLS = [
    SRO_PREVIEW_TOOL,
    SRO_READ_TOOL,
    SRO_CARD_TOOL,
    SRO_RAW_TOOL,
    SRO_DECIDE_TOOL,
    SRO_TRACE_TOOL,
    SRO_PREFLIGHT_TOOL,
    SRO_USAGE_TOOL,
]

# ---------------------------------------------------------------------------
# MCP server setup
# ---------------------------------------------------------------------------


def _safe_json(value: Any) -> str:
    """Serialize bridge results to JSON for MCP text output."""
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


class SparseReadClaudeMCP:
    """MCP wrapper around a ClaudeBridge instance.

    Each MCP tool call is dispatched to the bridge's handle() method, which
    delegates to the shared SparseReadBridgeServer logic (preview, read, card,
    raw, decide, trace, preflight).
    """

    def __init__(self, workspace: str, mode: str = "auto") -> None:
        self.workspace = workspace
        self.mode = mode
        self.bridge = ClaudeBridge(workspace=workspace, mode=mode)

    def handle(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatch a named method to the bridge."""
        request: dict[str, Any] = {"method": method, "params": params or {}}
        return self.bridge.handle(request)

    def handle_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Handle an MCP tool call and return a JSON string result."""
        method_map: dict[str, str] = {
            "sro_preview": "preview",
            "sro_read": "read",
            "sro_card": "card",
            "sro_raw": "raw",
            "sro_decide": "decide",
            "sro_trace": "trace",
            "sro_preflight": "preflight",
            "sro_usage": "usage",
        }
        method = method_map.get(tool_name)
        if method is None:
            return _safe_json({"error": f"unknown tool: {tool_name}"})

        try:
            result = self.handle(method, arguments)
            return _safe_json(result)
        except Exception as exc:
            return _safe_json({"error": str(exc)})


def build_server(sr_mcp: SparseReadClaudeMCP) -> Server:
    """Create an MCP Server instance with tool handlers bound to the bridge."""
    server = Server("sparseread")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return ALL_TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        result_text = sr_mcp.handle_tool(name, arguments)
        return [TextContent(type="text", text=result_text)]

    return server


async def run_mcp_server(workspace: str, mode: str) -> None:
    """Start the MCP stdio server and block until the client disconnects."""
    sr_mcp = SparseReadClaudeMCP(workspace=workspace, mode=mode)
    mcp_server = build_server(sr_mcp)

    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SparseRead MCP Server for Claude Code",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root directory (default: current directory)",
    )
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "force", "advisory", "native", "force_sro", "bench_protocol"],
        help="SparseRead operating mode (default: auto)",
    )
    args = parser.parse_args(argv)

    workspace = str(Path(args.workspace).resolve())
    print(f"[sparseread] Starting MCP server — workspace: {workspace}, mode: {args.mode}", file=sys.stderr)

    try:
        asyncio.run(run_mcp_server(workspace, args.mode))
    except KeyboardInterrupt:
        print("[sparseread] MCP server stopped.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
