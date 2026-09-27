"""Installed-transport checks without credentials, model calls, or workspace scans."""

import argparse
import asyncio
import json
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sparseread_agent_tools.mcp import ALL_TOOLS


async def check_mcp(workspace: str, mode: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "sparseread_agent_tools.mcp", "--host", "codex", "--workspace", workspace, "--mode", mode])
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            if names != {tool.name for tool in ALL_TOOLS}:
                raise RuntimeError(f"Unexpected MCP tools: {names}")
            result = await session.call_tool("sro_trace", {})
            if result.isError:
                raise RuntimeError("MCP sro_trace failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", choices=["codex", "pi"], required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", choices=["auto", "advisory"], default="auto")
    args = parser.parse_args(argv)
    if args.host == "codex":
        asyncio.run(asyncio.wait_for(check_mcp(args.workspace, args.mode), timeout=20))
    else:
        request = json.dumps({"id": "version", "method": "version"}) + "\n" + json.dumps({"id": "stop", "method": "shutdown"}) + "\n"
        result = subprocess.run([sys.executable, "-m", "sparseread_agent_tools.bridge", "--host", "pi", "--workspace", args.workspace, "--mode", args.mode],
                                input=request, text=True, capture_output=True, check=True, timeout=20)
        messages = [json.loads(line) for line in result.stdout.splitlines()]
        if len(messages) != 2 or messages[0].get("result", {}).get("protocol_version") != "1.0" or not all(item.get("ok") for item in messages):
            raise RuntimeError(f"Invalid JSONL handshake: {messages}")
    print(f"[doctor] {args.host} installed transport passed (protocol 1.0" + (", six MCP tools)" if args.host == "codex" else ")"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
