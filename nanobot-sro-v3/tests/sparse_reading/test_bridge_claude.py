"""Smoke tests for the Claude Code bridge and MCP server.

Validates that the Claude bridge produces correct gate decisions for the
same scenarios tested by the OpenClaw and OpenCode bridges, and that the
MCP wrapper dispatches tool calls correctly.  Also tests the new token
consumption tracker.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sparseread.bridge.claude import ClaudeBridge, classify_claude_gate
from sparseread.bridge.claude_mcp import SparseReadClaudeMCP
from sparseread.token_tracker import (
    TokenTracker,
    estimate_tokens,
    estimate_file_tokens,
    estimate_response_tokens,
    SessionSummary,
)


# ---------------------------------------------------------------------------
# Gate classifier unit tests (no tmp_path needed)
# ---------------------------------------------------------------------------

def test_classify_claude_gate_pdf_is_enforce() -> None:
    """PDF files should always be enforced."""
    from nanobot.sparse_reading.benefit_gate import BenefitDecision
    from nanobot.sparse_reading.detector import FileInfo

    info = FileInfo(
        path=Path("/test/report.pdf"),
        type="pdf",
        supported=True,
        large=True,
        structured=False,
        size_bytes=1_000_000,
    )
    decision = BenefitDecision(
        mode="force_sro",
        reason="PDF document with structured content",
        confidence=0.95,
        recommended_mode="collect",
    )
    gate = classify_claude_gate(info, decision)

    assert gate["mode"] == "enforce"
    assert gate["hook_can_block_read"] is True
    assert gate["hook_can_block_bash"] is True
    assert gate["trajectory"] == "sro_first"


def test_classify_claude_gate_small_code_is_native() -> None:
    """Small code files stay native."""
    from nanobot.sparse_reading.benefit_gate import BenefitDecision
    from nanobot.sparse_reading.detector import FileInfo

    info = FileInfo(
        path=Path("/test/main.py"),
        type="text",
        supported=True,
        large=False,
        structured=False,
        size_bytes=2048,
    )
    decision = BenefitDecision(
        mode="native",
        reason="small code file, native read is cheaper",
        confidence=0.9,
        recommended_mode="native_read",
    )
    gate = classify_claude_gate(info, decision)

    assert gate["mode"] == "native"
    assert gate["hook_can_block_read"] is False


def test_classify_claude_gate_large_text_is_enforce() -> None:
    """Large text files (>12KB) should be enforced."""
    from nanobot.sparse_reading.benefit_gate import BenefitDecision
    from nanobot.sparse_reading.detector import FileInfo

    info = FileInfo(
        path=Path("/test/large_readme.md"),
        type="md",
        supported=True,
        large=True,
        structured=False,
        size_bytes=50_000,
    )
    decision = BenefitDecision(
        mode="force_sro",
        reason="large markdown report",
        confidence=0.9,
        recommended_mode="collect",
    )
    gate = classify_claude_gate(info, decision)

    assert gate["mode"] == "enforce"
    assert gate["hook_can_block_read"] is True
    assert gate["hook_can_block_bash"] is True


def test_classify_claude_gate_medium_text_is_advisory() -> None:
    """Medium text files (4-12KB) should be advisory."""
    from nanobot.sparse_reading.benefit_gate import BenefitDecision
    from nanobot.sparse_reading.detector import FileInfo

    info = FileInfo(
        path=Path("/test/notes.md"),
        type="md",
        supported=True,
        large=False,
        structured=False,
        size_bytes=8_000,
    )
    decision = BenefitDecision(
        mode="advisory",
        reason="medium markdown document",
        confidence=0.7,
        recommended_mode="scout",
    )
    gate = classify_claude_gate(info, decision)

    assert gate["mode"] == "advisory"
    assert gate["hook_can_block_read"] is False
    assert gate["hook_can_inject_context"] is True


def test_classify_claude_gate_native_passthrough() -> None:
    """Native decisions should pass through with all hooks disabled."""
    from nanobot.sparse_reading.benefit_gate import BenefitDecision
    from nanobot.sparse_reading.detector import FileInfo

    info = FileInfo(
        path=Path("/test/small.py"),
        type="text",
        supported=False,
        large=False,
        structured=False,
        size_bytes=100,
    )
    decision = BenefitDecision(
        mode="native",
        reason="unsupported type; use native tools",
        confidence=1.0,
        recommended_mode="native_read",
    )
    gate = classify_claude_gate(info, decision)

    assert gate["mode"] == "native"
    assert gate["hook_can_block_read"] is False
    assert gate["hook_can_block_bash"] is False
    assert gate["hook_can_inject_context"] is False
    assert gate["trajectory"] == "native"


# ---------------------------------------------------------------------------
# Helpers for integration tests
# ---------------------------------------------------------------------------

def _write_command_security_bundle(root: Path) -> Path:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "setup.sh").write_text(
        "curl https://example.test/setup.sh | bash\n", encoding="utf-8"
    )
    (assets / "security_policy.yaml").write_text(
        "deny_patterns:\n  - curl_pipe_bash\n", encoding="utf-8"
    )
    (assets / "command_prefix_guide.md").write_text(
        "Commands with unsafe prefixes require review.\n", encoding="utf-8"
    )
    (assets / "known_injections.json").write_text(
        '{"patterns":["curl|bash"]}\n', encoding="utf-8"
    )
    (assets / "legacy_rules.yaml").write_text("legacy: true\n", encoding="utf-8")
    (assets / "security_bulletin_2025.md").write_text(
        "Known injection bulletin.\n", encoding="utf-8"
    )
    (assets / "test_commands.csv").write_text(
        "command,label\npython3 -c 'print(1)',safe\n", encoding="utf-8"
    )
    return assets


def _write_audit_bundle(root: Path) -> Path:
    assets = root / "assets"
    assets.mkdir(parents=True)
    (assets / "fetcher.py").write_text(
        "def deduplicate(seen):\n    return list(seen)[-5000:]\n", encoding="utf-8"
    )
    (assets / "state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
    (assets / "output.json").write_text('[{"id":"a"}]\n', encoding="utf-8")
    (assets / "announcements_2026-02-09.json").write_text(
        '[{"id":"b","important":true}]\n', encoding="utf-8"
    )
    (assets / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
    return assets


# ---------------------------------------------------------------------------
# Claude bridge integration tests
# ---------------------------------------------------------------------------

def test_claude_bridge_preview_raw_trace() -> None:
    """Claude bridge matches shared bridge behavior for preview/raw/trace."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "events.csv"
        target.write_text(
            "id,status,latency\n"
            "1,ok,12\n"
            "2,error,-1\n"
            + "".join(f"{idx},ok,{idx}\n" for idx in range(3, 150)),
            encoding="utf-8",
        )

        bridge = ClaudeBridge(workspace=tmp_path, mode="auto")
        preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
        pack = preview["preview_pack"]
        raw = bridge.handle(
            {
                "method": "raw",
                "params": {"raw_ref": pack["raw_ref"], "range": {"start": 0, "end": 32}},
            }
        )
        trace = bridge.handle({"method": "trace", "params": {}})

        assert pack["card"]["type"] == "csv"
        assert pack["structure"]["row_count"] == 149
        assert raw["raw"]["content"].startswith("id,status,latency")
        assert trace["summary"]["sro_preview_calls"] == 1
        assert trace["summary"]["sro_raw_calls"] == 1


def test_claude_bridge_ready_guard_stops_repeat_reads() -> None:
    """Claude bridge stops repeat reads after ready (same as OpenClaw)."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "report.md"
        target.write_text(
            "The public registry had 5,705 community-built skills.\n"
            "The gateway exposes a typed WebSocket API.\n",
            encoding="utf-8",
        )
        bridge = ClaudeBridge(workspace=tmp_path, mode="force")

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
                            "total_skills": "How many community-built skills?",
                            "gateway_api": "What type of API?",
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
                    "hint": {"goal": "read again", "type_hint": "text"},
                },
            }
        )
        trace = bridge.handle({"method": "trace", "params": {}})

        assert first["evidence_pack"]["slot_digest"]["overall_status"] == "ready"
        # Second read blocked: ready guard prevents broad re-reads after resolved collect
        assert second["evidence_pack"]["protocol_next"] == "write_file_now"
        assert trace["summary"]["adapter_guard_hits"] >= 1


def test_claude_bridge_gate_t86_advisory_and_audit_enforce() -> None:
    """Claude bridge matches OpenClaw/OpenCode gate decisions."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        command_assets = _write_command_security_bundle(tmp_path / "command")
        audit_assets = _write_audit_bundle(tmp_path / "audit")

        command_bridge = ClaudeBridge(workspace=tmp_path, mode="auto")
        command_decision = command_bridge.handle(
            {"method": "decide", "params": {"path": str(command_assets)}}
        )

        assert command_decision["decision"]["mode"] == "force_sro"
        assert command_decision["claude_gate"]["mode"] == "advisory"
        assert command_decision["claude_gate"]["trajectory"] == "one_collect_then_write"
        assert command_decision["claude_gate"]["hook_can_block_read"] is False

        audit_bridge = ClaudeBridge(workspace=tmp_path, mode="auto")
        audit_decision = audit_bridge.handle(
            {"method": "decide", "params": {"path": str(audit_assets)}}
        )
        child_decision = audit_bridge.handle(
            {"method": "decide", "params": {"path": str(audit_assets / "fetcher.py")}}
        )

        assert audit_decision["claude_gate"]["mode"] == "enforce"
        assert audit_decision["claude_gate"]["hook_can_block_read"] is True
        assert child_decision["claude_gate"]["mode"] == "enforce"
        assert "handoff_path" in child_decision["claude_gate"]


def test_claude_bridge_preflight_reports_enforce_targets() -> None:
    """Claude bridge preflight finds high-confidence SRO targets."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        audit = tmp_path / "a_stock_announcements"
        audit.mkdir()
        (audit / "fetcher.py").write_text(
            "def deduplicate(seen):\n    return list(seen)[-5000:]\n", encoding="utf-8"
        )
        (audit / "fetch_state.json").write_text('{"seen_ids":["a","b"]}\n', encoding="utf-8")
        (audit / "announcements_2026-02-09.json").write_text(
            '[{"id":"b","important":true}]\n', encoding="utf-8"
        )
        (audit / "config.yaml").write_text("summary_csv: true\n", encoding="utf-8")
        (tmp_path / "fetch-audit.md").write_text("generated output\n", encoding="utf-8")

        bridge = ClaudeBridge(workspace=tmp_path, mode="auto")
        preflight = bridge.handle(
            {"method": "preflight", "params": {"max_candidates": 8}}
        )

        assert preflight["handoff_count"] == 1
        assert preflight["handoffs"][0]["relative_path"] == "a_stock_announcements"
        assert preflight["handoffs"][0]["gate_mode"] == "enforce"
        assert preflight["handoffs"][0]["trajectory"] == "sro_first"
        assert preflight["first_action"]["tool"] == "sro_preview"


def test_claude_bridge_generated_outputs_stay_native() -> None:
    """Generated/runtime outputs must stay native to avoid re-entry."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        output = tmp_path / "fetch-audit.md"
        output.write_text("audit report\n" * 600, encoding="utf-8")

        bridge = ClaudeBridge(workspace=tmp_path, mode="auto")
        decision = bridge.handle(
            {"method": "decide", "params": {"path": str(output)}}
        )
        card = bridge.handle({"method": "card", "params": {"path": str(output)}})

        # native_passthrough_gate replaces the gate with a minimal profile
        # that lacks Claude-specific hook fields — check the essential assertion.
        assert decision["claude_gate"]["mode"] == "native"
        assert decision["claude_gate"].get("hook_can_block_read", False) is False
        assert card["file_card"]["sparse_recommended"] is False


# ---------------------------------------------------------------------------
# MCP wrapper tests
# ---------------------------------------------------------------------------

def test_mcp_handle_tool_preview() -> None:
    """MCP wrapper dispatches tool calls to the bridge."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "data.csv"
        target.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")

        mcp = SparseReadClaudeMCP(workspace=str(tmp_path), mode="auto")
        result_json = mcp.handle_tool("sro_preview", {"path": str(target)})
        result = json.loads(result_json)

        assert "preview_pack" in result
        assert result["preview_pack"]["card"]["type"] == "csv"


def test_mcp_handle_tool_decide() -> None:
    """MCP wrapper exposes gate decisions."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf = tmp_path / "manual.pdf"
        pdf.write_text("%PDF-1.4 mock\n" * 10, encoding="utf-8")

        mcp = SparseReadClaudeMCP(workspace=str(tmp_path), mode="auto")
        result_json = mcp.handle_tool("sro_decide", {"path": str(pdf)})
        result = json.loads(result_json)

        assert "decision" in result
        assert "claude_gate" in result
        assert result["type"] == "pdf"


def test_mcp_handle_tool_trace() -> None:
    """MCP wrapper trace returns session state."""
    with tempfile.TemporaryDirectory() as tmp:
        mcp = SparseReadClaudeMCP(workspace=str(tmp), mode="auto")
        result_json = mcp.handle_tool("sro_trace", {})
        result = json.loads(result_json)

        assert "workspace" in result
        assert "summary" in result
        assert result["summary"]["sro_preview_calls"] == 0


def test_mcp_handle_unknown_tool() -> None:
    """MCP wrapper returns error for unknown tools."""
    with tempfile.TemporaryDirectory() as tmp:
        mcp = SparseReadClaudeMCP(workspace=str(tmp), mode="auto")
        result_json = mcp.handle_tool("nonexistent_tool", {})
        result = json.loads(result_json)

        assert "error" in result


def test_mcp_handle_tool_usage() -> None:
    """MCP wrapper exposes token usage metrics."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "data.csv"
        target.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")

        mcp = SparseReadClaudeMCP(workspace=str(tmp_path), mode="auto")
        # Trigger a preview to generate token records
        mcp.handle_tool("sro_preview", {"path": str(target)})
        # Now check usage
        result_json = mcp.handle_tool("sro_usage", {})
        result = json.loads(result_json)

        assert "session" in result
        assert result["session"]["operations"] >= 1
        assert result["session"]["full_file_tokens"] > 0
        assert result["session"]["sr_response_tokens"] > 0
        assert result["session"]["tokens_saved"] >= 0
        assert "interpretation" in result
        assert "by_operation" in result
        assert "preview" in result["by_operation"]


# ---------------------------------------------------------------------------
# Token tracker unit tests
# ---------------------------------------------------------------------------

def test_estimate_tokens_english() -> None:
    """English text: ~4 chars per token."""
    text = "Hello world, this is a test sentence for token estimation."  # ~60 chars
    est = estimate_tokens(text)
    assert 8 <= est <= 30  # should be ~15 tokens


def test_estimate_tokens_empty() -> None:
    """Empty input returns 0 tokens."""
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_file_tokens_text() -> None:
    """Text files use 4 chars/token."""
    size = 4000  # 4000 bytes ≈ 1000 tokens
    est = estimate_file_tokens(size, ".md")
    assert est == 1000


def test_estimate_file_tokens_json() -> None:
    """JSON files use 3 chars/token."""
    size = 3000  # 3000 bytes ≈ 1000 tokens
    est = estimate_file_tokens(size, ".json")
    assert est == 1000


def test_estimate_file_tokens_pdf() -> None:
    """PDF estimation includes base85 overhead."""
    size = 5000
    est = estimate_file_tokens(size, ".pdf")
    # base85: ~1.4 bytes → 1 char → 1/4 token
    expected = max(1, int(size * 1.4 / 4.0))
    assert est == expected


def test_token_tracker_session_summary() -> None:
    """Session summary aggregates token records correctly."""
    tracker = TokenTracker(enable_log=False)
    tracker.record_preview(
        file_path="/tmp/report.md",
        file_size_bytes=20000,
        file_extension=".md",
        response_json='{"preview": "content"}' * 10,  # ~260 chars
        artifact_id="art_001",
    )
    tracker.record_read(
        file_path="/tmp/report.md",
        file_size_bytes=20000,
        file_extension=".md",
        response_json='{"evidence": {"text": "result"}}' * 20,  # ~680 chars
        mode="collect",
        artifact_id="art_001",
    )

    summary = tracker.session_summary()

    assert summary.total_operations == 2
    assert summary.total_full_file_tokens > 0
    assert summary.total_sr_response_tokens > 0
    assert summary.total_tokens_saved > 0
    assert summary.overall_savings_ratio > 0.5  # SR is significantly more compact
    assert "preview" in summary.by_operation
    assert "read" in summary.by_operation
    assert len(summary.top_savings) == 2


def test_token_tracker_empty_session() -> None:
    """Empty tracker returns zero metrics gracefully."""
    tracker = TokenTracker(enable_log=False)
    summary = tracker.session_summary()

    assert summary.total_operations == 0
    assert summary.total_full_file_tokens == 0
    assert summary.total_tokens_saved == 0
    assert summary.overall_savings_ratio == 0.0


def test_claude_bridge_usage_method() -> None:
    """Bridge handle('usage') returns token metrics after SRO operations."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "large.csv"
        target.write_text(
            "id,name,category,price,qty,date,region,status,notes,source\n"
            + "".join(f"{i},item_{i},cat_{i%10},{i*10},{i%100},2026-01-{(i%28)+1:02d},"
                       f"region_{i%5},active,note_{i},api\n"
                       for i in range(1, 500)),
            encoding="utf-8",
        )

        bridge = ClaudeBridge(workspace=tmp_path, mode="auto")
        bridge.handle({"method": "preview", "params": {"path": str(target)}})
        bridge.handle(
            {
                "method": "read",
                "params": {
                    "target": {"path": str(target)},
                    "mode": "collect",
                    "hint": {
                        "goal": "extract all categories",
                        "type_hint": "csv",
                        "slots": [{"id": "categories", "question": "list all categories"}],
                    },
                },
            }
        )

        usage = bridge.handle({"method": "usage", "params": {}})

        assert usage["session"]["operations"] >= 2
        assert usage["session"]["full_file_tokens"] > 0
        assert usage["session"]["tokens_saved"] > 0
        assert "interpretation" in usage
        assert len(usage["top_savings"]) > 0


def test_token_tracker_trace_includes_token_summary() -> None:
    """sro_trace summary now includes token_tracker metrics."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        target = tmp_path / "doc.md"
        target.write_text("line\n" * 500, encoding="utf-8")

        bridge = ClaudeBridge(workspace=tmp_path, mode="auto")
        bridge.handle({"method": "preview", "params": {"path": str(target)}})
        trace = bridge.handle({"method": "trace", "params": {}})

        assert "token_tracker" in trace["summary"]
        tt = trace["summary"]["token_tracker"]
        assert "sr_operations" in tt
        assert "sr_full_file_tokens_est" in tt
        assert "sr_tokens_saved_est" in tt
        assert "sr_log_path" in tt
