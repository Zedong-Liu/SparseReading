"""Codex MCP tools: a thin stdio transport around the shared core bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

from sparseread_agent_tools.runtime import HostBridge


EPISODE_HINT = {
    "type": "object",
    "properties": {
        "goal": {"type": "string", "enum": ["selective_read", "cross_file_evidence", "structured_compute", "edit_or_execute", "full_fidelity", "unknown"]},
        "relation": {"type": "string", "enum": ["new", "continue", "switch", "unknown"]},
        "coverage": {"type": "string", "enum": ["selective", "exhaustive", "unknown"]},
        "summary": {"type": "string"},
    },
}


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    result = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        result["required"] = required
    return result


PATH = {"type": "string", "minLength": 1, "description": "Absolute or workspace-relative path; targets outside this workspace use native tools."}
TARGET = schema({"path": PATH, "artifact_id": {"type": "string"}})
TARGET["anyOf"] = [{"required": ["path"]}, {"required": ["artifact_id"]}]

ALL_TOOLS = [
    Tool(name="sro_preview", description="Start selective reading of large evidence files/PDFs/directories. Returns compact structure, raw_ref, and next_action. Native/compute/full-fidelity guidance takes precedence; this is not a replacement for exact full-file computation.",
         inputSchema={**schema({"path": PATH, "artifact_id": {"type": "string"}, "episode_hint": EPISODE_HINT}), "anyOf": [{"required": ["path"]}, {"required": ["artifact_id"]}]}),
    Tool(name="sro_read", description="After preview, extract targeted evidence with scout/focus/collect/refine/verify. Hint needs a goal and optional needles or slots [{id,question,expected,aliases}]. Stop reading when slot_digest/next_action says ready; write the deliverable.",
         inputSchema=schema({"target": TARGET, "mode": {"type": "string", "enum": ["scout", "focus", "collect", "refine", "verify"]}, "hint": {"type": "object", "description": "HintSpec: goal, needles, slots, want, scope, type_hint, must_keep."}, "episode_hint": EPISODE_HINT}, ["target", "mode", "hint"])),
    Tool(name="sro_raw", description="Exact-content fallback using raw_ref returned by preview. Use when sparse evidence is insufficient; range is a byte range, selector filters lines.",
         inputSchema=schema({"raw_ref": {"type": "string"}, "range": schema({"start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0}}), "selector": {"type": "string"}}, ["raw_ref"])),
    Tool(name="sro_card", description="Compatibility/debug FileCard metadata; prefer sro_preview for normal reading.", inputSchema=schema({"path": PATH}, ["path"])),
    Tool(name="sro_decide", description="Inspect a path and report the unchanged core benefit gate, without extracting its content.", inputSchema=schema({"path": PATH, "episode_hint": EPISODE_HINT}, ["path"])),
    Tool(name="sro_trace", description="Inspect current bridge session events and gate decisions. No workspace scan.", inputSchema=schema({})),
]


def build_server(bridge: HostBridge) -> Server:
    server = Server("sparseread")
    names = {tool.name for tool in ALL_TOOLS}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return ALL_TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        try:
            if name not in names:
                raise ValueError(f"unknown tool: {name}")
            # Calls dispatch serially without await: core episode state cannot race.
            result = bridge.handle({"method": name.removeprefix("sro_"), "params": arguments or {}})
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, separators=(",", ":")))])
        except Exception as exc:
            return CallToolResult(isError=True, content=[TextContent(type="text", text=json.dumps({"error": str(exc), "fallback": "Use native bounded reading or exact compute; SparseRead is optional."}, ensure_ascii=False))])

    return server


async def run_server(host: str, workspace: str, mode: str) -> None:
    server = build_server(HostBridge(host=host, workspace=workspace, mode=mode))
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead Codex MCP server")
    parser.add_argument("--host", choices=["codex", "pi"], default="codex")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--mode", choices=["auto", "advisory"], default="auto")
    args = parser.parse_args(argv)
    asyncio.run(run_server(args.host, args.workspace, args.mode))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
