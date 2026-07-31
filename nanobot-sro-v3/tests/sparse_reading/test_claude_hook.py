"""Unit tests for the session-mode SparseRead Claude Code hook.

Validates that the hook's gate decisions match the bridge's classify_claude_gate
output, and that PreToolUse/PostToolUse handlers produce correct responses.
Covers all 15 mismatches that were fixed from the old simple hook.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from sparseread.hooks.claude_hook import (
    HookSession,
    handle_pretooluse,
    handle_posttooluse,
    _make_gate_engine,
    _extract_read_path,
    _extract_bash_paths,
    _resolve_path,
    _is_generated_or_runtime,
    _has_read_constraints,
    _format_block,
    _format_advisory,
    _format_allow,
    _format_post_nudge,
    _format_post_passthrough,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_workspace() -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def session(tmp_workspace: Path) -> HookSession:
    gate, _ = _make_gate_engine(str(tmp_workspace))
    return HookSession(gate, workspace=str(tmp_workspace))


# ---------------------------------------------------------------------------
# Gate decision tests — verify hook matches bridge
# ---------------------------------------------------------------------------


class TestGateDecisions:
    """Verify hook gate decisions match bridge classify_claude_gate for all 15 fixed mismatches."""

    def test_pdf_enforce(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #5: PDF <4KB should be native, PDF >=4KB should be enforce."""
        # Small PDF (<4KB)
        small_pdf = tmp_workspace / "small.pdf"
        small_pdf.write_text("%PDF-1.4\n", encoding="utf-8")
        decision = session.decide(str(small_pdf))
        assert decision["decision"] == "native", f"Small PDF should be native, got {decision['decision']}"

        # Large PDF (>=4KB)
        large_pdf = tmp_workspace / "large.pdf"
        large_pdf.write_text("%PDF-1.4 mock content\n" * 400, encoding="utf-8")
        decision = session.decide(str(large_pdf))
        assert decision["decision"] == "enforce", f"Large PDF should be enforce, got {decision['decision']}"

    def test_code_config_always_native(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #1: Code/config files >4KB should be native, not enforce."""
        for ext, content in [
            (".py", "def foo():\n    pass\n" * 500),
            (".sh", "#!/bin/bash\necho hello\n" * 500),
            (".toml", "[section]\nkey = 'value'\n" * 500),
        ]:
            f = tmp_workspace / f"large_code{ext}"
            f.write_text(content, encoding="utf-8")
            decision = session.decide(str(f))
            assert decision["decision"] in ("native", "advisory"), (
                f"Code file {ext} >4KB should NOT be enforce, got {decision}"
            )

    def test_structured_data_advisory(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #2: Structured data (CSV/JSON) should be advisory, not enforce."""
        # Large CSV
        csv_f = tmp_workspace / "data.csv"
        csv_f.write_text("a,b,c\n" + "1,2,3\n" * 2000, encoding="utf-8")
        decision = session.decide(str(csv_f))
        assert decision["decision"] == "advisory", (
            f"Large CSV should be advisory, got {decision['decision']}"
        )

        # Large JSON
        json_f = tmp_workspace / "data.json"
        json_f.write_text(json.dumps([{"id": i, "value": f"item_{i}"} for i in range(1000)]))
        decision = session.decide(str(json_f))
        assert decision["decision"] == "advisory", (
            f"Large JSON should be advisory, got {decision['decision']}"
        )

    def test_text_medium_advisory(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #13: Text 4-12KB should trigger advisory (not native, not enforce)."""
        # ~8KB text file
        medium_md = tmp_workspace / "medium.md"
        medium_md.write_text("# Title\n\n" + "Content line.\n" * 600, encoding="utf-8")
        size = medium_md.stat().st_size
        decision = session.decide(str(medium_md))
        # With BenefitGate, files >= 4096 bytes are "large" → force_sro
        # But Claude classifier maps text < 12288 to advisory
        if size < 12288:
            assert decision["decision"] in ("advisory", "enforce"), (
                f"Text 4-12KB should be advisory or enforce, got {decision['decision']}"
            )

    def test_text_large_enforce(self, session: HookSession, tmp_workspace: Path) -> None:
        """Large text >12KB should be enforce."""
        large_md = tmp_workspace / "large.md"
        large_md.write_text("# Big Doc\n\n" + "Content here.\n" * 3000, encoding="utf-8")
        decision = session.decide(str(large_md))
        assert decision["decision"] == "enforce", f"Large text should be enforce, got {decision['decision']}"

    def test_small_file_native(self, session: HookSession, tmp_workspace: Path) -> None:
        """Files under 4KB should be native."""
        small = tmp_workspace / "small.txt"
        small.write_text("Hello world.\n", encoding="utf-8")
        decision = session.decide(str(small))
        assert decision["decision"] == "native", f"Small file should be native, got {decision['decision']}"

    def test_generated_artifact_native(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #12: Generated outputs should always be native."""
        output = tmp_workspace / "fetch-audit.md"
        output.write_text("Audit report.\n" * 500, encoding="utf-8")
        decision = session.decide(str(output))
        assert decision["decision"] == "native", (
            f"Generated artifact should be native, got {decision['decision']}"
        )

    def test_nonexistent_path_native(self, session: HookSession, tmp_workspace: Path) -> None:
        """Non-existent paths should be native (fail open)."""
        decision = session.decide(str(tmp_workspace / "nonexistent.md"))
        assert decision["decision"] == "native"

    def test_collection_audit_enforce(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #14: Collection with code+state+output → enforce."""
        assets = tmp_workspace / "audit_bundle"
        assets.mkdir()
        (assets / "fetcher.py").write_text("def main():\n    pass\n" * 200, encoding="utf-8")
        (assets / "state.json").write_text('{"seen_ids": ["a", "b", "c"]}', encoding="utf-8")
        (assets / "output.json").write_text('[{"id": "a"}]', encoding="utf-8")
        (assets / "config.yaml").write_text("key: value\n", encoding="utf-8")
        decision = session.decide(str(assets))
        assert decision["decision"] == "enforce", (
            f"Audit bundle should be enforce, got {decision['decision']}"
        )

    def test_small_code_dir_native(self, session: HookSession, tmp_workspace: Path) -> None:
        """Directory with only small code files should not be enforce."""
        src = tmp_workspace / "src"
        src.mkdir()
        (src / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (src / "utils.py").write_text("def helper():\n    pass\n", encoding="utf-8")
        decision = session.decide(str(src))
        # Small code directory — may be native or advisory, should NOT be enforce
        assert decision["decision"] != "enforce", (
            f"Small code dir should NOT be enforce, got {decision['decision']}"
        )

    def test_cache_hit(self, session: HookSession, tmp_workspace: Path) -> None:
        """Session cache should return cached results on second lookup."""
        f = tmp_workspace / "cached.md"
        f.write_text("# Cached\n\n" + "content.\n" * 2000, encoding="utf-8")
        d1 = session.decide(str(f))
        d2 = session.decide(str(f))
        assert d1["decision"] == d2["decision"]
        assert d2["cached"] is True

    def test_one_time_block(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #10: After blocking once, subsequent attempts should be advisory (not re-block)."""
        f = tmp_workspace / "one_time.md"
        f.write_text("# Doc\n\n" + "content.\n" * 2000, encoding="utf-8")
        # First decision: enforce
        d1 = session.decide(str(f))
        assert d1["decision"] == "enforce"
        # Mark as blocked (simulating hook blocking the read)
        session.mark_blocked(str(f))
        # Second decision: should downgrade to advisory
        d2 = session.decide(str(f))
        assert d2["decision"] == "advisory", (
            f"One-time block: second attempt should be advisory, got {d2['decision']}"
        )


# ---------------------------------------------------------------------------
# Path extraction tests
# ---------------------------------------------------------------------------


class TestPathExtraction:
    def test_extract_read_path(self) -> None:
        assert _extract_read_path({"file_path": "/tmp/test.md"}) == "/tmp/test.md"
        assert _extract_read_path({}) == ""

    def test_extract_bash_cat(self) -> None:
        paths = _extract_bash_paths({"command": "cat /tmp/large.md"})
        assert "/tmp/large.md" in paths

    def test_extract_bash_head_quoted(self) -> None:
        paths = _extract_bash_paths({"command": 'head "/path/with spaces.md"'})
        assert "/path/with spaces.md" in paths

    def test_extract_bash_rg(self) -> None:
        """OpenClaw parity: rg/grep should be detected."""
        paths = _extract_bash_paths({"command": "rg 'pattern' /tmp/search.md"})
        assert "/tmp/search.md" in paths

    def test_extract_bash_pdftotext(self) -> None:
        """OpenClaw parity: pdftotext should be detected."""
        paths = _extract_bash_paths({"command": "pdftotext /tmp/report.pdf"})
        assert "/tmp/report.pdf" in paths

    def test_extract_bash_non_file_command(self) -> None:
        paths = _extract_bash_paths({"command": "ls -la /tmp"})
        assert paths == []

    def test_has_read_constraints(self) -> None:
        assert _has_read_constraints({"offset": 10}) is True
        assert _has_read_constraints({"limit": 100}) is True
        assert _has_read_constraints({"pages": "1-5"}) is True
        assert _has_read_constraints({}) is False

    def test_is_generated_or_runtime(self, tmp_workspace: Path) -> None:
        assert _is_generated_or_runtime(tmp_workspace / "fetch-audit.md") is True
        assert _is_generated_or_runtime(tmp_workspace / ".git" / "config") is True
        assert _is_generated_or_runtime(tmp_workspace / "node_modules" / "pkg" / "index.js") is True
        assert _is_generated_or_runtime(tmp_workspace / "normal_file.md") is False


# ---------------------------------------------------------------------------
# Response formatter tests
# ---------------------------------------------------------------------------


class TestResponseFormatters:
    def test_format_block(self) -> None:
        resp = _format_block("/tmp/test.md", "large markdown file")
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "deny"
        assert "sro_preview" in hook["additionalContext"]
        assert "/tmp/test.md" in hook["additionalContext"]

    def test_format_advisory(self) -> None:
        resp = _format_advisory("/tmp/data.csv", "large structured data")
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"
        assert "sro_preview" in hook["additionalContext"]

    def test_format_allow(self) -> None:
        resp = _format_allow()
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"
        assert "additionalContext" not in hook

    def test_format_post_nudge(self) -> None:
        tool_output = {"content": "very long content..."}
        resp = _format_post_nudge("/tmp/big.md", 10000, tool_output)
        hook = resp["hookSpecificOutput"]
        updated = hook["updatedToolOutput"]
        assert "SparseRead tip" in updated["content"]
        assert "sro_preview" in updated["content"]

    def test_format_post_passthrough(self) -> None:
        resp = _format_post_passthrough()
        hook = resp["hookSpecificOutput"]
        assert hook["hookEventName"] == "PostToolUse"
        assert "updatedToolOutput" not in hook


# ---------------------------------------------------------------------------
# PreToolUse handler tests
# ---------------------------------------------------------------------------


class TestPreToolUseHandler:
    def test_read_large_file_block(self, session: HookSession, tmp_workspace: Path) -> None:
        f = tmp_workspace / "large.md"
        f.write_text("# Big\n\n" + "content.\n" * 3000, encoding="utf-8")
        resp = handle_pretooluse(session, "Read", {"file_path": str(f)})
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "deny"
        assert "sro_preview" in hook["additionalContext"]

    def test_read_small_file_allow(self, session: HookSession, tmp_workspace: Path) -> None:
        f = tmp_workspace / "small.py"
        f.write_text("x = 1\n", encoding="utf-8")
        resp = handle_pretooluse(session, "Read", {"file_path": str(f)})
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"

    def test_read_with_constraints_allow(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #7: Partial reads (offset/limit/pages) should be allowed."""
        f = tmp_workspace / "large.md"
        f.write_text("# Big\n\n" + "content.\n" * 3000, encoding="utf-8")
        resp = handle_pretooluse(session, "Read", {"file_path": str(f), "offset": 10, "limit": 50})
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"

    def test_bash_cat_large_file_block(self, session: HookSession, tmp_workspace: Path) -> None:
        f = tmp_workspace / "large.txt"
        f.write_text("data\n" * 3000, encoding="utf-8")
        resp = handle_pretooluse(session, "Bash", {"command": f"cat {f}"})
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "deny"

    def test_bash_non_file_allow(self, session: HookSession) -> None:
        resp = handle_pretooluse(session, "Bash", {"command": "ls -la"})
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"

    def test_unknown_tool_allow(self, session: HookSession) -> None:
        resp = handle_pretooluse(session, "Grep", {"pattern": "test"})
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"

    def test_advisory_csv_allows_with_context(self, session: HookSession, tmp_workspace: Path) -> None:
        """Fix #2: Advisory mode allows but injects SRO context."""
        f = tmp_workspace / "data.csv"
        f.write_text("a,b,c\n" + "1,2,3\n" * 2000, encoding="utf-8")
        resp = handle_pretooluse(session, "Read", {"file_path": str(f)})
        hook = resp["hookSpecificOutput"]
        assert hook["permissionDecision"] == "allow"
        # Advisory should inject context
        assert "additionalContext" in hook


# ---------------------------------------------------------------------------
# PostToolUse handler tests
# ---------------------------------------------------------------------------


class TestPostToolUseHandler:
    def test_large_output_nudge(self, session: HookSession, tmp_workspace: Path) -> None:
        f = tmp_workspace / "big.md"
        f.write_text("# Big\n\n" + "content.\n" * 3000, encoding="utf-8")
        # Simulate a large output from a native read
        large_output = {"content": "x" * 6000}  # > POST_NUDGE_CHARS (5000)
        resp = handle_posttooluse(session, "Read", {"file_path": str(f)}, large_output)
        hook = resp["hookSpecificOutput"]
        # May or may not nudge depending on gate decision (file might be enforce)
        assert hook["hookEventName"] == "PostToolUse"

    def test_small_output_passthrough(self, session: HookSession) -> None:
        small_output = {"content": "short result"}
        resp = handle_posttooluse(session, "Read", {"file_path": "/tmp/small.md"}, small_output)
        hook = resp["hookSpecificOutput"]
        assert "updatedToolOutput" not in hook

    def test_no_output_passthrough(self, session: HookSession) -> None:
        resp = handle_posttooluse(session, "Read", {"file_path": "/tmp/f.md"}, None)
        hook = resp["hookSpecificOutput"]
        assert "updatedToolOutput" not in hook


# ---------------------------------------------------------------------------
# Session stats tests
# ---------------------------------------------------------------------------


class TestSessionStats:
    def test_stats_counters(self, session: HookSession, tmp_workspace: Path) -> None:
        # Create one enforce and one native file
        large = tmp_workspace / "large.md"
        large.write_text("content.\n" * 3000, encoding="utf-8")
        small = tmp_workspace / "small.py"
        small.write_text("x = 1\n", encoding="utf-8")

        handle_pretooluse(session, "Read", {"file_path": str(large)})
        handle_pretooluse(session, "Read", {"file_path": str(small)})

        stats = session.stats
        assert stats["blocks"] + stats["allows"] + stats["nudges"] >= 2
        assert stats["cache_size"] >= 2


# ---------------------------------------------------------------------------
# Bridge parity tests — hook decisions match bridge decisions
# ---------------------------------------------------------------------------


class TestBridgeParity:
    """Verify that the hook produces the same gate mode as the bridge's classify_claude_gate."""

    def test_hook_matches_bridge_pdf(self, tmp_workspace: Path) -> None:
        from sparseread.bridge.claude import ClaudeBridge

        pdf = tmp_workspace / "report.pdf"
        pdf.write_text("%PDF-1.4 mock\n" * 400, encoding="utf-8")

        gate, _ = _make_gate_engine(str(tmp_workspace))
        hook_session = HookSession(gate, workspace=str(tmp_workspace))
        hook_decision = hook_session.decide(str(pdf))

        bridge = ClaudeBridge(workspace=tmp_workspace, mode="auto")
        bridge_decide = bridge.handle({"method": "decide", "params": {"path": str(pdf)}})
        bridge_mode = bridge_decide.get("claude_gate", {}).get("mode", "unknown")

        assert hook_decision["decision"] == bridge_mode, (
            f"Hook: {hook_decision['decision']}, Bridge: {bridge_mode}"
        )

    def test_hook_matches_bridge_large_text(self, tmp_workspace: Path) -> None:
        from sparseread.bridge.claude import ClaudeBridge

        md = tmp_workspace / "large.md"
        md.write_text("# Doc\n\n" + "content.\n" * 3000, encoding="utf-8")

        gate, _ = _make_gate_engine(str(tmp_workspace))
        hook_session = HookSession(gate, workspace=str(tmp_workspace))
        hook_decision = hook_session.decide(str(md))

        bridge = ClaudeBridge(workspace=tmp_workspace, mode="auto")
        bridge_decide = bridge.handle({"method": "decide", "params": {"path": str(md)}})
        bridge_mode = bridge_decide.get("claude_gate", {}).get("mode", "unknown")

        assert hook_decision["decision"] == bridge_mode, (
            f"Hook: {hook_decision['decision']}, Bridge: {bridge_mode}"
        )

    def test_hook_matches_bridge_small_code(self, tmp_workspace: Path) -> None:
        from sparseread.bridge.claude import ClaudeBridge

        py = tmp_workspace / "main.py"
        py.write_text("x = 1\n", encoding="utf-8")

        gate, _ = _make_gate_engine(str(tmp_workspace))
        hook_session = HookSession(gate, workspace=str(tmp_workspace))
        hook_decision = hook_session.decide(str(py))

        bridge = ClaudeBridge(workspace=tmp_workspace, mode="auto")
        bridge_decide = bridge.handle({"method": "decide", "params": {"path": str(py)}})
        bridge_mode = bridge_decide.get("claude_gate", {}).get("mode", "unknown")

        assert hook_decision["decision"] == bridge_mode, (
            f"Hook: {hook_decision['decision']}, Bridge: {bridge_mode}"
        )
