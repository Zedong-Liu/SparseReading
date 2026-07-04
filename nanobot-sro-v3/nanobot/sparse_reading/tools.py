"""Agent-facing Sparse Reading Orchestrator tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.sparse_reading.models import (
    MAX_HINT_NEEDLES,
    MAX_HINT_SLOTS,
    VALID_SCOPES,
    VALID_TYPE_HINTS,
    VALID_WANTS,
)
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator


class SroPreviewTool(Tool):
    def __init__(self, orchestrator: SparseReadingOrchestrator):
        self.orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "sro_preview"

    @property
    def description(self) -> str:
        return (
            "Production SparseRead entrypoint. Return a deterministic L0 preview for a supported file, "
            "document, log, structured file, or collection without requiring a HintSpec. The FileCard is "
            "included inside the preview; call sro_read only for targeted follow-up evidence."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file/object to preview"},
                "budget": {
                    "type": "integer",
                    "description": "Optional preview budget in characters; capped by the core scout budget.",
                    "minimum": 200,
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, budget: int | None = None, **kwargs: Any) -> str:
        payload = self.orchestrator.preview(Path(path), budget=budget)
        return json.dumps(payload, ensure_ascii=False, indent=2)


class SroCardTool(Tool):
    def __init__(self, orchestrator: SparseReadingOrchestrator):
        self.orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "sro_card"

    @property
    def description(self) -> str:
        return "Compatibility/benchmark tool: return only the lightweight FileCard. Prefer sro_preview for production use."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file/object to inspect"},
            },
            "required": ["path"],
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        card = self.orchestrator.card(Path(path))
        payload: dict[str, Any] = {
            "file_card": card.to_dict(),
            "compatibility_note": "sro_card is retained for benchmark/legacy flows; use sro_preview as the production entrypoint.",
        }
        if card.sparse_recommended:
            mode = "collect" if card.type == "collection" else "scout"
            type_hint = "text" if card.type in {"txt", "text"} else card.type
            payload["next_action"] = {
                "tool": "sro_read",
                "target": {"artifact_id": card.artifact_id},
                "mode": mode,
                "instruction": (
                    "Legacy card path: use scout/focus without slots for default discovery. "
                    "Use collect only after copying each concrete user question into hint.slots."
                ),
                "hint": {
                    "goal": "state the evidence needed from this artifact",
                    "type_hint": type_hint,
                    "needles": [],
                    "slots": [],
                },
            }
            if "collect" in card.recommended_mode and card.type != "collection":
                payload["collect_template"] = {
                    "tool": "sro_read",
                    "target": {"artifact_id": card.artifact_id},
                    "mode": "collect",
                    "hint": {
                        "goal": "answer concrete questions from this artifact",
                        "type_hint": type_hint,
                        "slots": [{"id": "q1", "question": "copy a concrete user question here", "expected": "fact"}],
                    },
                }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class SroReadTool(Tool):
    def __init__(self, orchestrator: SparseReadingOrchestrator):
        self.orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "sro_read"

    @property
    def description(self) -> str:
        return (
            "Read sparse evidence from a large object using mode scout/focus/collect/refine/verify. "
            "SRO evidence is authoritative for the requested evidence goal: if collect/focus returns overall_status=ready or no unresolved items, write every requested deliverable or run one short calculation instead of rereading source files. "
            "For multi-question PDF/report tasks, the first targeted read after sro_preview should be mode=collect with hint.slots; do not use scout or a long needles list for that case. "
            "For directory collections that require diagnosis/audit/rules/config facts, use mode=collect first; use mode=focus only when you need candidate filenames and not facts. "
            "Slots are lightweight objects with id, question, expected, and optional aliases; collect returns a compact slot_digest rather than a large evidence matrix. "
            "When calc_ready is returned, use the derived TSV artifact(s) in one short calculation script."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "object",
                    "description": "Use path for first discovery or artifact_id for follow-up.",
                    "properties": {
                        "path": {"type": "string"},
                        "artifact_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "mode": {
                    "type": "string",
                    "enum": ["scout", "focus", "collect", "refine", "verify"],
                },
                "hint": {
                    "type": "object",
                    "description": "Evidence request for this read.",
                    "properties": {
                        "goal": {"type": "string"},
                        "needles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": MAX_HINT_NEEDLES,
                        },
                        "want": {"type": "string", "enum": sorted(VALID_WANTS)},
                        "scope": {"type": "string", "enum": sorted(VALID_SCOPES)},
                        "artifact": {"type": "string"},
                        "type_hint": {"type": "string", "enum": sorted(VALID_TYPE_HINTS)},
                        "must_keep": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "slots": {
                            "type": "array",
                            "maxItems": MAX_HINT_SLOTS,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "question": {"type": "string"},
                                    "expected": {"type": "string"},
                                    "aliases": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "maxItems": 8,
                                    },
                                },
                                "required": ["id", "question"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["target", "mode", "hint"],
        }

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        # Some API models occasionally wrap mode/hint or stringify target despite
        # the schema. Let execute() normalize these instead of burning a retry.
        return []

    def _normalize_target(self, target: Any) -> Any:
        if isinstance(target, dict):
            return target
        if isinstance(target, str):
            try:
                parsed = json.loads(target)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
            known_ids = [artifact_id for artifact_id in self.orchestrator._artifacts if artifact_id in target]
            if known_ids:
                return {"artifact_id": known_ids[0]}
            if len(self.orchestrator._artifacts) == 1:
                return {"artifact_id": next(iter(self.orchestrator._artifacts))}
        return target

    @staticmethod
    def _normalize_mode_hint(mode: Any, hint: Any) -> tuple[Any, Any]:
        if isinstance(mode, dict):
            if hint is None and isinstance(mode.get("hint"), dict):
                hint = mode["hint"]
            mode = mode.get("mode")
        return mode, hint

    async def execute(
        self,
        target: Any = None,
        mode: Any = None,
        hint: Any = None,
        **kwargs: Any,
    ) -> str:
        mode, hint = self._normalize_mode_hint(mode, hint)
        target = self._normalize_target(target)
        if not isinstance(target, dict):
            return "Error: target must be {'path': ...} or {'artifact_id': ...}."
        if not isinstance(mode, str):
            return "Error: mode must be one of scout, focus, collect, refine, verify."
        if not isinstance(hint, dict):
            hint = {}
        pack = self.orchestrator.read(target, mode, hint)
        return json.dumps({"evidence_pack": pack.to_dict()}, ensure_ascii=False, indent=2)
