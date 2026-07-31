"""Public SparseRead wrapper and toolkit facade."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from sparseread.config import SparseReadConfig, SparseReadMode
from sparseread.core.orchestrator import SparseReadingOrchestrator
from sparseread.core.tools import SroCardTool, SroPreviewTool, SroRawTool, SroReadTool


class SparseRead:
    """SparseRead runtime for tool-capable agents.

    The runtime owns one orchestrator and exposes agent-facing tools.  Framework
    adapters can install these tools and wire file-access guards.
    """

    def __init__(self, config: SparseReadConfig | None = None, **overrides: Any) -> None:
        if config is None:
            config = SparseReadConfig(**overrides)
        self.config = config
        workspace = Path(config.workspace).resolve() if config.workspace is not None else None
        benefit_override = config.benefit_gate_override() if config.benefit_gate else "native"
        self.orchestrator = SparseReadingOrchestrator(
            workspace,
            macro_available=True,
            benefit_gate_override=benefit_override,
        )
        self.last_trace: dict[str, Any] | None = None

    def tools(self) -> list[Any]:
        """Return SparseRead tool objects for frameworks that accept Python tools."""

        if self.config.mode == "bench_protocol":
            return [SroCardTool(self.orchestrator), SroReadTool(self.orchestrator)]
        return [
            SroPreviewTool(self.orchestrator),
            SroRawTool(self.orchestrator),
            SroCardTool(self.orchestrator),
            SroReadTool(self.orchestrator),
        ]

    def tool_schemas(self) -> list[dict[str, Any]]:
        """Return OpenAI-style tool schemas for frameworks that need schemas."""

        return [tool.to_schema() for tool in self.tools()]

    @property
    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools()]


class SparseReadAgentWrapper:
    """Thin compatibility wrapper around an existing agent object.

    Framework integrations are discovered through the ``sparseread.adapters``
    entry-point group. Core never imports a concrete agent framework.
    """

    def __init__(
        self,
        agent: Any,
        *,
        mode: SparseReadMode = "auto",
        workspace: str | Path | None = None,
        config: SparseReadConfig | None = None,
        adapter: Any | None = None,
    ) -> None:
        self.agent = agent
        self.sparseread = SparseRead(config or SparseReadConfig(mode=mode, workspace=workspace))
        self.adapter = adapter or self._autodetect_adapter(agent)
        self.installed: list[str] = []
        if self.adapter is not None:
            self.installed = list(self.adapter.install(agent, self.sparseread))

    @staticmethod
    def _autodetect_adapter(agent: Any) -> Any | None:
        try:
            candidates = entry_points(group="sparseread.adapters")
        except Exception:
            return None
        for entry_point in candidates:
            try:
                factory = entry_point.load()
                adapter = factory() if callable(factory) else factory
                if adapter is not None and adapter.matches(agent):
                    return adapter
            except Exception:
                continue
        return None

    def run(self, prompt: str, **kwargs: Any) -> Any:
        if hasattr(self.agent, "run"):
            return self.agent.run(prompt, **kwargs)
        if hasattr(self.agent, "invoke"):
            return self.agent.invoke(prompt, **kwargs)
        if hasattr(self.agent, "chat"):
            return self.agent.chat(prompt, **kwargs)
        if callable(self.agent):
            return self.agent(prompt, **kwargs)
        raise TypeError("Wrapped agent must expose run(), invoke(), chat(), or be callable.")

    def __getattr__(self, name: str) -> Any:
        return getattr(self.agent, name)


def wrap(
    agent: Any,
    *,
    mode: SparseReadMode = "auto",
    workspace: str | Path | None = None,
    config: SparseReadConfig | None = None,
    adapter: Any | None = None,
) -> SparseReadAgentWrapper:
    """Wrap an existing agent with SparseRead integration."""

    return SparseReadAgentWrapper(
        agent,
        mode=mode,
        workspace=workspace,
        config=config,
        adapter=adapter,
    )
