"""Claude Code session hook for SparseRead.

Runs as a long-lived process (session hook) speaking Claude Code's JSON-lines
hook protocol.  All routing decisions come from the shared
``sparseread-core`` BenefitGate; this module only adds the Claude-specific
execution surface:

- PreToolUse Read/Bash: enforce -> deny + additionalContext,
  advisory -> allow + recommendation, native -> allow.
- PostToolUse: nudge after large native reads.
- one-time-block: a path is hard-blocked once, then downgraded to advisory so
  the model can proceed after SRO context has been injected.
- fail-open on any hook error.

Usage:
  python -m sparseread_claude.hook --workspace .

Claude Code config (written by the installer):
  .claude/settings.local.json -> PreToolUse/PostToolUse session hook.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from sparseread.core.benefit_gate import BenefitGate, GateContext
from sparseread.core.detector import FileInfo, inspect_file
from sparseread.core.readers.collection import CollectionReader

from sparseread_claude.bridge import CLAUDE_TEXT_ENFORCE_BYTES, classify_claude_gate


if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


POST_NUDGE_CHARS = int(os.environ.get("SRO_POST_NUDGE_CHARS", "5000"))
MAX_CACHE = int(os.environ.get("SRO_HOOK_CACHE_SIZE", "512"))

# Runtime/derived namespaces that must never re-enter SparseRead.  This is the
# same spirit as core detector skip dirs plus the Claude-specific workspace
# namespaces; no benchmark/task filenames are special-cased.
_RUNTIME_SKIP_DIRS = {
    ".git",
    ".opencode",
    ".openclaw",
    ".nanobot",
    ".claude",
    ".sparseread",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "sessions",
    "memory",
    "bootstrap",
    "skills",
}

_READ_LIKE_COMMANDS = {"cat", "head", "tail", "less", "more", "pdftotext", "rg", "grep"}


def _make_gate_engine() -> BenefitGate:
    """Create the shared deterministic BenefitGate for hook decisions."""
    return BenefitGate(CollectionReader(), override=None)


def _resolve_path(raw: str, workspace: str | None = None) -> Path | None:
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
    return str(tool_input.get("file_path", ""))


def _extract_bash_paths(tool_input: dict[str, Any]) -> list[str]:
    command = str(tool_input.get("command", "")).strip()
    cmd_match = re.match(r"^(cat|head|tail|less|more|pdftotext|rg|grep)\s", command)
    if not cmd_match:
        return []
    cmd_name = cmd_match.group(1)
    args_str = command[cmd_match.end() :].strip()

    if cmd_name in ("rg", "grep"):
        remaining = re.sub(r"^(-\S+\s*)+", "", args_str).strip()
        remaining = re.sub(r"^([\"'](?:[^\"\\]|\\.)*[\"']\s*|\S+\s+)", "", remaining).strip()
        if not remaining:
            return []
        paths: list[str] = []
        for part in re.findall(r"\"([^\"]+)\"|'([^']+)'|(\S+)", remaining):
            for group in part:
                if group:
                    paths.append(group)
        return paths

    try:
        tokens = shlex.split(args_str)
    except ValueError:
        return []
    skip_value = False
    for index, token in enumerate(tokens):
        if skip_value:
            skip_value = False
            continue
        if token.startswith("-"):
            if index + 1 < len(tokens) and re.fullmatch(r"\d+(?:\.\d+)?", tokens[index + 1]):
                skip_value = True
            continue
        return [token]
    return []


def _has_read_constraints(tool_input: dict[str, Any]) -> bool:
    return bool(
        tool_input.get("offset") or tool_input.get("limit") or tool_input.get("pages")
    )


def _episode_hint_from_input(tool_input: dict[str, Any]) -> GateContext:
    raw = tool_input.get("episode_hint")
    if isinstance(raw, dict):
        return GateContext.from_value(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return GateContext.from_value(parsed)
        except json.JSONDecodeError:
            pass
    return GateContext()


def _is_generated_or_runtime(path: Path) -> bool:
    return any(part in _RUNTIME_SKIP_DIRS for part in path.parts)


class HookSession:
    """Per-session hook state: decision cache, one-time-block set, counters."""

    def __init__(self, gate: BenefitGate, workspace: str | None = None) -> None:
        self.gate = gate
        self.workspace = workspace
        self._cache: dict[str, dict[str, Any]] = {}
        self._blocked: set[str] = set()
        self._block_count = 0
        self._allow_count = 0
        self._nudge_count = 0

    def decide(self, path_str: str, tool_name: str = "Read") -> dict[str, Any]:
        resolved = _resolve_path(path_str, self.workspace)
        if resolved is None:
            return self._native_result("__unresolvable__", "unresolvable path", "unknown", 0)
        path_key = str(resolved)

        if path_key in self._cache:
            cached = dict(self._cache[path_key])
            cached["cached"] = True
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

        if _is_generated_or_runtime(resolved):
            return self._native_result(
                path_key, "generated/runtime artifact — never re-enter SparseRead", "generated", 0
            )
        if not resolved.exists():
            return self._native_result(path_key, "path does not exist", "missing", 0)

        try:
            info: FileInfo = inspect_file(resolved)
            context = _episode_hint_from_input({})  # native reads carry no hint in v1
            decision = self.gate.decide(info, context)
            gate_profile: dict[str, Any] = classify_claude_gate(info, decision)
        except Exception:
            return self._native_result(path_key, "gate pipeline error — fail open", "error", 0)

        gate_mode = str(gate_profile.get("mode", "native"))
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

    def _native_result(
        self, path_key: str, reason: str, info_type: str, info_size: int
    ) -> dict[str, Any]:
        result = {
            "decision": "native",
            "gate": {
                "mode": "native",
                "reason": reason,
                "hook_can_block_read": False,
                "hook_can_block_bash": False,
                "hook_can_inject_context": False,
                "trajectory": "native",
            },
            "info_type": info_type,
            "info_size": info_size,
            "reason": reason,
            "cached": False,
        }
        self._cache[path_key] = result
        self._prune_cache()
        return result

    def mark_blocked(self, path_str: str) -> None:
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
        while len(self._cache) > MAX_CACHE:
            self._cache.pop(next(iter(self._cache)))


def _format_block(path: str, reason: str, info_type: str = "") -> dict[str, Any]:
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
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }


def _format_post_nudge(path: str, output_size: int, tool_output: dict[str, Any]) -> dict[str, Any]:
    updated = dict(tool_output)
    nudge = (
        f"\n\n---\n"
        f"💡 **SparseRead tip**: This was a large native read "
        f"({output_size:,} chars from {json.dumps(path)}).\n"
        f"Next time, try `sro_preview(path={json.dumps(path)})` first "
        f"for targeted evidence extraction."
    )
    if "content" in updated and isinstance(updated["content"], str):
        updated["content"] = updated["content"] + nudge
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": updated,
        }
    }


def _format_post_passthrough() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
        }
    }


def handle_pretooluse(
    session: HookSession,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    if tool_name == "Read":
        path = _extract_read_path(tool_input)
        if not path:
            session.mark_allowed()
            return _format_allow()
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
        session.mark_allowed()
        return _format_allow()

    if tool_name == "Bash":
        paths = _extract_bash_paths(tool_input)
        if not paths:
            session.mark_allowed()
            return _format_allow()
        for path in paths:
            decision = session.decide(path, tool_name="Bash")
            if decision["decision"] == "enforce":
                session.mark_blocked(path)
                return _format_block(path, decision["reason"], decision.get("info_type", ""))
        for path in paths:
            decision = session.decide(path, tool_name="Bash")
            if decision["decision"] == "advisory":
                session.mark_nudged()
                return _format_advisory(path, decision["reason"])
        session.mark_allowed()
        return _format_allow()

    session.mark_allowed()
    return _format_allow()


def handle_posttooluse(
    session: HookSession,
    tool_name: str,
    tool_input: dict[str, Any],
    tool_output: dict[str, Any] | None,
) -> dict[str, Any]:
    if tool_output is None:
        return _format_post_passthrough()
    try:
        output_size = len(json.dumps(tool_output, ensure_ascii=False))
    except (TypeError, ValueError):
        return _format_post_passthrough()
    if output_size < POST_NUDGE_CHARS:
        return _format_post_passthrough()

    if tool_name == "Read":
        path = _extract_read_path(tool_input)
    elif tool_name == "Bash":
        paths = _extract_bash_paths(tool_input)
        path = paths[0] if paths else ""
    else:
        return _format_post_passthrough()
    if not path:
        return _format_post_passthrough()

    decision = session.decide(path, tool_name=tool_name)
    if decision["decision"] != "enforce":
        return _format_post_passthrough()
    session.mark_nudged()
    return _format_post_nudge(path, output_size, tool_output)


def _parse_event(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return None


def run_session(workspace: str | None = None) -> int:
    gate = _make_gate_engine()
    session = HookSession(gate, workspace=workspace)
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
            resp = _format_allow()
        else:
            hook_event_name = str(event.get("hook_event_name", event.get("hookEventName", "")))
            tool_name = str(event.get("tool_name", event.get("toolName", "")))
            tool_input = event.get("tool_input", event.get("toolInput", {}))
            if not isinstance(tool_input, dict):
                tool_input = {}
            if hook_event_name == "PreToolUse":
                resp = handle_pretooluse(session, tool_name, tool_input)
            elif hook_event_name == "PostToolUse":
                tool_output = event.get("tool_output", event.get("toolOutput"))
                resp = handle_posttooluse(session, tool_name, tool_input, tool_output)
            elif hook_event_name in ("Stop", "Shutdown", "Notification"):
                stats = session.stats
                sys.stderr.write(
                    f"[sparseread-hook] session end — "
                    f"blocks={stats['blocks']} allows={stats['allows']} "
                    f"nudges={stats['nudges']} cache={stats['cache_size']}\n"
                )
                sys.stderr.flush()
                sys.exit(0)
            else:
                resp = _format_allow()
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    stats = session.stats
    sys.stderr.write(
        f"[sparseread-hook] stdin closed — "
        f"blocks={stats['blocks']} allows={stats['allows']} "
        f"nudges={stats['nudges']} cache={stats['cache_size']}\n"
    )
    sys.stderr.flush()
    return 0


def run_single(workspace: str | None = None) -> int:
    gate = _make_gate_engine()
    session = HookSession(gate, workspace=workspace)
    try:
        raw = sys.stdin.read()
        event = _parse_event(raw)
    except Exception:
        event = None
    if event is None:
        print(json.dumps(_format_allow(), ensure_ascii=False))
        return 0

    hook_event_name = str(event.get("hook_event_name", event.get("hookEventName", "")))
    tool_name = str(event.get("tool_name", event.get("toolName", "")))
    tool_input = event.get("tool_input", event.get("toolInput", {}))
    if not isinstance(tool_input, dict):
        tool_input = {}
    if hook_event_name in ("PreToolUse", ""):
        resp = handle_pretooluse(session, tool_name, tool_input)
    elif hook_event_name == "PostToolUse":
        tool_output = event.get("tool_output", event.get("toolOutput"))
        resp = handle_posttooluse(session, tool_name, tool_input, tool_output)
    else:
        resp = _format_allow()
    print(json.dumps(resp, ensure_ascii=False))
    permission = resp.get("hookSpecificOutput", {}).get("permissionDecision", "allow")
    return 2 if permission == "deny" else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead Claude Code Hook")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--session", action="store_true", default=True)
    parser.add_argument("--single", action="store_true", default=False)
    parser.add_argument("--post-tool-use", action="store_true", default=False)
    args = parser.parse_args(argv)
    workspace = str(Path(args.workspace).resolve()) if args.workspace else None
    if args.single:
        return run_single(workspace)
    return run_session(workspace)


if __name__ == "__main__":
    raise SystemExit(main())
