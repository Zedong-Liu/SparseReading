from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from sparseread_claude.hook import (
    HookSession,
    _extract_bash_paths,
    _format_advisory,
    _format_allow,
    _format_block,
    _format_post_nudge,
    _make_gate_engine,
    handle_pretooluse,
)


@pytest.fixture()
def session(tmp_workspace: Path) -> HookSession:
    return HookSession(_make_gate_engine(), workspace=str(tmp_workspace))


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


def _write(workspace: Path, name: str, size: int, *, suffix: str = ".txt") -> Path:
    path = workspace / f"{name}{suffix}"
    path.write_text("x" * size, encoding="utf-8")
    return path


def test_pdf_enforce(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "doc", 5000, suffix=".pdf")
    assert session.decide(str(path))["decision"] == "enforce"


def test_small_code_native(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "main", 2000, suffix=".py")
    assert session.decide(str(path))["decision"] == "native"


def test_structured_data_advisory(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "data", 3000, suffix=".csv")
    assert session.decide(str(path))["decision"] == "advisory"


def test_text_medium_advisory(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "medium", 5000)
    assert session.decide(str(path))["decision"] == "advisory"


def test_text_large_enforce(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "large", 13000)
    assert session.decide(str(path))["decision"] == "enforce"


def test_small_file_native(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "small", 100)
    assert session.decide(str(path))["decision"] == "native"


def test_generated_artifact_native(session: HookSession, tmp_workspace: Path) -> None:
    runtime = tmp_workspace / ".sparseread"
    runtime.mkdir()
    path = runtime / "result.json"
    path.write_text("{}", encoding="utf-8")
    assert session.decide(str(path))["decision"] == "native"


def test_nonexistent_path_native(session: HookSession, tmp_workspace: Path) -> None:
    assert session.decide(str(tmp_workspace / "missing.txt"))["decision"] == "native"


def test_collection_with_long_pdf_enforce(session: HookSession, tmp_workspace: Path) -> None:
    bundle = tmp_workspace / "bundle"
    bundle.mkdir()
    (bundle / "doc.pdf").write_bytes(b"P" * 5000)
    (bundle / "notes.txt").write_text("notes", encoding="utf-8")
    assert session.decide(str(bundle))["decision"] == "enforce"


def test_one_time_block(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "large", 13000)
    first = session.decide(str(path))
    assert first["decision"] == "enforce"
    session.mark_blocked(str(path))
    second = session.decide(str(path))
    assert second["decision"] == "advisory"
    assert "previously blocked" in second["gate"]["reason"]


def test_cache_hit(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "small", 100)
    assert session.decide(str(path))["cached"] is False
    assert session.decide(str(path))["cached"] is True


def test_extract_bash_paths() -> None:
    assert _extract_bash_paths({"command": "cat /tmp/a.txt"}) == ["/tmp/a.txt"]
    assert _extract_bash_paths({"command": 'head -n 5 "/tmp/a b.txt"'}) == ["/tmp/a b.txt"]
    assert _extract_bash_paths({"command": "rg -n 'pattern' /tmp/a.txt /tmp/b.txt"}) == [
        "/tmp/a.txt",
        "/tmp/b.txt",
    ]
    assert _extract_bash_paths({"command": "pdftotext /tmp/a.pdf -"}) == ["/tmp/a.pdf"]
    assert _extract_bash_paths({"command": "ls -la"}) == []


def test_format_block_denies_with_context() -> None:
    payload = _format_block("/tmp/a.pdf", "long PDF")
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "sro_preview" in payload["hookSpecificOutput"]["additionalContext"]


def test_format_advisory_allows_with_context() -> None:
    payload = _format_advisory("/tmp/a.csv", "structured advisory")
    assert payload["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "sro_preview" in payload["hookSpecificOutput"]["additionalContext"]


def test_format_allow() -> None:
    assert _format_allow()["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_format_post_nudge_appends_content() -> None:
    payload = _format_post_nudge("/tmp/a.txt", 9999, {"content": "hello"})
    output = payload["hookSpecificOutput"]["updatedToolOutput"]
    assert output["content"].startswith("hello")
    assert "SparseRead tip" in output["content"]


def test_read_large_file_block(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "large", 13000)
    resp = handle_pretooluse(session, "Read", {"file_path": str(path)})
    assert resp["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_read_small_file_allow(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "small", 100)
    resp = handle_pretooluse(session, "Read", {"file_path": str(path)})
    assert resp["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_read_with_constraints_allow(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "large", 13000)
    resp = handle_pretooluse(session, "Read", {"file_path": str(path), "offset": 1, "limit": 5})
    assert resp["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_bash_cat_large_file_block(session: HookSession, tmp_workspace: Path) -> None:
    path = _write(tmp_workspace, "large", 13000)
    resp = handle_pretooluse(session, "Bash", {"command": f"cat {path}"})
    assert resp["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_bash_non_file_allow(session: HookSession) -> None:
    resp = handle_pretooluse(session, "Bash", {"command": "ls -la"})
    assert resp["hookSpecificOutput"]["permissionDecision"] == "allow"
