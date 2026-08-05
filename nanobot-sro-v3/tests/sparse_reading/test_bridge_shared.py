from __future__ import annotations

from pathlib import Path

from sparseread_openclaw.bridge import OpenClawBridge
from sparseread_opencode.bridge import OpenCodeBridge


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
    for index in range(4):
        (assets / f"reference-{index}.txt").write_text("Supporting evidence.\n" * 40, encoding="utf-8")
    return assets


def _write_audit_bundle(root: Path) -> Path:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "fetcher.py").write_text(
        "def deduplicate(seen):\n    return list(seen)[-5000:]\n" + "# audit evidence\n" * 600,
        encoding="utf-8",
    )
    (assets / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (assets / "output.json").write_text('[{"id":"a"}]\n', encoding="utf-8")
    (assets / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (assets / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
    return assets


def _write_mixed_evidence_collection(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "source.py").write_text("def load():\n    return True\n" + "# evidence\n" * 900, encoding="utf-8")
    (root / "state.json").write_text('{"cursor":"42"}\n' * 80, encoding="utf-8")
    (root / "config.yaml").write_text("enabled: true\n" * 80, encoding="utf-8")
    (root / "events.log").write_text("INFO completed\n" * 80, encoding="utf-8")
    (root / "notes.md").write_text("Supporting observation.\n" * 80, encoding="utf-8")
    return root


def _write_structured_analysis_collection(root: Path) -> Path:
    root.mkdir(parents=True)
    rows = "id,value\n" + "".join(f"{index},{index * 2}\n" for index in range(1_200))
    for index in range(5):
        (root / f"table-{index}.csv").write_text(rows, encoding="utf-8")
    return root


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


def test_shared_bridge_normalizes_common_natural_hint_values(tmp_path: Path) -> None:
    target = tmp_path / "incident-report.md"
    target.write_text(
        "# Incident Report\n\n"
        "ROOT_CAUSE: cache invalidation used customer_id instead of tenant_id.\n",
        encoding="utf-8",
    )
    bridge = OpenCodeBridge(workspace=tmp_path, mode="force")
    preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})

    result = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": preview["preview_pack"]["artifact_id"]},
                "mode": "focus",
                "hint": {
                    "goal": "Find ROOT_CAUSE value",
                    "needles": ["ROOT_CAUSE"],
                    "want": "The value assigned to ROOT_CAUSE",
                    "scope": "entire file",
                    "type_hint": "key-value assignment",
                },
            },
        }
    )

    pack = result["evidence_pack"]
    text = "\n".join(block["text"] for block in pack["evidence"])
    assert pack["error"] == ""
    assert "ROOT_CAUSE: cache invalidation" in text


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

    preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
    artifact_id = preview["preview_pack"]["artifact_id"]
    raw_ref = preview["preview_pack"]["raw_ref"]
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
    raw = bridge.handle({"method": "raw", "params": {"raw_ref": raw_ref, "selector": "registry"}})
    trace = bridge.handle({"method": "trace", "params": {}})

    assert first["evidence_pack"]["slot_digest"]["overall_status"] == "ready"
    assert raw["raw"]["next_action"]["guard"] == "openclaw_adapter_closure_once"
    assert raw["raw"]["protocol_next"] == "write_file_now"
    assert second["evidence_pack"]["protocol_next"] == "write_file_now"
    assert trace["summary"]["adapter_guard_hits"] == 2


def test_openclaw_ready_guard_allows_new_slots_after_partial_ready(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text(
        "ROOT_CAUSE: cache invalidation used customer_id instead of tenant_id.\n"
        "MITIGATION_OWNER: Mira Chen, Data Platform on-call.\n"
        "FINAL_DEADLINE: 2026-07-18 09:30 UTC.\n",
        encoding="utf-8",
    )
    bridge = OpenClawBridge(workspace=tmp_path, mode="force")

    preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
    artifact_id = preview["preview_pack"]["artifact_id"]
    raw_ref = preview["preview_pack"]["raw_ref"]
    first = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "find root cause",
                    "type_hint": "text",
                    "needles": ["ROOT_CAUSE"],
                    "slots": [{"id": "root_cause", "question": "What is ROOT_CAUSE?"}],
                },
            },
        }
    )
    second = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "find mitigation owner",
                    "type_hint": "text",
                    "needles": ["MITIGATION_OWNER"],
                    "slots": [{"id": "owner", "question": "What is MITIGATION_OWNER?"}],
                },
            },
        }
    )
    repeat = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "repeat root cause",
                    "type_hint": "text",
                    "needles": ["ROOT_CAUSE"],
                    "slots": [{"id": "root_cause", "question": "What is ROOT_CAUSE?"}],
                },
            },
        }
    )
    raw_new_selector = bridge.handle({"method": "raw", "params": {"raw_ref": raw_ref, "selector": "FINAL_DEADLINE"}})

    assert first["evidence_pack"]["slot_digest"]["overall_status"] == "ready"
    assert first["evidence_pack"]["slot_digest"]["resolved_slot_ids"] == ["root_cause"]
    assert second["evidence_pack"]["summary"] != "adapter ready guard: evidence is already ready from the prior read; write the deliverable now"
    assert second["evidence_pack"]["slot_digest"]["resolved_slot_ids"] == ["owner"]
    assert repeat["evidence_pack"]["next_action"]["guard"] == "openclaw_adapter_closure_once"
    assert raw_new_selector["raw"]["matches"][0]["text"].startswith("FINAL_DEADLINE")


def test_openclaw_preview_collection_ready_guard_stops_repeat_reads(tmp_path: Path) -> None:
    assets = _write_command_security_bundle(tmp_path / "command")
    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")

    preview = bridge.handle({"method": "preview", "params": {"path": str(assets)}})
    artifact_id = preview["preview_pack"]["artifact_id"]
    raw_ref = preview["preview_pack"]["raw_ref"]
    raw = bridge.handle({"method": "raw", "params": {"raw_ref": raw_ref, "selector": "security_policy.yaml"}})
    first = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "command prefix security analysis",
                    "want": "fact",
                    "type_hint": "collection",
                    "slots": [
                        {"id": "pipeline_commands", "question": "unsafe commands"},
                        {"id": "policy_conflicts", "question": "policy conflicts"},
                        {"id": "deliverables", "question": "required deliverables"},
                    ],
                },
            },
        }
    )
    decision = bridge.handle({"method": "decide", "params": {"path": str(assets / "security_policy.yaml")}})
    second = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "repeat command prefix security analysis",
                    "want": "fact",
                    "type_hint": "collection",
                },
            },
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})

    assert raw["raw"]["type"] == "collection_child"
    assert "deny_patterns" in raw["raw"]["content"]
    assert first["evidence_pack"]["next_action"]["overall_status"] == "ready"
    assert decision["openclaw_gate"]["protocol_next"] == "write_file_now"
    assert second["evidence_pack"]["protocol_next"] == "write_file_now"
    assert trace["summary"]["adapter_guard_hits"] == 1


def test_audit_collection_ready_guard_stops_new_detail_reads(tmp_path: Path) -> None:
    assets = _write_audit_bundle(tmp_path / "audit")

    for bridge_cls in (OpenCodeBridge, OpenClawBridge):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        preview = bridge.handle({"method": "preview", "params": {"path": str(assets)}})
        artifact_id = preview["preview_pack"]["artifact_id"]
        first = bridge.handle(
            {
                "method": "read",
                "params": {
                    "target": {"artifact_id": artifact_id},
                    "mode": "collect",
                    "hint": {
                        "goal": "audit the fetcher data integrity path",
                        "want": "fact",
                        "type_hint": "collection",
                        "slots": [
                            {"id": "state_vs_output", "question": "state vs output consistency"},
                            {"id": "missing_csv", "question": "missing csv summary"},
                            {"id": "dedup_bug", "question": "deduplication bug and fix"},
                        ],
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
                    "hint": {
                        "goal": "read run_scheduled_fetch.py again for exact code",
                        "want": "verbatim",
                        "type_hint": "python",
                        "needles": ["deduplicate", "list(seen)", "save_csv_summary"],
                    },
                },
            }
        )

        assert first["evidence_pack"]["next_action"]["overall_status"] == "ready"
        assert second["evidence_pack"]["protocol_next"] == "write_file_now"
        assert second["evidence_pack"]["summary"].startswith("adapter ready guard")


def test_adapter_gates_preserve_t86_advisory_and_audit_enforce(tmp_path: Path) -> None:
    command_assets = _write_command_security_bundle(tmp_path / "command")
    audit_assets = _write_audit_bundle(tmp_path / "audit")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        command_bridge = bridge_cls(workspace=tmp_path, mode="auto")
        command_decision = command_bridge.handle(
            {
                "method": "decide",
                "params": {
                    "path": str(command_assets),
                    "episode_hint": {
                        "goal": "cross_file_evidence",
                        "coverage": "exhaustive",
                        "relation": "new",
                    },
                },
            }
        )

        assert command_decision["decision"]["mode"] == "advisory"
        assert command_decision[gate_key]["mode"] == "advisory"
        assert command_decision[gate_key]["decision_code"] == "broad_evidence_boundary"
        assert command_decision[gate_key]["block_native_read"] is False

        audit_bridge = bridge_cls(workspace=tmp_path, mode="auto")
        params = {
            "episode_hint": {
                "goal": "cross_file_evidence",
                "coverage": "selective",
                "relation": "new",
            }
        }
        audit_decision = audit_bridge.handle(
            {"method": "decide", "params": {"path": str(audit_assets), **params}}
        )
        child_decision = audit_bridge.handle(
            {"method": "decide", "params": {"path": str(audit_assets / "fetcher.py"), **params}}
        )

        assert audit_decision[gate_key]["mode"] == "enforce"
        assert audit_decision[gate_key]["block_native_read"] is True
        assert audit_decision[gate_key]["block_native_search"] is True
        assert audit_decision[gate_key]["block_native_exec_dump"] is True
        assert child_decision[gate_key]["mode"] == "enforce"
        assert child_decision[gate_key]["handoff_path"] == str(audit_assets)


def test_bridge_preflight_reports_force_sro_first_action(tmp_path: Path) -> None:
    audit = tmp_path / "a_stock_announcements"
    audit.mkdir()
    (audit / "fetcher.py").write_text(
        "def deduplicate(seen):\n    return list(seen)[-5000:]\n" + "# audit evidence\n" * 600,
        encoding="utf-8",
    )
    (audit / "fetch_state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (audit / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (audit / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
    (tmp_path / "fetch-audit.md").write_text("generated output\n", encoding="utf-8")

    for bridge_cls in (OpenCodeBridge, OpenClawBridge):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        preflight = bridge.handle(
            {
                "method": "preflight",
                "params": {
                    "max_candidates": 8,
                    "episode_hint": {"goal": "cross_file_evidence", "coverage": "selective"},
                },
            }
        )

        assert preflight["handoff_count"] == 1
        assert preflight["handoffs"][0]["relative_path"] == "a_stock_announcements"
        assert preflight["handoffs"][0]["gate_mode"] == "enforce"
        assert preflight["handoffs"][0]["trajectory"] == "sro_first"
        assert preflight["first_action"]["tool"] == "sro_preview"
        assert preflight["first_action"]["path"] == "a_stock_announcements"


def test_bridge_preflight_prefers_specific_target_over_root_handoff(tmp_path: Path) -> None:
    audit = tmp_path / "a_stock_announcements"
    audit.mkdir()
    (audit / "fetcher.py").write_text(
        "def deduplicate(seen):\n    return list(seen)[-5000:]\n" + "# audit evidence\n" * 600,
        encoding="utf-8",
    )
    (audit / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (audit / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (audit / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
    (tmp_path / "fetcher.py").write_text("def fetch():\n    pass\n", encoding="utf-8")
    (tmp_path / "fetch_state.json").write_text('{"cursor":"2026-02-09"}\n', encoding="utf-8")

    for bridge_cls in (OpenCodeBridge, OpenClawBridge):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        preflight = bridge.handle(
            {
                "method": "preflight",
                "params": {
                    "max_candidates": 12,
                    "episode_hint": {"goal": "cross_file_evidence", "coverage": "selective"},
                },
            }
        )

        assert preflight["handoff_count"] == 1
        assert [item["relative_path"] for item in preflight["handoffs"]] == ["a_stock_announcements"]
        assert preflight["first_action"]["path"] == "a_stock_announcements"


def test_generated_outputs_stay_native_in_both_bridges(tmp_path: Path) -> None:
    output = tmp_path / "generated-report.md"
    output.write_text("audit report\n" * 600, encoding="utf-8")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        bridge.handle(
            {
                "method": "native_event",
                "params": {
                    "phase": "after",
                    "tool": "write_file",
                    "params": {"path": str(output)},
                },
            }
        )
        decision = bridge.handle({"method": "decide", "params": {"path": str(output)}})
        card = bridge.handle({"method": "card", "params": {"path": str(output)}})

        assert decision[gate_key]["mode"] == "native"
        assert decision[gate_key]["block_native_read"] is False
        assert card["file_card"]["sparse_recommended"] is False
        assert card[gate_key]["mode"] == "native"


def test_bridge_and_runtime_share_episode_decision_for_card_and_read(tmp_path: Path) -> None:
    structured = _write_structured_analysis_collection(tmp_path / "structured")
    report = tmp_path / "complete-report.md"
    report.write_text("Full-fidelity source material.\n" * 500, encoding="utf-8")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        assert bridge.episodes is bridge.runtime.orchestrator.episodes

        context = {"conversation_id": "force-scene", "turn_id": "turn-1"}
        card = bridge.handle(
            {
                "method": "card",
                "params": {
                    "path": str(structured),
                    "context": context,
                    "episode_hint": {
                        "goal": "edit_or_execute",
                        "relation": "new",
                        "coverage": "selective",
                        "summary": "plan a bounded multi-table analysis before writing code",
                    },
                },
            }
        )

        assert card[gate_key]["mode"] == "enforce"
        assert card["file_card"]["sparse_recommended"] is True
        assert card["file_card"]["recommended_mode"] == "collect"
        assert card["next_action"]["mode"] == "collect"

        native_bridge = bridge_cls(workspace=tmp_path, mode="auto")
        native_context = {"conversation_id": "native-scene", "turn_id": "turn-1"}
        native_card = native_bridge.handle(
            {
                "method": "card",
                "params": {
                    "path": str(report),
                    "context": native_context,
                    "episode_hint": {
                        "goal": "full_fidelity",
                        "relation": "new",
                        "coverage": "exhaustive",
                    },
                },
            }
        )
        native_read = native_bridge.handle(
            {
                "method": "read",
                "params": {
                    "target": {"artifact_id": native_card["file_card"]["artifact_id"]},
                    "mode": "scout",
                    "hint": {"goal": "read the complete source", "type_hint": "text"},
                    "context": native_context,
                    "episode_hint": {
                        "goal": "full_fidelity",
                        "relation": "continue",
                        "coverage": "exhaustive",
                    },
                },
            }
        )

        assert native_card[gate_key]["mode"] == "native"
        assert native_read["evidence_pack"]["summary"].startswith("low-sparse fallback")


def test_active_force_collection_scope_remains_child_handoff_without_repeated_hint(tmp_path: Path) -> None:
    collection = _write_mixed_evidence_collection(tmp_path / "evidence")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        context = {"conversation_id": "scene", "turn_id": "turn-1"}
        root = bridge.handle(
            {
                "method": "decide",
                "params": {
                    "path": str(collection),
                    "context": context,
                    "episode_hint": {
                        "goal": "cross_file_evidence",
                        "relation": "new",
                        "coverage": "selective",
                    },
                },
            }
        )
        child = bridge.handle(
            {
                "method": "decide",
                "params": {
                    "path": str(collection / "source.py"),
                    "context": context,
                },
            }
        )

        assert root[gate_key]["mode"] == "enforce"
        assert child["decision"]["mode"] == "force_sro"
        assert child[gate_key]["mode"] == "enforce"
        assert child[gate_key]["handoff_path"] == str(collection)


def test_runtime_native_passthrough_wins_over_ready_collection_guard(tmp_path: Path) -> None:
    collection = _write_mixed_evidence_collection(tmp_path / "evidence")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        context = {"conversation_id": "scene", "turn_id": "turn-1"}
        preview = bridge.handle(
            {
                "method": "preview",
                "params": {
                    "path": str(collection),
                    "context": context,
                    "episode_hint": {
                        "goal": "cross_file_evidence",
                        "relation": "new",
                        "coverage": "selective",
                    },
                },
            }
        )
        artifact_id = preview["preview_pack"]["artifact_id"]
        bridge._remember_adapter_ready_pack(
            {
                "artifact_id": artifact_id,
                "type": "collection",
                "slot_digest": {"overall_status": "ready", "slots": []},
                "evidence": [],
                "next_action": {"instruction": "write the requested output"},
            },
            conversation_id="scene",
        )
        generated = collection / ".sparseread" / "generated.md"
        generated.parent.mkdir(exist_ok=True)
        generated.write_text("generated result\n" * 500, encoding="utf-8")

        decision = bridge.handle(
            {"method": "decide", "params": {"path": str(generated), "context": context}}
        )
        card = bridge.handle(
            {"method": "card", "params": {"path": str(generated), "context": context}}
        )

        assert decision[gate_key]["mode"] == "native"
        assert decision[gate_key]["block_native_read"] is False
        assert decision[gate_key].get("already_ready") is not True
        assert card[gate_key]["mode"] == "native"
        assert card["file_card"]["sparse_recommended"] is False
        assert card.get("adapter_guard") is None


def test_ready_for_compute_is_terminal_but_recorded_outputs_stay_native(tmp_path: Path) -> None:
    collection = _write_structured_analysis_collection(tmp_path / "structured")

    for bridge_cls, gate_key in ((OpenCodeBridge, "opencode_gate"), (OpenClawBridge, "openclaw_gate")):
        bridge = bridge_cls(workspace=tmp_path, mode="auto")
        context = {"conversation_id": "compute", "turn_id": "turn-1"}
        card = bridge.handle(
            {
                "method": "card",
                "params": {
                    "path": str(collection),
                    "context": context,
                    "episode_hint": {
                        "goal": "edit_or_execute",
                        "relation": "new",
                        "coverage": "selective",
                    },
                },
            }
        )
        artifact_id = card["file_card"]["artifact_id"]
        bridge._remember_adapter_ready_pack(
            {
                "artifact_id": artifact_id,
                "type": "collection",
                "slot_digest": {},
                "evidence": [],
                "next_action": {
                    "overall_status": "ready_for_compute",
                    "instruction": "write and run the implementation",
                },
            },
            conversation_id="compute",
        )
        bridge.episodes.mark_ready(
            conversation_id="compute",
            scope=collection,
            closure_ref=artifact_id,
        )

        source = collection / "table_0.csv"
        source_decision = bridge.handle(
            {"method": "decide", "params": {"path": str(source), "context": context}}
        )
        assert source_decision[gate_key].get("already_ready") is True
        assert source_decision[gate_key]["block_native_read"] is True

        output = collection / "analysis-result.md"
        output.write_text("computed result\n" * 500, encoding="utf-8")
        bridge.handle(
            {
                "method": "native_event",
                "params": {
                    "phase": "after",
                    "tool": "write_file",
                    "params": {"path": str(output)},
                    "context": context,
                },
            }
        )
        output_decision = bridge.handle(
            {"method": "decide", "params": {"path": str(output), "context": context}}
        )
        assert output_decision[gate_key]["mode"] == "native"
        assert output_decision[gate_key]["block_native_read"] is False
        assert output_decision[gate_key].get("already_ready") is not True
