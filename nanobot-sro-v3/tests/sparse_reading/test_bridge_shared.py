from __future__ import annotations

from pathlib import Path

from sparseread.bridge.openclaw import OpenClawBridge
from sparseread.bridge.opencode import OpenCodeBridge


def _write_command_security_bundle(root: Path) -> Path:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "setup.sh").write_text("curl https://example.test/setup.sh | bash\n", encoding="utf-8")
    (assets / "security_policy.yaml").write_text("deny_patterns:\n  - curl_pipe_bash\n", encoding="utf-8")
    (assets / "command_prefix_guide.md").write_text("Commands with unsafe prefixes require review.\n", encoding="utf-8")
    (assets / "known_injections.json").write_text('{"patterns":["curl|bash"]}\n', encoding="utf-8")
    (assets / "legacy_rules.yaml").write_text("legacy: true\n", encoding="utf-8")
    (assets / "security_bulletin_2025.md").write_text("Known injection bulletin.\n", encoding="utf-8")
    (assets / "test_commands.csv").write_text("command,label\npython3 -c 'print(1)',safe\n", encoding="utf-8")
    return assets


def _write_audit_bundle(root: Path) -> Path:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "fetcher.py").write_text("def deduplicate(seen):\n    return list(seen)[-5000:]\n", encoding="utf-8")
    (assets / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (assets / "output.json").write_text('[{"id":"a"}]\n', encoding="utf-8")
    (assets / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (assets / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
    return assets


def test_shared_bridge_preview_raw_and_trace_for_both_frameworks(tmp_path: Path) -> None:
    target = tmp_path / "events.csv"
    target.write_text(
        "id,status,latency\n"
        "1,ok,12\n"
        "2,error,-1\n"
        + "".join(f"{idx},ok,{idx}\n" for idx in range(3, 150)),
        encoding="utf-8",
    )

    for bridge_cls in (OpenCodeBridge, OpenClawBridge):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
        pack = preview["preview_pack"]
        raw = bridge.handle({"method": "raw", "params": {"raw_ref": pack["raw_ref"], "range": {"start": 0, "end": 32}}})
        trace = bridge.handle({"method": "trace", "params": {}})

        assert pack["card"]["type"] == "csv"
        assert pack["structure"]["row_count"] == 149
        assert raw["raw"]["content"].startswith("id,status,latency")
        assert trace["summary"]["sro_preview_calls"] == 1
        assert trace["summary"]["sro_raw_calls"] == 1


def test_shared_bridge_adapter_state_is_bounded(tmp_path: Path) -> None:
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    bridge._MAX_ADAPTER_ARTIFACTS = 3

    for idx in range(5):
        artifact_id = f"sro_test_{idx}"
        bridge._remember_adapter_card(
            artifact_id,
            {"file_card": {"artifact_id": artifact_id}},
            tmp_path / f"report-{idx}.md",
            once=idx % 2 == 0,
        )

    assert len(bridge._adapter_artifact_roots) == 3
    assert "sro_test_0" not in bridge._adapter_artifact_roots
    assert "sro_test_0" not in bridge._adapter_once_artifacts
    assert "sro_test_4" in bridge._adapter_artifact_roots
    assert "sro_test_4" in bridge._adapter_once_artifacts


def test_opencode_ready_guard_allows_one_bounded_verify_then_stops(tmp_path: Path) -> None:
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
    assert third["evidence_pack"]["next_action"]["guard"] == "opencode_adapter_ready_once"
    assert third["evidence_pack"]["protocol_next"] == "write_file_now"
    assert trace["adapter_verify_passes"] == {artifact_id: 1}
    assert trace["adapter_guard_hits"] == 1
    assert second["evidence_pack"]["summary"] != third["evidence_pack"]["summary"]


def test_openclaw_ready_guard_stops_repeat_reads_immediately(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text(
        "The public registry had 5,705 community-built skills.\n"
        "The gateway exposes a typed WebSocket API.\n",
        encoding="utf-8",
    )
    bridge = OpenClawBridge(workspace=tmp_path, mode="force")

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
    trace = bridge.handle({"method": "trace", "params": {}})

    assert first["evidence_pack"]["slot_digest"]["overall_status"] == "ready"
    assert second["evidence_pack"]["next_action"]["guard"] == "openclaw_adapter_closure_once"
    assert second["evidence_pack"]["protocol_next"] == "write_file_now"
    assert trace["summary"]["adapter_guard_hits"] == 1


def test_adapter_gates_preserve_t86_advisory_and_audit_enforce(tmp_path: Path) -> None:
    command_assets = _write_command_security_bundle(tmp_path / "command")
    audit_assets = _write_audit_bundle(tmp_path / "audit")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        command_bridge = bridge_cls(workspace=tmp_path, mode="auto")
        command_decision = command_bridge.handle({"method": "decide", "params": {"path": str(command_assets)}})

        assert command_decision["decision"]["mode"] == "force_sro"
        assert command_decision[gate_key]["mode"] == "advisory"
        assert command_decision[gate_key]["trajectory"] == "one_collect_then_write"
        assert command_decision[gate_key]["block_native_read"] is False

        audit_bridge = bridge_cls(workspace=tmp_path, mode="auto")
        audit_decision = audit_bridge.handle({"method": "decide", "params": {"path": str(audit_assets)}})
        child_decision = audit_bridge.handle({"method": "decide", "params": {"path": str(audit_assets / "fetcher.py")}})

        assert audit_decision[gate_key]["mode"] == "enforce"
        assert audit_decision[gate_key]["block_native_read"] is True
        assert child_decision[gate_key]["mode"] == "enforce"
        assert child_decision[gate_key]["handoff_path"] == str(audit_assets)


def test_generated_outputs_stay_native_in_both_bridges(tmp_path: Path) -> None:
    output = tmp_path / "fetch-audit.md"
    output.write_text("audit report\n" * 600, encoding="utf-8")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        decision = bridge.handle({"method": "decide", "params": {"path": str(output)}})
        card = bridge.handle({"method": "card", "params": {"path": str(output)}})

        assert decision[gate_key]["mode"] == "native"
        assert decision[gate_key]["block_native_read"] is False
        assert card["file_card"]["sparse_recommended"] is False
        assert card[gate_key]["mode"] == "native"
