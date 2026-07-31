"""Claude Code PreToolUse Hook — SparseRead Gate.

Intercepts Read and Bash tool calls, checks whether the target qualifies
for SparseRead, and either:
  - blocks (exit 2) + injects additionalContext telling Claude to use sro_preview
  - allows (exit 0) for small files and native-safe operations

Stdlib-only for fast startup. Called by Claude Code before every Read/Bash
tool invocation.

Configuration in .claude/settings.local.json:
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["<project_root>/integrations/claude/hooks/claude_hook.py"]
          }
        ]
      }
    ]
  }
}
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration — tune thresholds here
# ---------------------------------------------------------------------------

# Files with these suffixes are always treated as "code" and read natively
# when below CODE_NATIVE_BYTES.
CODE_SUFFIXES: set[str] = {
    ".py", ".rs", ".js", ".ts", ".jsx", ".tsx", ".go", ".c", ".h",
    ".cpp", ".hpp", ".java", ".kt", ".swift", ".rb", ".php",
    ".sh", ".bash", ".zsh", ".fish",
    ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".conf",
    ".css", ".scss", ".html", ".xml", ".svg",
    ".sql", ".r", ".m", ".jl",
}

# Files with these suffixes are "text-like" and checked for SRO at the
# TEXT_ENFORCE threshold.
TEXT_SUFFIXES: set[str] = {
    ".md", ".txt", ".rst", ".log", ".csv", ".tsv",
}

# Files up to this size can always be read natively (4 KB).
CODE_NATIVE_BYTES = 4096

# Text files above this size trigger SRO enforcement (12 KB).
TEXT_ENFORCE_BYTES = 12288

# Directories with more than this many files trigger SRO enforcement.
DIR_ENFORCE_COUNT = 3

# Generate cache-busting (for generated output files that should stay native).
GENERATED_NAMES: set[str] = {
    "fetch-audit", "summary_report", "output", "result", "build", "dist",
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

# In-memory cache for the lifetime of this process.  Claude Code may invoke
# the hook script fresh each time (stateless), but we keep the cache pattern
# for future stateful hook modes.
_cache: dict[str, str | None] = {}


def _is_generated_path(path: Path) -> bool:
    """Heuristic: does this path look like a generated/runtime artifact?"""
    name_lower = path.name.lower()
    # Files in output directories
    parts = path.parts
    for part in parts:
        if part.lower() in {".git", "__pycache__", "node_modules", ".pytest_cache",
                             "dist", "build", ".sro", ".claude"}:
            return True
    # Output-like names
    for gen_name in GENERATED_NAMES:
        if gen_name in name_lower:
            return True
    return False


def check_file(path_str: str) -> str | None:
    """Decide whether the file at *path_str* should be handled by SparseRead.

    Returns:
      'enforce' — block native read, redirect to sro_preview
      'native'  — allow native read
      None      — cannot determine, allow
    """
    if not path_str or not path_str.strip():
        return None

    path_str = path_str.strip()

    if path_str in _cache:
        return _cache[path_str]

    p = Path(path_str)
    if not p.is_absolute():
        # Relative path — resolve against CWD
        p = Path(os.getcwd()) / p

    if not p.exists():
        _cache[path_str] = None
        return None

    # Generated/runtime artifacts always stay native
    if _is_generated_path(p):
        _cache[path_str] = "native"
        return "native"

    suffix = p.suffix.lower()

    # PDF → always enforce SRO
    if suffix == ".pdf":
        _cache[path_str] = "enforce"
        return "enforce"

    try:
        size = p.stat().st_size
    except OSError:
        _cache[path_str] = None
        return None

    # Small code/config files → native
    if suffix in CODE_SUFFIXES and size < CODE_NATIVE_BYTES:
        _cache[path_str] = "native"
        return "native"

    # Large text files → enforce
    if suffix in TEXT_SUFFIXES and size > TEXT_ENFORCE_BYTES:
        _cache[path_str] = "enforce"
        return "enforce"

    # Large data files → enforce
    if suffix in {".csv", ".tsv", ".json"} and size > TEXT_ENFORCE_BYTES:
        _cache[path_str] = "enforce"
        return "enforce"

    # Directories → enforce if many files
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
    """Extract the target file path from a Read tool input."""
    return str(tool_input.get("file_path", ""))


def extract_path_from_bash(tool_input: dict) -> str | None:
    """Extract a single file path from a Bash command that looks like a raw dump.

    Only matches simple cat/head/tail/less/more commands.
    """
    command = str(tool_input.get("command", "")).strip()
    # Match: cat/head/tail/less/more <path>
    match = re.match(
        r'^(cat|head|tail|less|more)\s+'
        r'(?:"([^"]+)"|\'([^\']+)\'|(\S+))',
        command,
    )
    if not match:
        return None
    # Return the first non-None group (quoted or bare)
    for g in match.groups()[1:]:
        if g is not None:
            return g
    return None


def format_block_response(path: str, reason: str) -> dict:
    """Build the block (permissionDecision: deny) response."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"SparseRead: {reason}"
            ),
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
    """Build the allow response."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def handle_read(tool_input: dict) -> None:
    """Handle a Read tool call."""
    path = extract_path_from_read(tool_input)
    decision = check_file(path)

    if decision == "enforce":
        resp = format_block_response(
            path,
            "large file or PDF — use sro_preview for targeted reading",
        )
        print(json.dumps(resp))
        sys.exit(2)

    # Allow: small file, unsupported type, or could not determine
    resp = format_allow_response()
    print(json.dumps(resp))
    sys.exit(0)


def handle_bash(tool_input: dict) -> None:
    """Handle a Bash tool call — only block cat/head/tail on SRO targets."""
    path = extract_path_from_bash(tool_input)

    if path is None:
        # Not a raw-dump command → allow
        resp = format_allow_response()
        print(json.dumps(resp))
        sys.exit(0)

    decision = check_file(path)

    if decision == "enforce":
        resp = format_block_response(
            path,
            "use sro_preview instead of shell commands for large files",
        )
        print(json.dumps(resp))
        sys.exit(2)

    # Allow
    resp = format_allow_response()
    print(json.dumps(resp))
    sys.exit(0)


def main() -> None:
    """Read PreToolUse event from stdin, decide, write response to stdout."""
    try:
        raw = sys.stdin.read()
    except (IOError, OSError):
        # If stdin is empty or unreadable, allow by default
        resp = format_allow_response()
        print(json.dumps(resp))
        sys.exit(0)

    if not raw or not raw.strip():
        resp = format_allow_response()
        print(json.dumps(resp))
        sys.exit(0)

    try:
        input_data = json.loads(raw)
    except json.JSONDecodeError:
        resp = format_allow_response()
        print(json.dumps(resp))
        sys.exit(0)

    tool_name = str(input_data.get("tool_name", ""))
    tool_input = input_data.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool_name == "Read":
        handle_read(tool_input)
    elif tool_name == "Bash":
        handle_bash(tool_input)
    else:
        # Other tools: allow
        resp = format_allow_response()
        print(json.dumps(resp))
        sys.exit(0)


if __name__ == "__main__":
    main()
