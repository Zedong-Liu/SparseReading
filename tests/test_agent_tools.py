from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from sparseread_agent_tools.runtime import HostBridge
from sparseread_opencode.bridge import OpenCodeBridge

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("host", ["codex", "pi"])
def test_host_core_parity_and_relative_paths(host, tmp_path):
    (tmp_path / "report.md").write_text("# Incident\n" + "Evidence about latency and errors.\n" * 2200)
    ours = HostBridge(host=host, workspace=tmp_path)
    baseline = OpenCodeBridge(workspace=tmp_path)
    request = {"method": "decide", "params": {"path": str(tmp_path / "report.md")}}
    expected = baseline.handle(request)
    actual = ours.handle({"method": "decide", "params": {"path": "report.md"}})
    assert actual["decision"] == expected["decision"]
    assert actual["host_gate"]["block_native_read"] == expected["opencode_gate"]["block_native_read"]
    assert actual["decision"]["mode"] == "force_sro"
    assert ours.handle({"method": "version"})["protocol_version"] == "1.0"


@pytest.mark.parametrize("host", ["codex", "pi"])
def test_preview_raw_and_context(host, tmp_path):
    (tmp_path / "report.md").write_text("# Latency\nThe failure started at noon.\n" * 2000)
    bridge = HostBridge(host=host, workspace=tmp_path)
    params = {"path": "report.md", "context": {"conversation_id": "first"}}
    result = bridge.handle({"method": "preview", "params": params})
    assert params["path"] == "report.md"  # caller's event object is untouched
    pack = result["preview_pack"]
    assert pack["raw_ref"]
    raw = bridge.handle({"method": "raw", "params": {"raw_ref": pack["raw_ref"], "context": {"conversation_id": "first"}}})
    assert "failure" in json.dumps(raw)
    with pytest.raises(ValueError, match="stale raw_ref"):
        bridge.handle({"method": "raw", "params": {"raw_ref": pack["raw_ref"], "context": {"conversation_id": "second"}}})


@pytest.mark.parametrize("path", ["../secret.txt", "/outside/secret.txt"])
def test_explicit_outside_paths_rejected(tmp_path, path):
    bridge = HostBridge(host="pi", workspace=tmp_path)
    with pytest.raises(ValueError, match="outside"):
        bridge.handle({"method": "preview", "params": {"path": path}})


@pytest.mark.parametrize("host", ["codex", "pi"])
def test_unicode_raw_range_and_stale_reference(host, tmp_path):
    text = "记录🙂第一条\n第二条：故障恢复\n"
    (tmp_path / "记录.md").write_text(text, encoding="utf-8")
    bridge = HostBridge(host=host, workspace=tmp_path)
    preview = bridge.handle({"method": "preview", "params": {"path": "记录.md"}})["preview_pack"]
    raw = bridge.handle({"method": "raw", "params": {"raw_ref": preview["raw_ref"], "range": {"start": 2, "end": 9}}})["raw"]
    assert raw["content"] == text[2:9]
    with pytest.raises(ValueError, match="stale raw_ref"):
        bridge.handle({"method": "raw", "params": {"raw_ref": "previous-session-ref"}})


def test_symlink_outside_and_target_normalization(tmp_path):
    bridge = HostBridge(host="codex", workspace=tmp_path)
    (tmp_path / "report.md").write_text("small report")
    result = bridge.handle({"method": "preview", "params": {"target": {"path": "report.md"}}})
    assert "preview_pack" in result
    if os.name != "nt":
        (tmp_path / "outside").symlink_to(tmp_path.parent, target_is_directory=True)
        with pytest.raises(ValueError, match="outside"):
            bridge.handle({"method": "decide", "params": {"path": "outside"}})


@pytest.mark.asyncio
async def test_real_stdio_mcp_preview_raw_and_errors(tmp_path):
    (tmp_path / "report.md").write_text("# Latency\nThe failure started at noon.\n" * 2000)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "packages/sparseread-core/src"), str(ROOT / "integrations/agent-tools/python/src")])
    server = StdioServerParameters(command=sys.executable, args=["-m", "sparseread_agent_tools.mcp", "--workspace", str(tmp_path)], env=env)
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            assert {tool.name for tool in (await client.list_tools()).tools} == {"sro_preview", "sro_read", "sro_raw", "sro_card", "sro_decide", "sro_trace"}
            result = await client.call_tool("sro_preview", {"path": "report.md"})
            assert not result.isError
            preview = json.loads(result.content[0].text)["preview_pack"]
            evidence = await client.call_tool("sro_read", {"target": {"artifact_id": preview["artifact_id"]}, "mode": "focus", "hint": {"goal": "Find when the failure started", "needles": ["failure"]}})
            assert not evidence.isError
            assert "noon" in evidence.content[0].text
            raw = await client.call_tool("sro_raw", {"raw_ref": preview["raw_ref"]})
            assert "failure" in raw.content[0].text
            outside = await client.call_tool("sro_preview", {"path": "../secret.txt"})
            assert outside.isError
            assert "native" in outside.content[0].text
            stale = await client.call_tool("sro_raw", {"raw_ref": "previous-session-ref"})
            assert stale.isError, "a stale reference must not be presented as successful evidence"
            assert "native" in stale.content[0].text
            invalid = await client.call_tool("sro_read", {"target": {"path": "report.md"}})
            assert invalid.isError


@pytest.mark.parametrize("host", ["codex", "pi"])
def test_targeted_incident_fields_and_native_compute_veto(host, tmp_path):
    report = tmp_path / "incident.md"
    report.write_text("# Incident\n" + "Routine telemetry.\n" * 2000 + "ROOT_CAUSE: cache invalidation used customer_id instead of tenant_id.\nMITIGATION_OWNER: Mira Chen.\nFINAL_DEADLINE: 2026-07-18 09:30 UTC.\n")
    bridge = HostBridge(host=host, workspace=tmp_path)
    preview = bridge.handle({"method": "preview", "params": {"path": "incident.md"}})["preview_pack"]
    evidence = bridge.handle({"method": "read", "params": {
        "target": {"artifact_id": preview["artifact_id"]}, "mode": "collect",
        "hint": {"goal": "Extract incident fields", "slots": [
            {"id": "root", "question": "What is ROOT_CAUSE?"},
            {"id": "owner", "question": "Who is MITIGATION_OWNER?"},
            {"id": "deadline", "question": "What is FINAL_DEADLINE?"},
        ]},
    }})["evidence_pack"]
    slots = {item["id"]: item for item in evidence["slot_digest"]["slots"]}
    assert slots["root"]["candidate"] == "cache invalidation used customer_id instead of tenant_id."
    assert slots["owner"]["candidate"] == "Mira Chen."
    assert slots["deadline"]["candidate"] == "2026-07-18 09:30 UTC."
    decision = HostBridge(host=host, workspace=tmp_path).handle({"method": "decide", "params": {
        "path": "incident.md", "episode_hint": {"goal": "full_fidelity", "coverage": "exhaustive"},
    }})
    assert decision["decision"]["mode"] == "native"
    assert not decision["host_gate"]["block_native_read"]
