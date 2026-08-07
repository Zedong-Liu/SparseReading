"""Nanobot adapter for SparseRead."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sparseread.config import SparseReadConfig, SparseReadMode
from sparseread.wrapper import SparseRead


class NanobotAdapter:
    """Install SparseRead into a nanobot-style agent loop or tool registry."""

    @staticmethod
    def matches(agent: Any) -> bool:
        tools = getattr(agent, "tools", None)
        return all(hasattr(tools, attr) for attr in ("register", "get", "has"))

    def install(
        self,
        agent: Any,
        sparseread: SparseRead,
        *,
        conversation_id: str = "default",
    ) -> list[str]:
        from sparseread_nanobot.hook import SparseReadHook, SroGuardTool, SroHandoffTool

        tools = getattr(agent, "tools", None)
        if tools is None:
            raise TypeError("NanobotAdapter requires an agent with a .tools registry.")

        installed: list[str] = []
        orchestrator = sparseread.orchestrator

        for tool in sparseread.tools():
            if not tools.has(tool.name):
                tools.register(tool)
                installed.append(tool.name)
        guard_tool = SroGuardTool()
        if not tools.has(guard_tool.name):
            tools.register(guard_tool)
            installed.append("sro_guard")
        handoff_tool = SroHandoffTool(orchestrator)
        if not tools.has(handoff_tool.name):
            tools.register(handoff_tool)
            installed.append("sro_handoff")
        orchestrator.mark_macro_available()

        hooks = getattr(agent, "_extra_hooks", None)
        if isinstance(hooks, list):
            hooks.append(SparseReadHook(sparseread, conversation_id=conversation_id))
            installed.append("hook:sparse_read")

        agent.sparseread = sparseread
        return installed


def install(
    agent: Any,
    *,
    mode: SparseReadMode = "auto",
    workspace: str | Path | None = None,
    config: SparseReadConfig | None = None,
    conversation_id: str = "default",
) -> SparseRead:
    """Install SparseRead into a nanobot-style agent and return the runtime."""

    runtime = SparseRead(config or SparseReadConfig(mode=mode, workspace=workspace))
    NanobotAdapter().install(agent, runtime, conversation_id=conversation_id)
    return runtime
