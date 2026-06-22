"""Nanobot adapter for SparseRead."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.sparse_reading.policy import SparseCommandPolicy
from sparseread.config import SparseReadConfig, SparseReadMode
from sparseread.wrapper import SparseRead


class NanobotAdapter:
    """Install SparseRead into a nanobot-style agent loop or tool registry."""

    @staticmethod
    def matches(agent: Any) -> bool:
        tools = getattr(agent, "tools", None)
        return all(hasattr(tools, attr) for attr in ("register", "get", "has"))

    def install(self, agent: Any, sparseread: SparseRead) -> list[str]:
        tools = getattr(agent, "tools", None)
        if tools is None:
            raise TypeError("NanobotAdapter requires an agent with a .tools registry.")

        installed: list[str] = []
        orchestrator = sparseread.orchestrator

        for tool in sparseread.tools():
            if not tools.has(tool.name):
                tools.register(tool)
                installed.append(tool.name)
        orchestrator.mark_macro_available()

        for name in ("read_file", "list_dir", "grep"):
            tool = tools.get(name)
            if tool is not None and hasattr(tool, "_sro"):
                setattr(tool, "_sro", orchestrator)
                installed.append(f"{name}:guard")

        exec_tool = tools.get("exec")
        if exec_tool is not None and hasattr(exec_tool, "sro_policy"):
            setattr(exec_tool, "sro_policy", SparseCommandPolicy(orchestrator))
            installed.append("exec:policy")

        setattr(agent, "sparseread", sparseread)
        return installed


def install(
    agent: Any,
    *,
    mode: SparseReadMode = "auto",
    workspace: str | Path | None = None,
    config: SparseReadConfig | None = None,
) -> SparseRead:
    """Install SparseRead into a nanobot-style agent and return the runtime."""

    runtime = SparseRead(config or SparseReadConfig(mode=mode, workspace=workspace))
    NanobotAdapter().install(agent, runtime)
    return runtime
