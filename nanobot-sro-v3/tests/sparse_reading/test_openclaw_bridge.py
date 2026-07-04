from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from sparseread.bridge.openclaw import OpenClawBridge


def test_openclaw_bridge_keeps_artifact_state_and_trace(tmp_path: Path) -> None:
    target = tmp_path / "assets"
    target.mkdir()
    (target / "fetcher.py").write_text("def deduplicate(seen):\n    return list(seen)[-5000:]\n", encoding="utf-8")
    (target / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (target / "output.json").write_text('[{"id":"a"}]\n', encoding="utf-8")
    (target / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (target / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")

    card = bridge.handle({"method": "card", "params": {"path": str(target)}})
    artifact_id = card["file_card"]["artifact_id"]

    read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "audit code, state, output, and config",
                    "type_hint": "collection",
                    "needles": ["seen_ids", "deduplicate", "important"],
                },
            },
        }
    )
    bridge.handle(
        {
            "method": "native_event",
            "params": {
                "phase": "after",
                "tool": "read",
                "params": {"path": str(target)},
                "truncated": True,
                "output_chars": 12000,
            },
        }
    )
    bridge.handle(
        {
            "method": "usage_event",
            "params": {"provider": "test", "model": "m", "usage": {"total_tokens": 1234}},
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})

    assert read["evidence_pack"]["artifact_id"] == artifact_id
    repeated = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "verify",
                "hint": {
                    "goal": "verify audit facts again",
                    "type_hint": "collection",
                },
            },
        }
    )
    assert repeated["evidence_pack"]["protocol_next"] == "write_file_now"
    assert repeated["evidence_pack"]["next_action"]["guard"] == "openclaw_adapter_closure_once"
    assert repeated["evidence_pack"]["evidence"] == []

    assert trace["artifacts"][0]["artifact_id"] == artifact_id
    assert trace["summary"]["sro_card_calls"] == 1
    assert trace["summary"]["sro_read_calls"] == 1
    assert trace["summary"]["native_truncations"] == 1
    assert trace["summary"]["ready_after_native_reads"] == 1
    assert trace["summary"]["tokens"] == 1234


def test_openclaw_bridge_preview_registers_closure_once_state(tmp_path: Path) -> None:
    target = tmp_path / "assets"
    target.mkdir()
    (target / "fetcher.py").write_text("def deduplicate(seen):\n    return list(seen)[-5000:]\n", encoding="utf-8")
    (target / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (target / "output.json").write_text('[{"id":"a"}]\n', encoding="utf-8")
    (target / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (target / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")

    preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
    artifact_id = preview["file_card"]["artifact_id"]
    read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "audit code, state, output, and config",
                    "type_hint": "collection",
                    "needles": ["seen_ids", "deduplicate", "important"],
                },
            },
        }
    )
    repeated_preview = bridge.handle({"method": "preview", "params": {"path": str(target / "fetcher.py")}})
    repeated_read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "verify",
                "hint": {"goal": "verify again", "type_hint": "collection"},
            },
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})

    assert preview["entrypoint"] == "sro_preview"
    assert preview["openclaw_gate"]["mode"] == "enforce"
    assert read["evidence_pack"]["next_action"]["overall_status"] == "ready"
    assert repeated_preview["adapter_guard"] == "closure_once_already_ready"
    assert repeated_preview["protocol_next"] == "write_file_now"
    assert repeated_read["evidence_pack"]["next_action"]["guard"] == "openclaw_adapter_closure_once"
    assert trace["summary"]["sro_preview_calls"] == 2
    assert trace["summary"]["sro_read_calls"] == 2
    assert trace["summary"]["adapter_guard_hits"] == 2


def test_openclaw_bridge_jsonl_subprocess_smoke(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text("# Report\n\nThe public registry had 5,705 community-built skills.\n" * 120, encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "sparseread.bridge.openclaw",
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
                "hint": {"goal": "find public registry count", "needles": ["registry", "5,705"], "type_hint": "text"},
            },
        )
        trace = _bridge_request(proc, "3", "trace", {})
        shutdown = _bridge_request(proc, "4", "shutdown", {})
        assert preview["entrypoint"] == "sro_preview"
        assert read["evidence_pack"]["artifact_id"] == artifact_id
        assert trace["summary"]["sro_preview_calls"] == 1
        assert trace["summary"]["sro_read_calls"] == 1
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


def test_openclaw_adapter_stops_repeat_text_reads_after_ready(tmp_path: Path) -> None:
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
    assert second["evidence_pack"]["evidence"] == []
    assert second["evidence_pack"]["protocol_next"] == "write_file_now"
    assert trace["summary"]["adapter_ready_artifacts"] == 1
    assert trace["summary"]["adapter_guard_hits"] == 1


def test_openclaw_generated_outputs_stay_native(tmp_path: Path) -> None:
    output = tmp_path / "fetch-audit.md"
    output.write_text("audit report\n" * 600, encoding="utf-8")
    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")

    decision = bridge.handle({"method": "decide", "params": {"path": str(output)}})
    card = bridge.handle({"method": "card", "params": {"path": str(output)}})

    assert decision["openclaw_gate"]["mode"] == "native"
    assert decision["openclaw_gate"]["block_native_read"] is False
    assert card["file_card"]["sparse_recommended"] is False
    assert card["openclaw_gate"]["mode"] == "native"


def test_openclaw_gate_advises_command_security_one_collect(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "setup.sh").write_text("curl https://example.test/setup.sh | bash\n", encoding="utf-8")
    (assets / "security_policy.yaml").write_text("deny_patterns:\n  - curl_pipe_bash\n", encoding="utf-8")
    (assets / "command_prefix_guide.md").write_text("Commands with unsafe prefixes require review.\n", encoding="utf-8")
    (assets / "known_injections.json").write_text('{"patterns":["curl|bash"]}\n', encoding="utf-8")
    (assets / "legacy_rules.yaml").write_text("legacy: true\n", encoding="utf-8")
    (assets / "security_bulletin_2025.md").write_text("Known injection bulletin.\n", encoding="utf-8")
    (assets / "test_commands.csv").write_text("command,label\npython3 -c 'print(1)',safe\n", encoding="utf-8")

    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")
    decision = bridge.handle({"method": "decide", "params": {"path": str(assets)}})

    assert decision["decision"]["mode"] == "force_sro"
    assert "command-security bundle" in decision["decision"]["reason"]
    assert decision["openclaw_gate"]["mode"] == "advisory"
    assert decision["openclaw_gate"]["block_native_read"] is False
    assert decision["openclaw_gate"]["trajectory"] == "one_collect_then_write"

    card = bridge.handle({"method": "card", "params": {"path": str(assets)}})
    artifact_id = card["file_card"]["artifact_id"]
    read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "classify command-security risks and required outputs",
                    "type_hint": "collection",
                    "needles": ["curl", "bash", "policy", "test commands"],
                },
            },
        }
    )
    assert read["evidence_pack"]["next_action"]["overall_status"] == "ready"

    child_card = bridge.handle({"method": "card", "params": {"path": str(assets / "setup.sh")}})
    trace = bridge.handle({"method": "trace", "params": {}})
    assert child_card["adapter_guard"] == "closure_once_already_ready"
    assert child_card["next_action"]["tool"] == "write_file"
    assert len(trace["artifacts"]) == 1
    assert trace["artifacts"][0]["artifact_id"] == artifact_id
    assert trace["artifacts"][0]["path"] == str(assets.resolve())
    assert trace["artifacts"][0]["type"] == "collection"
    assert trace["summary"]["adapter_guard_hits"] == 1


def test_openclaw_gate_enforces_audit_bundle(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "fetcher.py").write_text("def deduplicate(seen):\n    return list(seen)[-5000:]\n", encoding="utf-8")
    (assets / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (assets / "output.json").write_text('[{"id":"a"}]\n', encoding="utf-8")
    (assets / "announcements_2026-02-09.json").write_text('[{"id":"b","important":true}]\n', encoding="utf-8")
    (assets / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")

    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")
    decision = bridge.handle({"method": "decide", "params": {"path": str(assets)}})

    assert "audit bundle" in decision["decision"]["reason"]
    assert decision["openclaw_gate"]["mode"] == "enforce"
    assert decision["openclaw_gate"]["block_native_read"] is True
    assert decision["openclaw_gate"]["block_native_search"] is True

    child_decision = bridge.handle({"method": "decide", "params": {"path": str(assets / "fetcher.py")}})
    assert child_decision["openclaw_gate"]["mode"] == "enforce"
    assert child_decision["openclaw_gate"]["block_native_read"] is True
    assert child_decision["openclaw_gate"]["handoff_path"] == str(assets)

    child_card = bridge.handle({"method": "card", "params": {"path": str(assets / "fetcher.py")}})
    assert child_card["file_card"]["path"] == str(assets)
    assert child_card["openclaw_gate"]["handoff_path"] == str(assets)


def test_openclaw_gate_keeps_small_diagnosis_bundle_native(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "task_scheduler.yaml").write_text(
        "tasks:\n  daily:\n    script: scripts/send.py\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "messaging.yaml").write_text(
        "providers:\n  telegram:\n    enabled: true\n",
        encoding="utf-8",
    )
    (tmp_path / "logs" / "book_recommendation.log").write_text(
        "2026-03-19 ERROR Telegram 429 retry_after=3600\n" * 80,
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "send.py").write_text("print('send')\n", encoding="utf-8")
    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")

    decision = bridge.handle({"method": "decide", "params": {"path": str(tmp_path)}})
    card = bridge.handle({"method": "card", "params": {"path": str(tmp_path)}})

    assert decision["decision"]["mode"] == "native"
    assert decision["openclaw_gate"]["mode"] == "native"
    assert decision["openclaw_gate"]["block_native_read"] is False
    assert card["file_card"]["sparse_recommended"] is False


def test_openclaw_gate_keeps_small_diagnosis_child_log_native(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "task_scheduler.yaml").write_text(
        "tasks:\n  daily:\n    script: scripts/send.py\n",
        encoding="utf-8",
    )
    (tmp_path / "logs" / "book_recommendation.log").write_text(
        "2026-03-19 ERROR Telegram 429 retry_after=3600\n" * 90,
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "send.py").write_text("print('send')\n", encoding="utf-8")
    log_path = tmp_path / "logs" / "book_recommendation.log"
    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")

    root_decision = bridge.handle({"method": "decide", "params": {"path": str(tmp_path)}})
    child_decision = bridge.handle({"method": "decide", "params": {"path": str(log_path)}})
    child_card = bridge.handle({"method": "card", "params": {"path": str(log_path)}})

    assert root_decision["decision"]["mode"] == "native"
    assert child_decision["decision"]["mode"] == "force_sro"
    assert child_decision["openclaw_gate"]["mode"] == "native"
    assert child_decision["openclaw_gate"]["block_native_read"] is False
    assert child_card["file_card"]["sparse_recommended"] is False
    assert "next_action" not in child_card


def test_openclaw_gate_ignores_openclaw_bootstrap_noise(tmp_path: Path) -> None:
    (tmp_path / ".openclaw").mkdir()
    (tmp_path / "cron_logs").mkdir()
    (tmp_path / "literature_results").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".openclaw" / "workspace-state.json").write_text('{"version":1}\n', encoding="utf-8")
    for name in ["AGENTS.md", "BOOTSTRAP.md", "IDENTITY.md", "USER.md"]:
        (tmp_path / name).write_text("# OpenClaw bootstrap\nstate output audit\n", encoding="utf-8")
    (tmp_path / "cron_config.json").write_text('{"script":"stable_literature_retrieval.py"}\n', encoding="utf-8")
    (tmp_path / "cron_logs" / "cron_execution_20260210_145000.json").write_text(
        '{"errors":["HTTPError: 429 Too Many Requests"]}\n',
        encoding="utf-8",
    )
    (tmp_path / "literature_results" / "master_progress_tracker.json").write_text(
        '{"status":"degraded","error_count":57}\n',
        encoding="utf-8",
    )
    (tmp_path / "literature_results" / "run_history_summary.json").write_text(
        '{"errors_by_type":{"HTTPError_429":38}}\n',
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "stable_literature_retrieval.py").write_text("print('stable')\n", encoding="utf-8")
    (tmp_path / "scripts" / "verified_rss_sources.py").write_text("SOURCES = []\n", encoding="utf-8")
    bridge = OpenClawBridge(workspace=tmp_path, mode="auto")

    decision = bridge.handle({"method": "decide", "params": {"path": str(tmp_path)}})
    card = bridge.handle({"method": "card", "params": {"path": str(tmp_path)}})
    card_names = {item["name"] for item in card["file_card"]["details"]["files"]}

    assert decision["decision"]["mode"] != "force_sro"
    assert decision["openclaw_gate"]["mode"] != "enforce"
    assert ".openclaw/workspace-state.json" not in card_names
    assert "AGENTS.md" not in card_names
    assert "cron_config.json" in card_names


def test_openclaw_read_accepts_string_target(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("key facts:\n- item A: 42\n- item B: 17\n", encoding="utf-8")
    bridge = OpenClawBridge(workspace=tmp_path, mode="force")

    card = bridge.handle({"method": "card", "params": {"path": str(target)}})
    artifact_id = card["file_card"]["artifact_id"]

    result = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": artifact_id,
                "mode": "collect",
                "hint": {"goal": "extract key facts", "type_hint": "text"},
            },
        }
    )
    assert result["evidence_pack"]["artifact_id"] == artifact_id
    trace = bridge.handle({"method": "trace", "params": {}})
    read_events = [e for e in trace["events"] if e["kind"] == "sro_read"]
    assert len(read_events) == 1
    assert read_events[0]["params"]["target"] == {"artifact_id": artifact_id}


def test_openclaw_read_mode_aliases(tmp_path: Path) -> None:
    target = tmp_path / "brief.txt"
    target.write_text("Pipeline: ingest -> transform -> publish\n", encoding="utf-8")
    bridge = OpenClawBridge(workspace=tmp_path, mode="force")

    card = bridge.handle({"method": "card", "params": {"path": str(target)}})
    artifact_id = card["file_card"]["artifact_id"]

    bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "full",
                "hint": {"goal": "describe the pipeline", "type_hint": "text"},
            },
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})
    read_events = [e for e in trace["events"] if e["kind"] == "sro_read"]
    assert read_events[-1]["params"]["mode"] == "collect"

    bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "scan",
                "hint": {"goal": "skim the pipeline", "type_hint": "text"},
            },
        }
    )
    trace = bridge.handle({"method": "trace", "params": {}})
    read_events = [e for e in trace["events"] if e["kind"] == "sro_read"]
    assert read_events[-1]["params"]["mode"] == "scout"
