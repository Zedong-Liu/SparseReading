"""Claude Code Session-Mode Hook — SparseRead Deep Gate.

Runs as a long-lived process (session hook) communicating with Claude Code
via stdin/stdout JSON-lines protocol.  Uses the FULL BenefitGate pipeline
(identical to the MCP bridge) for gate decisions, fixing 15 mismatches
between the old simple hook and the bridge.

PreToolUse events:
  - Read / Bash → inspect_file → BenefitGate.decide → classify_claude_gate
  - enforce  → deny (exit 2 equivalent) + additionalContext pointing to sro_preview
  - advisory → allow + additionalContext with SRO recommendation
  - native   → allow (no intervention)

PostToolUse events:
  - After large native Read/Bash output → append nudge to updatedToolOutput

Usage:
  uv run --project nanobot-sro-v3 python -m sparseread.hooks.claude_hook --workspace .

Configuration (.claude/settings.local.json):
  {
    "hooks": {
      "PreToolUse": [{
        "matcher": "Read|Bash",
        "hooks": [{
          "type": "session",
          "command": "uv",
          "args": ["--project", "<PROJECT>/nanobot-sro-v3", "run",
                   "python", "-m", "sparseread.hooks.claude_hook", "--workspace", "."]
        }]
      }],
      "PostToolUse": [{
        "matcher": "Read|Bash",
        "hooks": [{
          "type": "session",
          "command": "uv",
          "args": ["--project", "<PROJECT>/nanobot-sro-v3", "run",
                   "python", "-m", "sparseread.hooks.claude_hook", "--workspace", "."]
        }]
      }]
    }
  }
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# Force UTF-8 on Windows to avoid GBK encoding errors with Unicode chars
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from nanobot.sparse_reading.benefit_gate import BenefitGate, BenefitDecision
from nanobot.sparse_reading.detector import FileInfo, inspect_file
from nanobot.sparse_reading.readers.collection import CollectionReader
from sparseread.bridge.claude import classify_claude_gate, CLAUDE_TEXT_ENFORCE_BYTES


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# PostToolUse: minimum native output chars before we append a SRO nudge
POST_NUDGE_CHARS = int(os.environ.get("SRO_POST_NUDGE_CHARS", "5000"))

# Maximum cache entries (LRU eviction via FIFO truncation)
MAX_CACHE = int(os.environ.get("SRO_HOOK_CACHE_SIZE", "512"))


# ---------------------------------------------------------------------------
# Gate engine (shared with bridge)
# ---------------------------------------------------------------------------


def _make_gate_engine(workspace: str | None = None) -> tuple[BenefitGate, str | None]:
    """Create a BenefitGate instance for gate decisions."""
    collection_reader = CollectionReader()
    override = None
    env_override = os.environ.get("SRO_BENEFIT_GATE_OVERRIDE", "").strip().lower()
    if env_override in {"force_sro", "native", "advisory"}:
        override = env_override
    gate = BenefitGate(collection_reader, override=override)
    return gate, workspace


# ---------------------------------------------------------------------------
# Path extraction from tool inputs
# ---------------------------------------------------------------------------


def _resolve_path(raw: str, workspace: str | None = None) -> Path | None:
    """Resolve a path string to an absolute Path, or None if unresolvable."""
    if not raw or not raw.strip():
        return None
    raw = raw.strip().strip('"').strip("'")
    try:
        p = Path(raw)
        if not p.is_absolute() and workspace:
            p = Path(workspace) / p
        return p.resolve(strict=False)
    except Exception:
        return None


def _extract_read_path(tool_input: dict[str, Any]) -> str:
    """Extract the file path from a Read tool input."""
    return str(tool_input.get("file_path", ""))


def _extract_bash_paths(tool_input: dict[str, Any]) -> list[str]:
    """Extract file paths from Bash commands (cat/head/tail/less/more/rg/grep/pdftotext).

    For rg/grep, the pattern comes first and the file path comes last.
    We extract all space-separated arguments that look like file paths.
    """
    command = str(tool_input.get("command", "")).strip()

    # Match the command name
    cmd_match = re.match(
        r'^(cat|head|tail|less|more|pdftotext|rg|grep)\s',
        command,
    )
    if not cmd_match:
        return []

    cmd_name = cmd_match.group(1)
    # Get everything after the command name
    args_str = command[cmd_match.end():].strip()

    # For rg/grep: the first quoted arg is the pattern, subsequent args are paths
    # For cat/head/tail: the first arg is the file path
    paths: list[str] = []

    if cmd_name in ("rg", "grep"):
        # rg/grep: pattern first (often quoted), then file paths
        # Skip the pattern argument (first quoted string), extract remaining paths
        # Pattern example: rg 'pattern' file1 file2  OR  rg -n 'pattern' file1
        remaining = args_str
        # Strip flags first
        remaining = re.sub(r'^(-\S+\s*)+', '', remaining).strip()
        # Strip the pattern (first quoted or unquoted arg)
        remaining = re.sub(r'^(["\'](?:[^"\\]|\\.)*["\']\s*|\S+\s+)', '', remaining).strip()
        # Remaining args are file paths
        if remaining:
            # Extract each path-like argument
            for part in re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', remaining):
                for g in part:
                    if g:
                        paths.append(g)
    else:
        # cat/head/tail/less/more/pdftotext: path comes right after command
        for part in re.findall(r'"([^"]+)"|\'([^\']+)\'|(\S+)', args_str):
            for g in part:
                if g:
                    paths.append(g)
                    break  # Only first path for cat/head/tail
            break

    return paths


def _has_read_constraints(tool_input: dict[str, Any]) -> bool:
    """Check if the read has offset/limit/pages constraints (partial read)."""
    return bool(
        tool_input.get("offset") or tool_input.get("limit") or tool_input.get("pages")
    )


# ---------------------------------------------------------------------------
# Path classification (fast pre-check before BenefitGate)
# ---------------------------------------------------------------------------

# Paths that should always bypass SRO (generated/runtime artifacts)
_GENERATED_PATH_PARTS = {
    ".git", "__pycache__", "node_modules", ".pytest_cache",
    "dist", "build", ".sro", ".claude", ".nanobot", "sessions",
    "bootstrap", "skills", "memory",
}

_GENERATED_OUTPUT_NAMES = {
    "answer.txt", "command_classifications.json", "fetch-audit.md",
    "final_answer.md", "diagnosis_report.md", "did_results_summary.md",
    "metrics_summary.json", "monitoring-status.md", "output.json",
    "result.json", "summary_report.md", "summary.csv",
}


def _is_generated_or_runtime(path: Path) -> bool:
    """Check if a path is a generated/runtime artifact that should never
    enter SparseRead (prevents re-entry and infinite loops)."""
    # Check path parts for known skip directories
    for part in path.parts:
        if part in _GENERATED_PATH_PARTS:
            return True
    # Check filename against known output names
    if path.name.lower() in _GENERATED_OUTPUT_NAMES:
        return True
    # Check for fetch-audit and similar generated names
    name_lower = path.name.lower()
    for marker in ("fetch-audit", "summary_report", "final_answer", "diagnosis_"):
        if marker in name_lower:
            return True
    return False


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


class HookSession:
    """Per-session state for the hook process.

    Maintains a cache of gate decisions, a set of already-blocked paths
    (one-time block strategy), and counters for diagnostics.
    """

    def __init__(self, gate: BenefitGate, workspace: str | None = None) -> None:
        self.gate = gate
        self.workspace = workspace
        self._cache: dict[str, dict[str, Any]] = {}  # path → gate profile
        self._blocked: set[str] = set()  # paths already blocked once
        self._block_count = 0
        self._allow_count = 0
        self._nudge_count = 0

    def decide(self, path_str: str, tool_name: str = "Read") -> dict[str, Any]:
        """Run the full gate pipeline and return a decision dict.

        Returns:
          {
            "decision": "enforce" | "advisory" | "native",
            "gate": {...},         # full classify_claude_gate profile
            "info_type": str,      # FileInfo.type
            "info_size": int,      # FileInfo.size_bytes
            "reason": str,
            "cached": bool,
          }
        """
        # Resolve path
        resolved = _resolve_path(path_str, self.workspace)
        if resolved is None:
            return {
                "decision": "native",
                "gate": {"mode": "native", "reason": "unresolvable path"},
                "info_type": "unknown",
                "info_size": 0,
                "reason": "unresolvable path",
                "cached": False,
            }

        path_key = str(resolved)

        # Check cache — but apply one-time-block downgrade even for cached results
        if path_key in self._cache:
            cached = dict(self._cache[path_key])
            cached["cached"] = True
            # One-time-block: if cached as enforce but path was already blocked,
            # downgrade to advisory
            if cached.get("decision") == "enforce" and path_key in self._blocked:
                cached = dict(cached)
                cached["decision"] = "advisory"
                cached["gate"] = dict(cached.get("gate", {}))
                cached["gate"]["mode"] = "advisory"
                cached["gate"]["reason"] = (
                    f"previously blocked; allowing retry with SRO context. "
                    f"Original: {cached['gate'].get('reason', '')}"
                )
            return cached

        # Fast skip: generated/runtime artifacts
        if _is_generated_or_runtime(resolved):
            result = {
                "decision": "native",
                "gate": {
                    "mode": "native",
                    "reason": "generated/runtime artifact — never re-enter SparseRead",
                    "hook_can_block_read": False,
                    "hook_can_block_bash": False,
                    "hook_can_inject_context": False,
                    "trajectory": "native",
                },
                "info_type": "generated",
                "info_size": 0,
                "reason": "generated/runtime artifact",
                "cached": False,
            }
            self._cache[path_key] = result
            self._prune_cache()
            return result

        # Fast skip: non-existent path
        if not resolved.exists():
            result = {
                "decision": "native",
                "gate": {
                    "mode": "native",
                    "reason": "path does not exist",
                    "hook_can_block_read": False,
                    "hook_can_block_bash": False,
                    "hook_can_inject_context": False,
                    "trajectory": "native",
                },
                "info_type": "missing",
                "info_size": 0,
                "reason": "path does not exist",
                "cached": False,
            }
            self._cache[path_key] = result
            self._prune_cache()
            return result

        # Full BenefitGate pipeline
        try:
            info: FileInfo = inspect_file(resolved)
            benefit_decision: BenefitDecision = self.gate.decide(info)
            gate_profile: dict[str, Any] = classify_claude_gate(info, benefit_decision)
        except Exception:
            # On any error, allow native read (fail open)
            result = {
                "decision": "native",
                "gate": {
                    "mode": "native",
                    "reason": "gate pipeline error — fail open",
                    "hook_can_block_read": False,
                    "hook_can_block_bash": False,
                    "hook_can_inject_context": False,
                    "trajectory": "native",
                },
                "info_type": "error",
                "info_size": 0,
                "reason": "gate pipeline error",
                "cached": False,
            }
            self._cache[path_key] = result
            self._prune_cache()
            return result

        # Map gate profile mode to our three-way decision
        gate_mode = gate_profile.get("mode", "native")

        # Has this path already been blocked? One-time-block strategy:
        # after blocking once, subsequent attempts get advisory (allow + nudge)
        if gate_mode == "enforce" and path_key in self._blocked:
            gate_mode = "advisory"
            gate_profile = dict(gate_profile)
            gate_profile["mode"] = "advisory"
            gate_profile["reason"] = (
                f"previously blocked; allowing retry with SRO context. "
                f"Original: {gate_profile.get('reason', '')}"
            )

        result = {
            "decision": gate_mode,
            "gate": gate_profile,
            "info_type": info.type,
            "info_size": info.size_bytes,
            "reason": gate_profile.get("reason", ""),
            "cached": False,
        }

        self._cache[path_key] = result
        self._prune_cache()
        return result

    def mark_blocked(self, path_str: str) -> None:
        """Record that a path has been blocked."""
        resolved = _resolve_path(path_str, self.workspace)
        if resolved:
            self._blocked.add(str(resolved))
            self._block_count += 1

    def mark_allowed(self) -> None:
        self._allow_count += 1

    def mark_nudged(self) -> None:
        self._nudge_count += 1

    @property
    def stats(self) -> dict[str, int]:
        return {
            "cache_size": len(self._cache),
            "blocked_paths": len(self._blocked),
            "blocks": self._block_count,
            "allows": self._allow_count,
            "nudges": self._nudge_count,
        }

    def _prune_cache(self) -> None:
        """FIFO eviction when cache exceeds MAX_CACHE."""
        while len(self._cache) > MAX_CACHE:
            oldest = next(iter(self._cache))
            del self._cache[oldest]


# ---------------------------------------------------------------------------
# Response formatters
# ---------------------------------------------------------------------------


def _format_block(path: str, reason: str, info_type: str = "") -> dict[str, Any]:
    """Format a 'deny' response that blocks the tool and injects SRO context."""
    type_hint = f" ({info_type})" if info_type else ""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"SparseRead enforce{type_hint}: {reason}",
            "additionalContext": (
                f"⚠️ SparseRead blocked native read on {json.dumps(path)}.\n"
                f"Reason: {reason}\n\n"
                f"USE sro_preview(path={json.dumps(path)}) instead.\n"
                f"sro_preview returns structure overview, content samples, "
                f"key signals, and next_action guidance.\n\n"
                f"After preview:\n"
                f"  - If the preview is sufficient → answer directly\n"
                f"  - If you need specific evidence → "
                f"sro_read(target={{'artifact_id': ...}}, mode='collect', hint={{...}})\n"
                f"  - When sro_read returns 'ready' → write deliverable immediately\n"
                f"Do NOT retry read_file on this path — use sro_preview."
            ),
        }
    }


def _format_advisory(path: str, reason: str) -> dict[str, Any]:
    """Format an 'allow' response with SRO advisory context injected."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "additionalContext": (
                f"💡 SparseRead advisory: {reason}\n"
                f"Consider using sro_preview(path={json.dumps(path)}) "
                f"for more efficient reading."
            ),
        }
    }


def _format_allow() -> dict[str, Any]:
    """Format a simple 'allow' response."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def _format_post_nudge(path: str, output_size: int, tool_output: dict[str, Any]) -> dict[str, Any]:
    """Format a PostToolUse response that appends an SRO nudge to output."""
    updated = dict(tool_output)
    nudge = (
        f"\n\n---\n"
        f"💡 **SparseRead tip**: This was a large native read "
        f"({output_size:,} chars from {json.dumps(path)}).\n"
        f"Next time, try `sro_preview(path={json.dumps(path)})` first "
        f"for targeted evidence extraction — it can save significant "
        f"context window space."
    )
    # Append nudge to the content field if present
    if "content" in updated and isinstance(updated["content"], str):
        updated["content"] = updated["content"] + nudge
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": updated,
        }
    }


def _format_post_passthrough() -> dict[str, Any]:
    """Format a PostToolUse passthrough (no modification)."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
        }
    }


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------


def handle_pretooluse(
    session: HookSession,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    """Handle a PreToolUse event — gate check for Read and Bash tools."""

    if tool_name == "Read":
        path = _extract_read_path(tool_input)
        if not path:
            session.mark_allowed()
            return _format_allow()

        # Don't block partial reads (offset/limit/pages constrained)
        if _has_read_constraints(tool_input):
            session.mark_allowed()
            return _format_allow()

        decision = session.decide(path, tool_name="Read")

        if decision["decision"] == "enforce":
            session.mark_blocked(path)
            return _format_block(path, decision["reason"], decision.get("info_type", ""))

        if decision["decision"] == "advisory":
            session.mark_nudged()
            return _format_advisory(path, decision["reason"])

        # native
        session.mark_allowed()
        return _format_allow()

    if tool_name == "Bash":
        paths = _extract_bash_paths(tool_input)
        if not paths:
            session.mark_allowed()
            return _format_allow()

        # Check each path; block if ANY path is enforce
        for path in paths:
            decision = session.decide(path, tool_name="Bash")
            if decision["decision"] == "enforce":
                session.mark_blocked(path)
                return _format_block(path, decision["reason"], decision.get("info_type", ""))

        # All paths are advisory or native — allow with nudge if any advisory
        for path in paths:
            decision = session.decide(path, tool_name="Bash")
            if decision["decision"] == "advisory":
                session.mark_nudged()
                return _format_advisory(path, decision["reason"])

        session.mark_allowed()
        return _format_allow()

    # Unknown tool — always allow
    session.mark_allowed()
    return _format_allow()


def handle_posttooluse(
    session: HookSession,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: dict[str, Any] | None,
) -> dict[str, Any]:
    """Handle a PostToolUse event — nudge after large native reads."""

    if tool_output is None:
        return _format_post_passthrough()

    # Serialize output to estimate size
    try:
        output_str = json.dumps(tool_output, ensure_ascii=False)
    except (TypeError, ValueError):
        return _format_post_passthrough()

    output_size = len(output_str)

    if output_size < POST_NUDGE_CHARS:
        return _format_post_passthrough()

    # Determine the path
    if tool_name == "Read":
        path = _extract_read_path(tool_input)
    elif tool_name == "Bash":
        paths = _extract_bash_paths(tool_input)
        path = paths[0] if paths else ""
    else:
        return _format_post_passthrough()

    if not path:
        return _format_post_passthrough()

    # Don't nudge on already-enforced paths
    decision = session.decide(path, tool_name=tool_name)
    if decision["decision"] != "enforce":
        return _format_post_passthrough()

    session.mark_nudged()
    return _format_post_nudge(path, output_size, tool_output)


# ---------------------------------------------------------------------------
# Main loop (session mode)
# ---------------------------------------------------------------------------


def _parse_event(raw: str) -> dict[str, Any] | None:
    """Parse a JSON event from stdin. Returns None on parse failure."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, TypeError):
        return None


def run_session(workspace: str | None = None) -> int:
    """Run the hook in session mode: read JSON events from stdin in a loop.

    Each line on stdin is a JSON event from Claude Code.
    Each response is a JSON line on stdout.
    The process stays alive until stdin closes.
    """
    gate, _ws = _make_gate_engine(workspace)
    session = HookSession(gate, workspace=workspace)

    # Signal readiness (session mode init)
    init_msg = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "permissionDecision": "allow",
            }
        },
        ensure_ascii=False,
    )
    sys.stdout.write(init_msg + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        event = _parse_event(line)
        if event is None:
            # Unparseable event — allow by default (fail open)
            resp = _format_allow()
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        hook_event_name = str(event.get("hook_event_name", event.get("hookEventName", "")))

        if hook_event_name == "PreToolUse":
            tool_name = str(event.get("tool_name", event.get("toolName", "")))
            tool_input = event.get("tool_input", event.get("toolInput", {}))
            if not isinstance(tool_input, dict):
                tool_input = {}
            resp = handle_pretooluse(session, tool_name, tool_input)

        elif hook_event_name == "PostToolUse":
            tool_name = str(event.get("tool_name", event.get("toolName", "")))
            tool_input = event.get("tool_input", event.get("toolInput", {}))
            if not isinstance(tool_input, dict):
                tool_input = {}
            tool_output = event.get("tool_output", event.get("toolOutput"))
            resp = handle_posttooluse(session, tool_name, tool_input, tool_output)

        elif hook_event_name in ("Stop", "Shutdown", "Notification"):
            # Session ending — log stats and exit cleanly
            stats = session.stats
            sys.stderr.write(
                f"[sparseread-hook] session end — "
                f"blocks={stats['blocks']} allows={stats['allows']} "
                f"nudges={stats['nudges']} cache={stats['cache_size']}\n"
            )
            sys.stderr.flush()
            sys.exit(0)

        else:
            # Unknown event type — allow
            resp = _format_allow()

        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    # Stdin closed — clean exit
    stats = session.stats
    sys.stderr.write(
        f"[sparseread-hook] stdin closed — "
        f"blocks={stats['blocks']} allows={stats['allows']} "
        f"nudges={stats['nudges']} cache={stats['cache_size']}\n"
    )
    sys.stderr.flush()
    return 0


# ---------------------------------------------------------------------------
# Single-shot mode (for testing / debugging)
# ---------------------------------------------------------------------------


def run_single(workspace: str | None = None) -> int:
    """Run the hook in single-shot mode: read one event, write one response.

    Used for testing and for command-type hooks (backward compatible).
    """
    gate, _ws = _make_gate_engine(workspace)
    session = HookSession(gate, workspace=workspace)

    try:
        raw = sys.stdin.read()
        event = _parse_event(raw)
    except Exception:
        resp = _format_allow()
        print(json.dumps(resp, ensure_ascii=False))
        return 0

    if event is None:
        resp = _format_allow()
        print(json.dumps(resp, ensure_ascii=False))
        return 0

    hook_event_name = str(event.get("hook_event_name", event.get("hookEventName", "")))
    tool_name = str(event.get("tool_name", event.get("toolName", "")))
    tool_input = event.get("tool_input", event.get("toolInput", {}))
    if not isinstance(tool_input, dict):
        tool_input = {}

    if hook_event_name in ("PreToolUse", ""):
        # Default to PreToolUse for backward compat (old hook format had no event name)
        resp = handle_pretooluse(session, tool_name, tool_input)
    elif hook_event_name == "PostToolUse":
        tool_output = event.get("tool_output", event.get("toolOutput"))
        resp = handle_posttooluse(session, tool_name, tool_input, tool_output)
    else:
        resp = _format_allow()

    print(json.dumps(resp, ensure_ascii=False))

    # Use exit code 2 for deny (backward compat with command-mode hooks)
    permission = (
        resp.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    )
    if permission == "deny":
        return 2
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SparseRead Claude Code Hook (session-mode gate + nudge)",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace root directory (default: current directory)",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        default=True,
        help="Run in session mode (default)",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        default=False,
        help="Run in single-shot mode (for testing / command-type hooks)",
    )
    parser.add_argument(
        "--post-tool-use",
        action="store_true",
        default=False,
        help="Only handle PostToolUse events (for dedicated PostToolUse hook config)",
    )
    args = parser.parse_args(argv)

    workspace = str(Path(args.workspace).resolve()) if args.workspace else None

    if args.single:
        return run_single(workspace)

    return run_session(workspace)


if __name__ == "__main__":
    raise SystemExit(main())
