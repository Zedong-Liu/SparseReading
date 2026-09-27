#!/usr/bin/env python3
"""Read-only Codex app-server plugin check; no model turns or hook trust writes.

Run after project install, with an isolated CODEX_HOME for test fixtures.
The caller must have explicitly trusted the target project in that test home.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--codex", default="codex")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    messages: queue.Queue[dict] = queue.Queue()
    diagnostics: list[str] = []
    process = subprocess.Popen([args.codex, "app-server", "--stdio"], cwd=workspace, env=os.environ.copy(),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def read() -> None:
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        messages.put({"error": "Codex app-server stopped: " + "".join(diagnostics)[-2000:]})

    def read_errors() -> None:
        for line in process.stderr:
            diagnostics.append(line)
            del diagnostics[:-20]

    threading.Thread(target=read, daemon=True).start()
    threading.Thread(target=read_errors, daemon=True).start()
    next_id = 0

    def rpc(method: str, params: dict) -> dict:
        nonlocal next_id
        next_id += 1
        process.stdin.write(json.dumps({"id": next_id, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while True:
            message = messages.get(timeout=30)
            if "id" not in message and "error" in message:
                raise RuntimeError(message["error"])
            if message.get("id") == next_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]

    try:
        rpc("initialize", {"clientInfo": {"name": "sparseread-smoke", "version": "0.1.1"}, "capabilities": {"experimentalApi": True}})
        process.stdin.write('{"method":"initialized"}\n')
        process.stdin.flush()
        listing = rpc("plugin/list", {"cwds": [str(workspace)], "marketplaceKinds": ["local"], "forceRefetch": False})
        catalog = [{"name": market["name"], "plugins": [plugin for plugin in market.get("plugins", []) if plugin.get("name") == "sparseread-codex"]} for market in listing.get("marketplaces", [])]
        print(json.dumps({"catalog": [market for market in catalog if market["plugins"]]}, ensure_ascii=False))
        detail = rpc("plugin/read", {"pluginName": "sparseread-codex", "marketplacePath": str(workspace / ".agents/plugins/marketplace.json")})
        plugin = detail["plugin"]
        print(json.dumps({"components": {key: plugin.get(key) for key in ("mcpServers", "skills", "hooks")}}, ensure_ascii=False))
        if not plugin.get("mcpServers") or not plugin.get("skills") or not plugin.get("hooks"):
            raise RuntimeError("Codex did not discover the MCP server, skill, and bundled hooks")
        thread = rpc("thread/start", {"cwd": str(workspace), "ephemeral": True})
        thread_id = thread["thread"]["id"]
        expected = {"sro_preview", "sro_read", "sro_raw", "sro_card", "sro_decide", "sro_trace"}
        deadline = time.monotonic() + 20
        while True:
            inventory = rpc("mcpServerStatus/list", {"threadId": thread_id})
            matches = [server for server in inventory.get("data", []) if "sparseread" in server.get("name", "")]
            if matches and matches[0].get("runtimeStatus") == "failed":
                raise RuntimeError("SparseRead MCP startup failed: " + str(matches[0].get("toolsError")))
            if matches and expected.issubset(matches[0].get("tools", {})):
                print(json.dumps({"mcp": {"name": matches[0]["name"], "tools": list(matches[0]["tools"])}}, ensure_ascii=False))
                result = rpc("mcpServer/tool/call", {"threadId": thread_id, "server": matches[0]["name"], "tool": "sro_trace", "arguments": {}})
                if result.get("isError"):
                    raise RuntimeError(f"Host MCP trace call failed: {result}")
                print(json.dumps({"trace_call": result}, ensure_ascii=False))
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(f"SparseRead tools were not loaded for trusted project: {inventory}\n" + "".join(diagnostics))
            time.sleep(0.25)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
