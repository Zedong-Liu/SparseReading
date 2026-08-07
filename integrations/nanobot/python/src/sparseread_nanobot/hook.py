"""NanoBot native integration through the official AgentHook extension point.

This module is host-free: it never imports ``nanobot``.  The hook and tools are
duck-typed against the official ``AgentHook`` / ``Tool`` surfaces so the adapter
works with any installed ``nanobot-ai`` (>=0.2) without vendoring the framework.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from sparseread.core.policy import SparseCommandPolicy
from sparseread.wrapper import SparseRead

from sparseread_nanobot.guidance import SRO_GUIDANCE


READ_TOOLS = {"read_file", "list_dir", "grep"}
EXEC_TOOLS = {"exec", "bash", "shell"}
WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "write"}


class _SroTool:
    """Minimal Tool-compatible surface (duck-typed for nanobot ToolRegistry)."""

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def description(self) -> str:
        raise NotImplementedError

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        raise NotImplementedError

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return params if isinstance(params, dict) else {}

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if not isinstance(params, dict):
            return ["parameters must be an object"]
        errors: list[str] = []
        for required in (self.parameters or {}).get("required", []):
            if required not in params:
                errors.append(f"missing required {required}")
        return errors

    async def execute(self, **kwargs: Any) -> Any:
        raise NotImplementedError


class SroGuardTool(_SroTool):
    """Internal tool that returns a SparseRead guard message to the model."""

    @property
    def name(self) -> str:
        return "sro_guard"

    @property
    def description(self) -> str:
        return "SparseRead internal guard result. Do not call directly."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    async def execute(self, message: str, **kwargs: Any) -> str:
        return str(message)


class SroHandoffTool(_SroTool):
    """Return the old-host-style SRO handoff guidance for a large object."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "sro_handoff"

    @property
    def description(self) -> str:
        return "SparseRead internal handoff guidance. Do not call directly."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "episode_hint": {
                    "type": "object",
                    "description": "Optional episode boundary hint.",
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, episode_hint: dict[str, Any] | None = None, **kwargs: Any) -> str:
        if isinstance(episode_hint, dict):
            try:
                self.orchestrator.bind_episode(path, episode_hint)
            except Exception:
                pass
        try:
            return self.orchestrator.handoff_message(path)
        except Exception as exc:
            return f"Error: SRO handoff failed: {exc}"


def _candidate_paths(args: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("file_path", "path", "filename", "target"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, dict):
            for sub in ("path", "artifact_id"):
                item = value.get(sub)
                if isinstance(item, str) and item.strip():
                    values.append(item.strip())
    for key in ("paths", "files"):
        value = args.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if isinstance(item, str) and item.strip())
    return values


class SparseReadHook:
    """PreToolUse-style SRO gate for NanoBot's official AgentHook surface.

    Duck-typed against ``nanobot.agent.hook.AgentHook``: implements
    ``before_iteration`` / ``before_execute_tools`` / ``after_iteration`` /
    ``finalize_content`` without importing the framework.
    """

    def __init__(
        self,
        sparseread: SparseRead,
        *,
        conversation_id: str = "default",
        inject_guidance: bool = True,
        workspace: str | Path | None = None,
    ) -> None:
        self.sparseread = sparseread
        self.orchestrator = sparseread.orchestrator
        self.conversation_id = conversation_id
        self.inject_guidance = inject_guidance
        self.workspace = (
            str(Path(workspace).resolve())
            if workspace is not None
            else (str(Path(sparseread.config.workspace).resolve()) if sparseread.config.workspace else None)
        )
        self.policy = SparseCommandPolicy(sparseread.orchestrator)
        self._guidance_injected = False
        self._pending_writes: list[str] = []
        self._exec_entries: list[dict[str, Any]] = []
        self._last_stop_reason: str | None = None

    def _cwd(self, args: dict[str, Any]) -> str:
        for key in ("cwd", "workdir"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return self.workspace or str(Path.cwd())

    @staticmethod
    def _parse_hint(hint: Any) -> dict[str, Any] | None:
        if isinstance(hint, dict):
            return hint
        if isinstance(hint, str) and hint.strip():
            try:
                parsed = json.loads(hint)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return None

    def _route_read(
        self,
        call: Any,
        path: str,
        hint: Any,
        *,
        offset: int | None,
        limit: int | None,
        pages: str | None,
    ) -> None:
        if self.sparseread.config.mode == "bench_protocol":
            return
        try:
            should_handoff = self.orchestrator.should_handoff_read(
                path,
                offset=offset if offset is not None else 1,
                limit=limit,
                pages=pages,
            )
        except Exception:
            return
        if not should_handoff:
            return
        parsed_hint = self._parse_hint(hint)
        arguments: dict[str, Any] = {"path": str(path)}
        if parsed_hint:
            arguments["episode_hint"] = parsed_hint
        call.name = "sro_handoff"
        call.arguments = arguments

    async def before_iteration(self, context: Any) -> None:
        self.orchestrator.set_context(
            {"conversation_id": self.conversation_id, "turn_id": str(getattr(self, "_iteration", 0))}
        )
        self._iteration = getattr(self, "_iteration", 0) + 1
        if self.inject_guidance and not self._guidance_injected:
            self._guidance_injected = True
            messages = context.messages
            if isinstance(messages, list):
                already = any(
                    isinstance(m, dict)
                    and m.get("role") == "system"
                    and "# Sparse Reading" in str(m.get("content", ""))
                    for m in messages
                )
                if not already:
                    messages.insert(0, {"role": "system", "content": SRO_GUIDANCE})

    async def before_execute_tools(self, context: Any) -> None:
        response = context.response
        if response is None:
            return
        self._pending_writes = []
        self._exec_entries = []
        for index, call in enumerate(list(response.tool_calls)):
            name = str(call.name or "")
            args = dict(call.arguments or {})
            if name in READ_TOOLS:
                offset = args.get("offset")
                limit = args.get("limit")
                pages = args.get("pages")
                for path in _candidate_paths(args):
                    if path:
                        self._route_read(
                            call,
                            path,
                            args.get("episode_hint"),
                            offset=offset,
                            limit=limit,
                            pages=pages,
                        )
                        if call.name == "sro_handoff":
                            break
            elif name in EXEC_TOOLS:
                command = str(args.get("command") or args.get("cmd") or "")
                if command:
                    guard_message = self.policy.guard(command, self._cwd(args))
                    if guard_message:
                        call.name = "sro_guard"
                        call.arguments = {"message": guard_message}
                    else:
                        self._exec_entries.append(
                            {
                                "index": index,
                                "command": command,
                                "cwd": self._cwd(args),
                            }
                        )
            elif name in WRITE_TOOLS:
                self._pending_writes.extend(_candidate_paths(args))

    async def after_iteration(self, context: Any) -> None:
        for raw_path in self._pending_writes:
            try:
                path = Path(raw_path)
                if not path.is_absolute() and self.workspace:
                    path = Path(self.workspace) / path
                self.orchestrator.record_output_write(path)
            except Exception:
                pass
        self._pending_writes = []
        if self._exec_entries and context.tool_results:
            results = list(context.tool_results)
            for entry in self._exec_entries:
                index = int(entry["index"])
                if index >= len(results):
                    continue
                try:
                    self.policy.record_result(
                        entry["command"],
                        entry["cwd"],
                        str(results[index]),
                    )
                except Exception:
                    pass
        self._exec_entries = []
        stop_reason = context.stop_reason
        if stop_reason and stop_reason != self._last_stop_reason:
            self._last_stop_reason = stop_reason
            try:
                self.orchestrator.finish_episode(self.conversation_id)
            except Exception:
                pass

    def finalize_content(self, context: Any, content: str | None) -> str | None:
        if content:
            try:
                self.orchestrator.finish_episode(self.conversation_id)
            except Exception:
                pass
        return content
