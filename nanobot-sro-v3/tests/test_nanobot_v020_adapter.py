"""Compatibility contracts for the NanoBot 0.2 tool-loader integration."""

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus


def test_v020_tool_loader_shares_one_sro_orchestrator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 5000)

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )

    assert loop._sro is not None
    for name in ("read_file", "write_file", "list_dir", "grep"):
        assert loop.tools.get(name)._sro is loop._sro
    assert loop.tools.get("exec").sro_policy._sro is loop._sro
    for name in ("sro_preview", "sro_raw", "sro_card", "sro_read"):
        assert loop.tools.has(name)

