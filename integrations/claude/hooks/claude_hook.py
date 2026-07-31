"""Claude Code PreToolUse Hook — SparseRead Gate.

Intercepts Read and Bash tool calls, checks whether the target qualifies
for SparseRead, and either:
  - blocks (exit 2) + injects additionalContext telling Claude to use sro_preview
  - allows (exit 0) for small files and native-safe operations

Stdlib-only for fast startup. Called by Claude Code before every Read/Bash
tool invocation.
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path

# Force UTF-8 output on Windows — avoid GBK encoding crashes with emoji
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CODE_SUFFIXES: set[str] = {
    ".py", ".rs", ".js", ".ts", ".jsx", ".tsx", ".go", ".c", ".h",
    ".cpp", ".hpp", ".java", ".kt", ".swift", ".rb", ".php",
    ".sh", ".bash", ".zsh", ".fish",
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf",
    ".css", ".scss", ".html", ".xml", ".svg",
    ".sql", ".r", ".m", ".jl",
}

TEXT_SUFFIXES: set[str] = {".md", ".txt", ".rst", ".log", ".csv", ".tsv"}

CODE_NATIVE_BYTES = 4096
TEXT_ENFORCE_BYTES = 12288
DIR_ENFORCE_COUNT = 3

GENERATED_NAMES: set[str] = {
    "fetch-audit", "summary_report", "output", "result", "build", "dist",
}

_cache: dict[str, str | None] = {}


def _is_generated_path(path: Path) -> bool:
    name_lower = path.name.lower()
    for part in path.parts:
        if part.lower() in {".git", "__pycache__", "node_modules",
                             ".pytest_cache", "dist", "build", ".sro", ".claude"}:
            return True
    for gen_name in GENERATED_NAMES:
        if gen_name in name_lower:
            return True
    return False


def check_file(path_str: str) -> str | None:
    if not path_str or not path_str.strip():
        return None
    path_str = path_str.strip()
    if path_str in _cache:
        return _cache[path_str]
    p = Path(path_str)
    if not p.is_absolute():
        p = Path(os.getcwd()) / p
    if not p.exists():
        _cache[path_str] = None
        return None
    if _is_generated_path(p):
        _cache[path_str] = "native"
        return "native"
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        _cache[path_str] = "enforce"
        return "enforce"
    try:
        size = p.stat().st_size
    except OSError:
        _cache[path_str] = None
        return None
    if suffix in CODE_SUFFIXES and size < CODE_NATIVE_BYTES:
        _cache[path_str] = "native"
        return "native"
    if suffix in TEXT_SUFFIXES and size > TEXT_ENFORCE_BYTES:
        _cache[path_str] = "enforce"
        return "enforce"
    if suffix in {".csv", ".tsv", ".json"} and size > TEXT_ENFORCE_BYTES:
        _cache[path_str] = "enforce"
        return "enforce"
    if p.is_dir():
        try:
            count = sum(1 for _ in p.iterdir())
        except OSError:
            count = 0
        if count > DIR_ENFORCE_COUNT:
            _cache[path_str] = "enforce"
            return "enforce"
    _cache[path_str] = None
    return None


def extract_path_from_read(tool_input: dict) -> str:
    return str(tool_input.get("file_path", ""))


def extract_path_from_bash(tool_input: dict) -> str | None:
    command = str(tool_input.get("command", "")).strip()
    match = re.match(
        r'^(cat|head|tail|less|more)\s+'
        r'(?:"([^"]+)"|\'([^\']+)\'|(\S+))',
        command,
    )
    if not match:
        return None
    for g in match.groups()[1:]:
        if g is not None:
            return g
    return None


def format_block_response(path: str, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"SparseRead: {reason}",
            "additionalContext": (
                f"⚠️ SparseRead gate blocked native read on {json.dumps(path)}.\n"
                f"Reason: {reason}\n"
                f"USE sro_preview(path={json.dumps(path)}) instead.\n"
                f"sro_preview returns structure overview, content samples, key "
                f"signals, and next_action guidance — more efficient than "
                f"reading the entire file.\n"
                f"After preview: if you need specific evidence, call "
                f"sro_read(target={{'artifact_id': ...}}, mode='collect', hint={{...}}).\n"
                f"When sro_read returns 'ready', write the deliverable immediately."
            ),
        }
    }


def format_allow_response() -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def handle_read(tool_input: dict) -> None:
    path = extract_path_from_read(tool_input)
    decision = check_file(path)
    if decision == "enforce":
        resp = format_block_response(path, "large file or PDF — use sro_preview")
        print(json.dumps(resp, ensure_ascii=False))
        sys.exit(2)
    resp = format_allow_response()
    print(json.dumps(resp, ensure_ascii=False))
    sys.exit(0)


def handle_bash(tool_input: dict) -> None:
    path = extract_path_from_bash(tool_input)
    if path is None:
        resp = format_allow_response()
        print(json.dumps(resp, ensure_ascii=False))
        sys.exit(0)
    decision = check_file(path)
    if decision == "enforce":
        resp = format_block_response(path, "large file cat/head/tail — use sro_preview")
        print(json.dumps(resp, ensure_ascii=False))
        sys.exit(2)
    resp = format_allow_response()
    print(json.dumps(resp, ensure_ascii=False))
    sys.exit(0)


def main() -> None:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        resp = format_allow_response()
        print(json.dumps(resp, ensure_ascii=False))
        sys.exit(0)
    tool_name = str(data.get("tool_name", data.get("name", "")))
    tool_input = data.get("tool_input", data.get("input", {}))
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool_name == "Read":
        handle_read(tool_input)
    elif tool_name == "Bash":
        handle_bash(tool_input)
    else:
        resp = format_allow_response()
        print(json.dumps(resp, ensure_ascii=False))
        sys.exit(0)


if __name__ == "__main__":
    main()
