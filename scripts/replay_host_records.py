#!/usr/bin/env python3
"""Deterministic real-host record replay; reports metrics, never record bodies.

Cases JSON: [{id,path,goal,needles,expected,route}]. Files stay local; no model
turn/API is used. Install the host into the case workspace before running.
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


class RPC:
    def __init__(self, command: list[str], cwd: Path):
        self.process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding="utf-8")
        self.messages: queue.Queue = queue.Queue()
        self.counter = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                self.messages.put(json.loads(line))
            except json.JSONDecodeError:
                pass
        self.messages.put({"error": "host exited"})

    def call(self, method, params, *, codex=False):
        self.counter += 1
        self.process.stdin.write(json.dumps({"id": self.counter, "method": method, "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 45
        while True:
            message = self.messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if codex and message.get("id") != self.counter and "error" not in message:
                continue
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            return message["result"]

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def replay_case(call, case, workspace):
    path = (workspace / case["path"]).resolve()
    if not path.is_relative_to(workspace):
        raise ValueError("case path must be inside workspace")
    source = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    expected = case["expected"]
    if not expected or not all(term in source for term in expected):
        raise ValueError("case expectations must exist in the local source")
    started = time.monotonic()
    decision = call("sro_decide", {"path": case["path"]})["decision"]["mode"]
    result = {"id": case["id"], "source_bytes": path.stat().st_size, "route": decision,
              "route_pass": decision == case["route"]}
    if decision == "native":
        # The host intentionally delegates unsupported/exact reads to native.
        result.update(evidence_pass=None, raw_pass=None, native_source_pass=True)
    else:
        preview = call("sro_preview", {"path": case["path"]})["preview_pack"]
        evidence = call("sro_read", {"target": {"artifact_id": preview["artifact_id"]}, "mode": "focus",
                                    "hint": {"goal": case["goal"], "needles": case["needles"]}})
        blocks = evidence.get("evidence_pack", {}).get("evidence", [])
        text = "\n".join(block.get("text", "") for block in blocks)
        initial_pass = all(term in text for term in expected)
        if case.get("followup_hint"):
            followup = call("sro_read", {"target": {"artifact_id": preview["artifact_id"]}, "mode": "refine",
                                        "hint": case["followup_hint"]})
            more = followup.get("evidence_pack", {}).get("evidence", [])
            blocks.extend(more)
            text += "\n" + "\n".join(block.get("text", "") for block in more)
        verified = []
        for term in expected:
            # Exact verification uses locally computed character ranges, not bytes.
            start = source.index(term)
            raw = call("sro_raw", {"raw_ref": preview["raw_ref"], "range": {"start": start, "end": start + len(term)}})["raw"]
            verified.append(raw.get("content") == term)
        result.update(initial_evidence_pass=initial_pass, evidence_pass=all(term in text for term in expected), raw_pass=all(verified),
                      evidence_chars=len(text), source_chars=len(source),
                      evidence_ratio=round(len(text) / max(1, len(source)), 4),
                      anchors_pass=bool(blocks) and all(block.get("anchor") for block in blocks))
    result["seconds"] = round(time.monotonic() - started, 3)
    return result


def codex_tools(client, workspace):
    client.call("initialize", {"clientInfo": {"name": "sparseread-record-replay", "version": "1"},
                               "capabilities": {"experimentalApi": True}}, codex=True)
    client.process.stdin.write('{"method":"initialized"}\n')
    client.process.stdin.flush()
    thread = client.call("thread/start", {"cwd": str(workspace), "ephemeral": True}, codex=True)
    thread_id = thread["thread"]["id"]
    deadline = time.monotonic() + 30
    while True:
        inventory = client.call("mcpServerStatus/list", {"threadId": thread_id}, codex=True)
        servers = [s for s in inventory.get("data", []) if "sparseread" in s.get("name", "")]
        if servers and servers[0].get("runtimeStatus") == "connected" and len(servers[0].get("tools", {})) >= 6:
            server = servers[0]["name"]
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Codex did not load six SparseRead tools; check project trust")
        time.sleep(0.25)

    def call(name, params):
        result = client.call("mcpServer/tool/call", {"threadId": thread_id, "server": server,
                             "tool": name, "arguments": params}, codex=True)
        if result.get("isError"):
            raise RuntimeError(result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])
    return call


def recovery_checks(client, call, host, workspace, cases):
    candidate = next(case["path"] for case in cases if case["route"] == "force_sro")
    checks = {}
    preview = call("sro_preview", {"path": candidate})["preview_pack"]
    exact = call("sro_decide", {"path": candidate, "episode_hint": {
        "relation": "switch", "goal": "full_fidelity", "coverage": "exhaustive"}})
    checks["full_fidelity_native"] = exact["decision"]["mode"] == "native"
    fresh_task = call("sro_decide", {"path": candidate, "episode_hint": {
        "relation": "new", "goal": "selective_read", "coverage": "selective"}})
    checks["new_task_restores_force"] = fresh_task["decision"]["mode"] == "force_sro"
    if host == "pi":
        first = client.call("native_read", {"path": candidate})
        checks["broad_redirect_once"] = bool(first and first.get("block")) and client.call("native_read", {"path": candidate}) is None
        checks["bounded_native"] = client.call("native_read", {"path": candidate, "limit": 10}) is None
        checks["native_tool_executes"] = client.call("native_execute", {"path": candidate, "limit": 10})["content_chars"] > 0
        unsupported = next(case["path"] for case in cases if case["path"].endswith(".jsonl"))
        checks["unsupported_native"] = client.call("native_read", {"path": unsupported}) is None
        checks["jsonl_native_executes"] = client.call("native_execute", {"path": unsupported, "limit": 1})["content_chars"] > 0
        checks["outside_native_fallback"] = client.call("native_read", {"path": "../outside.md"}) is None
        client.call("reset", {})
        checks["session_reset_empty"] = not call("sro_trace", {}).get("artifacts")
        try:
            call("sro_raw", {"raw_ref": preview["raw_ref"]})
            checks["old_session_ref_rejected"] = False
        except RuntimeError as exc:
            checks["old_session_ref_rejected"] = "stale raw_ref" in str(exc)
        fresh = call("sro_preview", {"path": candidate})["preview_pack"]
        checks["refresh_after_reset"] = bool(call("sro_raw", {"raw_ref": fresh["raw_ref"], "range": {"start": 0, "end": 10}})["raw"].get("content"))
    else:
        launcher = workspace / ".sparseread/codex/sparseread-codex/scripts/launcher.mjs"
        def hook(command):
            event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                     "cwd": str(workspace), "session_id": f"record-replay-{client.process.pid}"}
            env = dict(os.environ, PLUGIN_DATA=str(workspace / ".replay-hook-data"))
            output = subprocess.run(["node", str(launcher), "--hook"], input=json.dumps(event),
                                    capture_output=True, text=True, encoding="utf-8", env=env, timeout=10, check=True)
            return json.loads(output.stdout or "{}")
        first = hook(f"cat {json.dumps(candidate, ensure_ascii=False)}")
        checks["broad_redirect_once"] = first.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" and not hook(f"cat {json.dumps(candidate, ensure_ascii=False)}")
        checks["bounded_native"] = not hook(f"head -n 10 {json.dumps(candidate, ensure_ascii=False)}")
        checks["outside_native_fallback"] = not hook("cat ../outside.md")
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=["codex", "pi"], required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--pi-sdk", type=Path, default=Path(__file__).resolve().parents[1] / "integrations/pi/package")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    command = [args.codex, "app-server", "--stdio"] if args.host == "codex" else [
        "node", str(Path(__file__).with_name("replay_pi_records.mjs")), str(workspace),
        str(args.pi_sdk.resolve()), str(workspace / ".replay-pi-agent")]
    client = RPC(command, workspace)
    try:
        call = codex_tools(client, workspace) if args.host == "codex" else client.call
        cases = json.loads(args.cases.read_text())
        results = [replay_case(call, case, workspace) for case in cases]
        trace = call("sro_trace", {})
        checks = {"trace_events": len(trace.get("events", [])), "trace_artifacts": len(trace.get("artifacts", []))}
        try:
            call("sro_raw", {"raw_ref": "stale-ref-from-previous-session"})
            checks["stale_ref_error"] = False
        except RuntimeError as exc:
            checks["stale_ref_error"] = "stale raw_ref" in str(exc)
        checks.update(recovery_checks(client, call, args.host, workspace, cases))
        report = {"host": args.host, "kind": "deterministic-real-host-replay-no-model",
                  "cases": results, "checks": checks}
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        passed = all(r["route_pass"] and r.get("evidence_pass") is not False and r.get("raw_pass") is not False and r.get("anchors_pass") is not False for r in results)
        return 0 if passed and all(bool(value) for value in checks.values()) else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
