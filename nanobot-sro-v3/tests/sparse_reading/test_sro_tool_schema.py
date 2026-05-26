import asyncio
import json
from pathlib import Path

from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator
from nanobot.sparse_reading.tools import SroCardTool


def test_text_initial_hints_use_schema_type_hint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    text_path = tmp_path / "document.txt"
    text_path.write_text("answer evidence " * 500, encoding="utf-8")
    orchestrator = SparseReadingOrchestrator(tmp_path)

    direct = json.loads(asyncio.run(SroCardTool(orchestrator).execute(str(text_path))))
    handoff = json.loads(orchestrator.handoff_message(text_path))

    assert direct["file_card"]["type"] == "txt"
    assert direct["next_action"]["hint"]["type_hint"] == "text"
    assert handoff["next_action"]["hint"]["type_hint"] == "text"
