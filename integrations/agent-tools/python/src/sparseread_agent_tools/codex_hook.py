"""Conservative Codex Bash hook for SparseRead."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TextIO


_UNSAFE_SHELL_CHARACTERS = "|;&<>`$()\n\r"
_STATE_TTL_SECONDS = 24 * 60 * 60
_MAX_REDIRECTS = 512


def _parse_unbounded_cat(command: Any) -> str | None:
    """Return a single cat target; ambiguous shell syntax stays native."""
    if not isinstance(command, str) or not command.strip():
        return None
    command = command.strip()
    if any(character in command for character in _UNSAFE_SHELL_CHARACTERS):
        return None
    if any(character in command for character in "*?[]{}"):
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if len(tokens) != 2 or Path(tokens[0]).name != "cat":
        return None
    target = tokens[1]
    if not target or target.startswith("-") or target.startswith("~"):
        return None
    return target


def _resolve_workspace_target(raw_path: str, cwd: Any, workspace: str | Path) -> Path | None:
    try:
        root = Path(workspace).resolve(strict=False)
        event_cwd = Path(cwd) if isinstance(cwd, str) and cwd.strip() else root
        if not event_cwd.is_absolute():
            event_cwd = root / event_cwd
        target = Path(raw_path)
        if not target.is_absolute():
            target = event_cwd / target
        target = target.resolve(strict=False)
        target.relative_to(root)
        return target
    except (OSError, RuntimeError, ValueError):
        return None


@contextmanager
def _locked_state(plugin_data: Path) -> Iterator[Path]:
    plugin_data.mkdir(parents=True, exist_ok=True)
    lock_path = plugin_data / "codex-hook-state.lock"
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt

            lock_file.seek(0)
            if not lock_file.read(1):
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            unlock = lambda: msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            unlock = lambda: fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        try:
            yield plugin_data / "codex-hook-state.json"
        finally:
            unlock()


def _redirect_once(plugin_data: str | Path, session_id: str, path: Path) -> bool:
    """Claim the single redirect for this session and path, under a file lock."""
    root = Path(plugin_data)
    state_key = hashlib.sha256(f"{session_id}\0{path}".encode("utf-8")).hexdigest()
    now = time.time()
    with _locked_state(root) as state_path:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or not isinstance(state.get("redirects"), dict):
                raise ValueError("invalid Codex hook state")
            redirects = state["redirects"]
        else:
            redirects = {}

        redirects = {
            key: timestamp
            for key, timestamp in redirects.items()
            if isinstance(key, str)
            and isinstance(timestamp, (int, float))
            and now - timestamp < _STATE_TTL_SECONDS
        }
        if state_key in redirects:
            _write_state(state_path, redirects)
            return False

        redirects[state_key] = now
        if len(redirects) > _MAX_REDIRECTS:
            redirects = dict(
                sorted(redirects.items(), key=lambda item: item[1])[-_MAX_REDIRECTS:]
            )
        _write_state(state_path, redirects)
        return True


def _write_state(state_path: Path, redirects: dict[str, float | int]) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=".codex-hook-state-", dir=state_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as state_file:
            json.dump({"redirects": redirects}, state_file, separators=(",", ":"))
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, state_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _default_bridge_factory(**kwargs: Any) -> Any:
    from .bridge import HostBridge

    return HostBridge(**kwargs)


def _guidance(path: Path, reason: str = "") -> str:
    detail = f" SparseRead reason: {reason}." if reason else ""
    return (
        f"Use sro_preview(path={json.dumps(str(path), ensure_ascii=False)}) and then "
        f"sro_read for targeted evidence before deciding whether the full native read is needed.{detail}"
    )


def handle_event(
    event: Any,
    *,
    workspace: str | Path,
    mode: str = "auto",
    plugin_data: str | Path | None = None,
    bridge_factory: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    """Evaluate one Codex PreToolUse event and fail open on every bridge error."""
    if not isinstance(event, dict) or event.get("hook_event_name") != "PreToolUse":
        return None
    if event.get("tool_name") != "Bash":
        return None
    tool_input = event.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    raw_path = _parse_unbounded_cat(tool_input.get("command"))
    if raw_path is None:
        return None

    path = _resolve_workspace_target(raw_path, event.get("cwd"), workspace)
    if path is None:
        return None

    session_id = str(event.get("session_id") or "codex-session")
    context = {
        "session_id": session_id,
        "turn_id": str(event.get("turn_id") or ""),
    }
    factory = bridge_factory or _default_bridge_factory
    try:
        bridge = factory(host="codex", workspace=str(Path(workspace).resolve()), mode=mode)
        result = bridge.handle(
            {
                "id": f"codex-hook-{uuid.uuid4().hex}",
                "method": "decide",
                "params": {"path": str(path), "context": context},
            }
        )
        if not isinstance(result, dict):
            return None
        gate = result.get("host_gate")
        if not isinstance(gate, dict):
            return None

        reason = str(gate.get("reason") or "").strip()
        guidance = _guidance(path, reason)
        if (
            mode == "auto"
            and gate.get("mode") == "enforce"
            and (gate.get("block_native_exec_dump") is True or gate.get("block_native_read") is True)
        ):
            if plugin_data is None or not _redirect_once(plugin_data, session_id, path):
                return None
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "SparseRead recommends a preview before this unbounded file read."
                    ),
                    "additionalContext": guidance,
                }
            }

        if mode == "advisory" or gate.get("mode") == "advisory" or gate.get("nudge_native") is True:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": guidance + " The native command remains available.",
                }
            }
    except Exception:
        return None
    return None


def run_hook(
    raw_input: str,
    *,
    workspace: str | Path,
    mode: str = "auto",
    plugin_data: str | Path | None = None,
    bridge_factory: Callable[..., Any] | None = None,
) -> dict[str, Any] | None:
    try:
        event = json.loads(raw_input)
    except (TypeError, json.JSONDecodeError):
        return None
    return handle_event(
        event,
        workspace=workspace,
        mode=mode,
        plugin_data=plugin_data,
        bridge_factory=bridge_factory,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead Codex PreToolUse hook")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mode", choices=("auto", "advisory"), default="auto")
    args = parser.parse_args(argv)
    try:
        raw_input = sys.stdin.read()
        response = run_hook(
            raw_input,
            workspace=args.workspace,
            mode=args.mode,
            plugin_data=os.environ.get("PLUGIN_DATA"),
        )
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    except Exception:
        # An unavailable hook must never prevent Codex's native permission flow.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
