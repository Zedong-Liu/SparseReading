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
from nanobot.sparse_reading.tools import SroCardTool, SroPreviewTool, SroReadTool


def _schema(tmp_path: Path) -> dict:
    return SroReadTool(SparseReadingOrchestrator(tmp_path)).parameters


def test_sro_read_description_retains_p0_guidance(tmp_path: Path) -> None:
    description = SroReadTool(SparseReadingOrchestrator(tmp_path)).description

    assert "Read sparse evidence" in description
    assert "overall_status=ready" in description
    assert "mode=collect with hint.slots" in description
    assert "after sro_preview" in description
    assert "For directory collections" in description
    assert "candidate filenames and not facts" in description
    assert "slot_digest" in description
    assert "calc_ready" in description


def test_sro_read_schema_exposes_standard_target_shape(tmp_path: Path) -> None:
    target = _schema(tmp_path)["properties"]["target"]

    assert set(target["properties"]) == {"path", "artifact_id"}
    assert target["additionalProperties"] is False


def test_sro_preview_schema_is_l0_entrypoint(tmp_path: Path) -> None:
    tool = SroPreviewTool(SparseReadingOrchestrator(tmp_path))

    assert tool.name == "sro_preview"
    assert "without requiring a HintSpec" in tool.description
    assert set(tool.parameters["properties"]) == {"path", "budget"}
    assert tool.parameters["required"] == ["path"]


def test_sro_read_schema_uses_hintspec_contract(tmp_path: Path) -> None:
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
    assert hint["additionalProperties"] is False


def test_sro_read_schema_exposes_standard_slot_shape(tmp_path: Path) -> None:
    slot = _schema(tmp_path)["properties"]["hint"]["properties"]["slots"]["items"]

    assert set(slot["properties"]) == {"id", "question", "expected", "aliases"}
    assert slot["required"] == ["id", "question"]
    assert slot["properties"]["aliases"]["maxItems"] == 8
    assert slot["additionalProperties"] is False


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


def test_legacy_card_defaults_to_executable_scout_for_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    text_path = tmp_path / "document.txt"
    text_path.write_text("answer evidence " * 500, encoding="utf-8")
    orchestrator = SparseReadingOrchestrator(tmp_path)

    direct = json.loads(asyncio.run(SroCardTool(orchestrator).execute(str(text_path))))

    assert direct["compatibility_note"]
    assert direct["next_action"]["mode"] == "scout"
    assert direct["next_action"]["hint"]["slots"] == []
    assert direct["collect_template"]["mode"] == "collect"
