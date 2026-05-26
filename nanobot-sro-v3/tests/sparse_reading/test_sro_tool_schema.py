import asyncio
import json
from pathlib import Path

from nanobot.sparse_reading.models import (
    MAX_HINT_NEEDLES,
    MAX_HINT_SLOTS,
    VALID_SCOPES,
    VALID_TYPE_HINTS,
    VALID_WANTS,
)
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator
from nanobot.sparse_reading.tools import SroCardTool, SroReadTool


def _schema(tmp_path: Path) -> dict:
    return SroReadTool(SparseReadingOrchestrator(tmp_path)).parameters


def test_sro_read_description_keeps_one_terminal_rule(tmp_path: Path) -> None:
    description = SroReadTool(SparseReadingOrchestrator(tmp_path)).description

    assert "Return sparse evidence" in description
    assert "ready for output" in description
    assert "write the deliverable" in description
    assert "overall_status" not in description
    assert "multi-question" not in description
    assert "calc_ready" not in description


def test_sro_read_schema_exposes_target_fields(tmp_path: Path) -> None:
    target = _schema(tmp_path)["properties"]["target"]

    assert set(target["properties"]) == {"path", "artifact_id"}
    assert target["additionalProperties"] is False


def test_sro_read_schema_exposes_hint_contract(tmp_path: Path) -> None:
    hint = _schema(tmp_path)["properties"]["hint"]
    properties = hint["properties"]

    assert set(properties) == {
        "goal",
        "needles",
        "want",
        "scope",
        "artifact",
        "type_hint",
        "must_keep",
        "slots",
    }
    assert set(properties["want"]["enum"]) == VALID_WANTS
    assert set(properties["scope"]["enum"]) == VALID_SCOPES
    assert set(properties["type_hint"]["enum"]) == VALID_TYPE_HINTS
    assert properties["needles"]["maxItems"] == MAX_HINT_NEEDLES
    assert properties["slots"]["maxItems"] == MAX_HINT_SLOTS


def test_sro_read_schema_exposes_canonical_slot_shape(tmp_path: Path) -> None:
    slot = _schema(tmp_path)["properties"]["hint"]["properties"]["slots"]["items"]

    assert set(slot["properties"]) == {"id", "question", "expected", "aliases"}
    assert slot["required"] == ["id", "question"]
    assert slot["properties"]["aliases"]["type"] == "array"
    assert slot["properties"]["aliases"]["maxItems"] == 8


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
