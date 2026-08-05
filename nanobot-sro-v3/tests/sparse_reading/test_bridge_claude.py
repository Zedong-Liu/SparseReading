from __future__ import annotations

import json
import tempfile
from pathlib import Path

from sparseread.core.benefit_gate import BenefitDecision
from sparseread.core.detector import FileInfo

from sparseread_claude.bridge import CLAUDE_TEXT_ENFORCE_BYTES, ClaudeBridge, classify_claude_gate
from sparseread_claude.claude_mcp import SparseReadClaudeMCP
from sparseread_claude.token_tracker import estimate_file_tokens, estimate_tokens


def _decision(
    mode: str = "native",
    *,
    code: str = "unspecified",
    reason: str = "reason",
    preview: bool = False,
    scope_kind: str = "single_document",
) -> BenefitDecision:
    return BenefitDecision(
        mode=mode,  # type: ignore[arg-type]
        reason=reason,
        confidence=1.0,
        recommended_mode="recommended",
        code=code,
        preview_recommended=preview,
        scope_kind=scope_kind,
    )


def _info(
    path: Path,
    *,
    type: str = "text",
    size: int = 100,
    structured: bool = False,
) -> FileInfo:
    return FileInfo(
        path=path,
        type=type,
        size_bytes=size,
        structured=structured,
        supported=True,
        large=size >= 4096,
    )


def test_classify_pdf_always_enforce() -> None:
    decision = _decision("force_sro", code="long_document")
    profile = classify_claude_gate(_info(Path("a.pdf"), type="pdf", size=5000), decision)
    assert profile["mode"] == "enforce"
    assert profile["hook_can_block_read"] is True
    assert profile["block_native_read"] is True
    assert profile["trajectory"] == "sro_first"


def test_classify_small_code_is_native() -> None:
    profile = classify_claude_gate(_info(Path("a.py"), type="text", size=2000), _decision("native"))
    assert profile["mode"] == "native"
    assert profile["hook_can_block_read"] is False


def test_classify_large_text_is_enforce() -> None:
    decision = _decision("force_sro", code="long_document_selective", preview=True)
    profile = classify_claude_gate(
        _info(Path("a.md"), type="text", size=CLAUDE_TEXT_ENFORCE_BYTES + 1), decision
    )
    assert profile["mode"] == "enforce"
    assert profile["trajectory"] == "sro_first"


def test_classify_medium_text_is_advisory() -> None:
    decision = _decision("force_sro", code="long_document", preview=True)
    profile = classify_claude_gate(_info(Path("a.md"), type="text", size=5000), decision)
    assert profile["mode"] == "advisory"
    assert profile["trajectory"] == "optional"
    assert profile["hook_can_inject_context"] is True


def test_classify_structured_analysis_plan_is_enforce_with_one_collect() -> None:
    decision = _decision("force_sro", code="structured_analysis_plan", preview=True, scope_kind="collection")
    profile = classify_claude_gate(_info(Path("dir"), type="collection", size=50000), decision)
    assert profile["mode"] == "enforce"
    assert profile["trajectory"] == "one_collect_then_write"
    assert profile["prompt_style"] == "closure_once"


def test_classify_advisory_keeps_optional_profile() -> None:
    profile = classify_claude_gate(_info(Path("a.csv"), type="csv", size=5000, structured=True), _decision("advisory"))
    assert profile["mode"] == "advisory"
    assert profile["trajectory"] == "optional"
    assert profile["hook_can_inject_context"] is True
    assert profile["nudge_native"] is True


def test_classify_unknown_force_code_downgrades_to_advisory() -> None:
    profile = classify_claude_gate(
        _info(Path("a.txt"), type="text", size=50000), _decision("force_sro", code="future_code")
    )
    assert profile["mode"] == "advisory"


def test_bridge_preview_raw_trace_protocol() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        target = workspace / "report.md"
        target.write_text("# Report\n\nROOT_CAUSE: cache invalidation used tenant_id.\n", encoding="utf-8")
        bridge = ClaudeBridge(workspace=workspace)
        version = bridge.handle({"method": "version"})
        assert version["protocol_version"] == "1.0"
        preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
        assert "preview_pack" in preview
        raw_ref = preview["preview_pack"]["raw_ref"]
        raw = bridge.handle({"method": "raw", "params": {"raw_ref": raw_ref}})
        assert "ROOT_CAUSE" in str(raw.get("raw", {}))
        trace = bridge.handle({"method": "trace"})
        assert trace["summary"]["sro_preview_calls"] >= 1


def test_ready_guard_stops_repeat_reads() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        target = workspace / "report.md"
        target.write_text("# Report\n\nThe planted answer is 5705.\n", encoding="utf-8")
        bridge = ClaudeBridge(workspace=workspace, mode="force_sro")
        preview = bridge.handle({"method": "preview", "params": {"path": str(target)}})
        artifact_id = preview["preview_pack"]["artifact_id"]
        hint = {
            "goal": "find the planted answer",
            "slots": [{"id": "answer", "question": "What is the planted answer?", "expected": "number"}],
            "type_hint": "text",
        }
        first = bridge.handle(
            {"method": "read", "params": {"target": {"artifact_id": artifact_id}, "mode": "collect", "hint": hint}}
        )
        assert first["evidence_pack"]["slot_digest"]["overall_status"] == "ready"
        second = bridge.handle(
            {"method": "read", "params": {"target": {"artifact_id": artifact_id}, "mode": "collect", "hint": hint}}
        )
        assert second["evidence_pack"].get("protocol_next") == "write_file_now"
        assert "ready guard" in second["evidence_pack"]["summary"]


def test_mcp_handle_tool_preview_decide_trace_usage_unknown() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        target = workspace / "report.md"
        target.write_text("# Report\n\nROOT_CAUSE: cache invalidation used tenant_id.\n", encoding="utf-8")
        mcp = SparseReadClaudeMCP(workspace=str(workspace))
        preview = json.loads(mcp.handle_tool("sro_preview", {"path": str(target)}))
        assert "preview_pack" in preview
        decide = json.loads(mcp.handle_tool("sro_decide", {"path": str(target)}))
        assert "decision" in decide or "mode" in str(decide)
        trace = json.loads(mcp.handle_tool("sro_trace", {}))
        assert "summary" in trace
        usage = json.loads(mcp.handle_tool("sro_usage", {}))
        assert "token_tracker" in usage
        unknown = json.loads(mcp.handle_tool("sro_nope", {}))
        assert "error" in unknown


def test_estimate_tokens_basics() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") >= 1
    assert estimate_file_tokens(4000, ".txt") == 1000
    assert estimate_file_tokens(4000, ".json") == 1333
