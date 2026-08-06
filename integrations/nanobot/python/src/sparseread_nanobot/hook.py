"""NanoBot native integration through the official AgentHook extension point.

This module is the NanoBot counterpart of the OpenCode/OpenClaw/Claude
PreToolUse adapters: it observes the model's tool calls before execution and
rewrites high-benefit reads to SRO tools.  It does not require any SRO fields
in the NanoBot host source.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.providers.base import ToolCallRequest

from sparseread.core.benefit_gate import BenefitGate, GateContext
from sparseread.core.detector import inspect_file
from sparseread.core.policy import SparseCommandPolicy
from sparseread.core.readers.collection import CollectionReader
from sparseread.wrapper import SparseRead

from sparseread_nanobot.guidance import SRO_GUIDANCE


READ_TOOLS = {"read_file", "list_dir", "grep"}
EXEC_TOOLS = {"exec", "bash", "shell"}
WRITE_TOOLS = {"write_file", "edit_file", "apply_patch", "write"}


class SroGuardTool:
    """Internal tool that returns a SparseRead guard message to the model."""

    @property
    def name(self) -> str:
        return "sro_guard"

    @property
    def description(self) -> str:
        return "SparseRead internal guard result. Do not call directly."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, message: str, **kwargs: Any) -> str:
        return str(message)


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


class SparseReadHook(AgentHook):
    """PreToolUse-style SRO gate for NanoBot's official AgentHook surface."""

    def __init__(
        self,
        sparseread: SparseRead,
        *,
        conversation_id: str = "default",
        inject_guidance: bool = True,
        workspace: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.sparseread = sparseread
        self.orchestrator = sparseread.orchestrator
        self.conversation_id = conversation_id
        self.inject_guidance = inject_guidance
        self.workspace = (
            str(Path(workspace).resolve())
            if workspace is not None
            else (str(Path(sparseread.config.workspace).resolve()) if sparseread.config.workspace else None)
        )
        override = (
            sparseread.config.benefit_gate_override()
            if sparseread.config.benefit_gate
            else "native"
        )
        self.gate = BenefitGate(CollectionReader(), override=override)
        self.policy = SparseCommandPolicy(sparseread.orchestrator)
        self._guidance_injected = False
        self._pending_writes: list[str] = []
        self._exec_commands: list[tuple[str, str]] = []

    def _cwd(self, args: dict[str, Any]) -> str:
        for key in ("cwd", "workdir"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return self.workspace or str(Path.cwd())

    def _route_read(self, call: ToolCallRequest, path: str, hint: Any) -> None:
        try:
            info = inspect_file(path)
            decision = self.gate.decide(info, GateContext.from_value(hint))
        except Exception:
            return
        if decision.mode == "force_sro" and decision.preview_recommended:
            arguments: dict[str, Any] = {"path": str(path)}
            if isinstance(hint, dict):
                arguments["episode_hint"] = hint
            call.name = "sro_preview"
            call.arguments = arguments

    async def before_iteration(self, context: AgentHookContext) -> None:
        self.orchestrator.set_context(
            {"conversation_id": self.conversation_id, "turn_id": str(getattr(self, "_iteration", 0))}
        )
        self._iteration = getattr(self, "_iteration", 0) + 1
        if self.inject_guidance and not self._guidance_injected:
            self._guidance_injected = True
            messages = context.messages
            if isinstance(messages, list) and messages and isinstance(messages[0], dict):
                first = messages[0]
                content = str(first.get("content", ""))
                if "SparseRead protocol" not in content:
                    messages[0] = {**first, "content": SRO_GUIDANCE + "\n\n" + content}

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        response = context.response
        if response is None:
            return
        self._pending_writes = []
        self._exec_commands = []
        for call in list(response.tool_calls):
            name = str(call.name or "")
            args = dict(call.arguments or {})
            if name in READ_TOOLS:
                for path in _candidate_paths(args):
                    if path:
                        self._route_read(call, path, args.get("episode_hint"))
                        break
            elif name in EXEC_TOOLS:
                command = str(args.get("command") or args.get("cmd") or "")
                if command:
                    guard_message = self.policy.guard(command, self._cwd(args))
                    if guard_message:
                        call.name = "sro_guard"
                        call.arguments = {"message": guard_message}
                    else:
                        self._exec_commands.append((command, self._cwd(args)))
            elif name in WRITE_TOOLS:
                paths = _candidate_paths(args)
                if paths:
                    self._pending_writes.append(paths[0])

    async def after_iteration(self, context: AgentHookContext) -> None:
        for path in self._pending_writes:
            try:
                self.orchestrator.record_output_write(path)
            except Exception:
                pass
        self._pending_writes = []
        if self._exec_commands and context.tool_results:
            results = list(context.tool_results)
            for (command, cwd), result in zip(self._exec_commands, results):
                try:
                    self.policy.record_result(command, cwd, str(result))
                except Exception:
                    pass
        self._exec_commands = []

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        if content:
            try:
                self.orchestrator.finish_episode(self.conversation_id)
            except Exception:
                pass
        return content
