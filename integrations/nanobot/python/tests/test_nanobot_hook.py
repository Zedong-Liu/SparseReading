from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sparseread.config import SparseReadConfig
from sparseread.wrapper import SparseRead

from sparseread_nanobot.adapter import NanobotAdapter
from sparseread_nanobot.hook import SparseReadHook


class FakeCall:
    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.id = "1"
        self.name = name
        self.arguments = arguments


class FakeResponse:
    def __init__(self, *calls: FakeCall) -> None:
        self.content = None
        self.tool_calls = list(calls)


class FakeContext:
    def __init__(self, response: FakeResponse | None = None, messages: list | None = None) -> None:
        self.iteration = 0
        self.messages = messages or []
        self.response = response
        self.tool_results: list[Any] = []
        self.stop_reason: str | None = None


def _call(name: str, arguments: dict[str, Any]) -> FakeCall:
    return FakeCall(name, arguments)


def _response(*calls: FakeCall) -> FakeResponse:
    return FakeResponse(*calls)


def _context(response: FakeResponse | None = None, messages: list | None = None) -> FakeContext:
    return FakeContext(response=response, messages=messages)


def _hook(tmp_path: Path, *, mode: str = "auto") -> SparseReadHook:
    runtime = SparseRead(SparseReadConfig(mode=mode, workspace=str(tmp_path)))
    runtime.orchestrator.mark_macro_available()
    return SparseReadHook(runtime, workspace=str(tmp_path), inject_guidance=False)


def _write(workspace: Path, name: str, size: int) -> Path:
    path = workspace / name
    path.write_text("x" * size, encoding="utf-8")
    return path


def test_large_read_is_rewritten_to_sro_handoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    target = _write(tmp_path, "large.txt", 6000)
    hook = _hook(tmp_path)
    call = _call("read_file", {"file_path": str(target)})

    asyncio.run(hook.before_execute_tools(_context(_response(call))))

    assert call.name == "sro_handoff"
    assert call.arguments["path"] == str(target)


def test_small_read_stays_native(tmp_path: Path) -> None:
    target = _write(tmp_path, "small.txt", 100)
    hook = _hook(tmp_path)
    call = _call("read_file", {"file_path": str(target)})

    asyncio.run(hook.before_execute_tools(_context(_response(call))))

    assert call.name == "read_file"


def test_bounded_read_stays_native(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    target = _write(tmp_path, "large.txt", 6000)
    hook = _hook(tmp_path)
    call = _call("read_file", {"file_path": str(target), "offset": 2, "limit": 50})

    asyncio.run(hook.before_execute_tools(_context(_response(call))))

    assert call.name == "read_file"


def test_multi_path_breaks_after_first_handoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    large = _write(tmp_path, "large.txt", 6000)
    small = _write(tmp_path, "small.txt", 100)
    hook = _hook(tmp_path)
    call = _call("read_file", {"paths": [str(large), str(small)]})

    asyncio.run(hook.before_execute_tools(_context(_response(call))))

    assert call.name == "sro_handoff"
    assert call.arguments["path"] == str(large)


def test_exec_large_dump_is_rewritten_to_guard(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    target = _write(tmp_path, "large.txt", 6000)
    hook = _hook(tmp_path)
    call = _call("exec", {"command": f"cat {target}"})

    asyncio.run(hook.before_execute_tools(_context(_response(call))))

    assert call.name == "sro_guard"
    assert "blocked" in call.arguments["message"]


def test_sro_guard_executes_through_registry() -> None:
    from sparseread_nanobot.hook import SroGuardTool

    tool = SroGuardTool()
    params = tool.cast_params({"message": "blocked: use sro_read"})
    assert tool.validate_params(params) == []
    result = asyncio.run(tool.execute(**params))

    assert result == "blocked: use sro_read"


def test_sro_handoff_executes_through_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    from sparseread_nanobot.hook import SroHandoffTool

    hook = _hook(tmp_path)
    target = _write(tmp_path, "large.txt", 6000)
    tool = SroHandoffTool(hook.orchestrator)

    params = tool.cast_params({"path": str(target)})
    assert tool.validate_params(params) == []
    result = asyncio.run(tool.execute(**params))

    assert "sro_handoff" in str(result) or "file_card" in str(result)


def test_write_provenance_is_recorded_after_iteration(tmp_path: Path) -> None:
    target = _write(tmp_path, "output.txt", 10)
    hook = _hook(tmp_path)
    call = _call("write_file", {"path": str(target)})
    context = _context(_response(call))

    asyncio.run(hook.before_execute_tools(context))
    asyncio.run(hook.after_iteration(context))

    assert str(target.resolve()) in hook.orchestrator._written_paths_by_conversation["default"]


def test_finish_episode_on_final_content(tmp_path: Path, monkeypatch) -> None:
    hook = _hook(tmp_path)
    calls = {"finish": 0}

    def fake_finish(conversation_id: str | None = None):
        calls["finish"] += 1
        return None

    monkeypatch.setattr(hook.orchestrator, "finish_episode", fake_finish)

    result = hook.finalize_content(_context(), "done")

    assert result == "done"
    assert calls["finish"] == 1


def test_stop_reason_triggers_finish_once(tmp_path: Path, monkeypatch) -> None:
    hook = _hook(tmp_path)
    calls = {"finish": 0}

    def fake_finish(conversation_id: str | None = None):
        calls["finish"] += 1
        return None

    monkeypatch.setattr(hook.orchestrator, "finish_episode", fake_finish)
    context = _context()
    context.stop_reason = "completed"

    asyncio.run(hook.after_iteration(context))
    asyncio.run(hook.after_iteration(context))

    assert calls["finish"] == 1


def test_guidance_is_injected_once(tmp_path: Path) -> None:
    runtime = SparseRead(SparseReadConfig(mode="auto", workspace=str(tmp_path)))
    hook = SparseReadHook(runtime, workspace=str(tmp_path), inject_guidance=True)
    messages = [{"role": "user", "content": "original"}]
    context = _context(messages=messages)

    asyncio.run(hook.before_iteration(context))
    asyncio.run(hook.before_iteration(context))

    assert messages[0]["role"] == "system"
    assert "# Sparse Reading" in messages[0]["content"]
    assert sum(1 for m in messages if m.get("role") == "system") == 1


def test_adapter_installs_without_host_sro_fields(tmp_path: Path) -> None:
    registry = SimpleNamespace(
        _tools={},
        has=lambda name: name in registry._tools,
        register=lambda tool: registry._tools.__setitem__(tool.name, tool),
        get=lambda name: registry._tools.get(name),
    )
    agent = SimpleNamespace(tools=registry, _extra_hooks=[])
    runtime = SparseRead(SparseReadConfig(mode="auto", workspace=str(tmp_path)))

    installed = NanobotAdapter().install(agent, runtime)

    assert "hook:sparse_read" in installed
    assert "sro_guard" in installed
    assert len(agent._extra_hooks) == 1
