from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.tools.filesystem import ListDirTool, ReadFileTool
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.search import GrepTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.bus.queue import MessageBus
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator

from sparseread import SparseRead, SparseReadConfig, wrap
from sparseread.adapters.nanobot import install


class FakeNanobotAgent:
    def __init__(self, workspace: Path) -> None:
        self.tools = ToolRegistry()
        self.tools.register(ReadFileTool(workspace=workspace))
        self.tools.register(ListDirTool(workspace=workspace))
        self.tools.register(GrepTool(workspace=workspace))
        self.tools.register(ExecTool(working_dir=str(workspace)))

    def run(self, prompt: str, **kwargs: object) -> tuple[str, dict[str, object]]:
        return prompt, kwargs


def test_runtime_exposes_sparse_read_tools(tmp_path: Path) -> None:
    runtime = SparseRead(workspace=tmp_path)

    assert runtime.tool_names == ["sro_preview", "sro_raw", "sro_card", "sro_read"]
    assert [schema["function"]["name"] for schema in runtime.tool_schemas()] == [
        "sro_preview",
        "sro_raw",
        "sro_card",
        "sro_read",
    ]


def test_bench_protocol_keeps_original_tool_path(tmp_path: Path) -> None:
    runtime = SparseRead(SparseReadConfig(mode="bench_protocol", workspace=tmp_path))

    assert runtime.tool_names == ["sro_card", "sro_read"]
    assert "Benchmark SparseRead entrypoint" in runtime.tools()[0].description
    assert "after sro_card" in runtime.tools()[1].description


def test_config_force_mode_overrides_benefit_gate(tmp_path: Path) -> None:
    target = tmp_path / "small.txt"
    target.write_text("short native file\n", encoding="utf-8")

    runtime = SparseRead(SparseReadConfig(mode="force", workspace=tmp_path))
    decision = runtime.orchestrator.benefit_gate.decide(runtime.orchestrator.inspect(target))

    assert decision.mode == "force_sro"
    assert "SparseRead config" in decision.reason


def test_wrapper_forwards_run_for_unknown_agents(tmp_path: Path) -> None:
    class FakeAgent:
        def run(self, prompt: str, **kwargs: object) -> tuple[str, dict[str, object]]:
            return prompt, kwargs

    wrapped = wrap(FakeAgent(), workspace=tmp_path)

    assert wrapped.run("audit this", answer=True) == ("audit this", {"answer": True})
    assert wrapped.installed == []


def test_nanobot_adapter_installs_tools_and_guards(tmp_path: Path) -> None:
    agent = FakeNanobotAgent(tmp_path)

    runtime = install(agent, workspace=tmp_path)

    assert agent.tools.has("sro_card")
    assert agent.tools.has("sro_preview")
    assert agent.tools.has("sro_raw")
    assert agent.tools.has("sro_read")
    assert agent.tools.get("read_file")._sro is runtime.orchestrator  # type: ignore[union-attr]
    assert agent.tools.get("list_dir")._sro is runtime.orchestrator  # type: ignore[union-attr]
    assert agent.tools.get("grep")._sro is runtime.orchestrator  # type: ignore[union-attr]
    assert agent.tools.get("exec").sro_policy is not None  # type: ignore[union-attr]
    assert agent.sparseread is runtime


def test_nanobot_agent_loop_registers_preview_first_tools(tmp_path: Path) -> None:
    loop = AgentLoop.__new__(AgentLoop)
    loop.tools = ToolRegistry()
    loop._sro_runtime_mode = "auto"
    sro = SparseReadingOrchestrator(tmp_path)

    loop._activate_sro_macros(sro)

    assert [name for name in loop.tools.tool_names if name.startswith("sro_")] == [
        "sro_preview",
        "sro_read",
    ]


def test_nanobot_agent_loop_advisory_workspace_registers_compact_tools(tmp_path: Path) -> None:
    loop = AgentLoop.__new__(AgentLoop)
    loop.tools = ToolRegistry()
    loop._sro_runtime_mode = "auto"
    loop._sro_workspace_mode = "advisory"
    sro = SparseReadingOrchestrator(tmp_path)

    loop._activate_sro_macros(sro)

    assert [name for name in loop.tools.tool_names if name.startswith("sro_")] == [
        "sro_preview",
        "sro_read",
    ]


def test_native_sro_workspace_disables_sparse_reading_skill() -> None:
    assert AgentLoop._effective_disabled_skills([], sro_disabled=True) == ["sparse-reading"]
    assert AgentLoop._effective_disabled_skills(
        ["sparse-reading", "another-skill"],
        sro_disabled=True,
    ) == ["sparse-reading", "another-skill"]


def test_native_sro_workspace_keeps_compact_advisory_tools(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    monkeypatch.setenv("SPARSEREAD_MODE", "native")
    (tmp_path / "data").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data" / "panel_data.csv").write_text(
        "firm_id,year,did,outcome\nF1,2020,1,3\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "firm_metadata.csv").write_text(
        "firm_id,industry\nF1,Tech\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "data_dictionary.json").write_text('{"did":"effect"}', encoding="utf-8")
    (tmp_path / "scripts" / "did_regression.py").write_text("# template\n", encoding="utf-8")
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")

    assert [name for name in loop.tools.tool_names if name.startswith("sro_")] == [
        "sro_preview",
        "sro_read",
    ]
    assert loop.tools.get("read_file")._sro is not None  # type: ignore[union-attr]
    assert loop.tools.get("exec").sro_policy is not None  # type: ignore[union-attr]
    assert "sparse-reading" not in loop.context.skills.disabled_skills


def test_nanobot_agent_loop_bench_protocol_registers_original_path_only(tmp_path: Path) -> None:
    loop = AgentLoop.__new__(AgentLoop)
    loop.tools = ToolRegistry()
    loop._sro_runtime_mode = "bench_protocol"
    sro = SparseReadingOrchestrator(tmp_path)

    loop._activate_sro_macros(sro)

    assert [name for name in loop.tools.tool_names if name.startswith("sro_")] == [
        "sro_card",
        "sro_read",
    ]


def test_wrapper_autodetects_nanobot_registry(tmp_path: Path) -> None:
    agent = FakeNanobotAgent(tmp_path)

    wrapped = wrap(agent, workspace=tmp_path)

    assert "sro_preview" in wrapped.installed
    assert "sro_raw" in wrapped.installed
    assert "sro_card" in wrapped.installed
    assert "sro_read" in wrapped.installed
    assert "read_file:guard" in wrapped.installed
    assert wrapped.run("hello", value=1) == ("hello", {"value": 1})
