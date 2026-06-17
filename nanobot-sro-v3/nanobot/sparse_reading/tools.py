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


class SroCardTool(Tool):
    def __init__(self, orchestrator: SparseReadingOrchestrator):
        self.orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "sro_card"

    @property
    def description(self) -> str:
        return "Return a lightweight FileCard for a large supported file or text-file collection before reading it."

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
        payload: dict[str, Any] = {"file_card": card.to_dict()}
        if card.sparse_recommended:
            mode = "collect" if "collect" in card.recommended_mode else card.recommended_mode
            payload["next_action"] = {
                "tool": "sro_read",
                "target": {"artifact_id": card.artifact_id},
                "mode": mode,
                "instruction": "For multi-question reports, copy each user question into one compact slot.",
                "hint": {
                    "goal": "state the evidence needed from this artifact",
                    "type_hint": "text" if card.type == "txt" else card.type,
                },
            }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class SroPreviewTool(Tool):
    def __init__(self, orchestrator: SparseReadingOrchestrator):
        self.orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "sro_preview"

    @property
    def description(self) -> str:
        return (
            "Production SparseRead entrypoint. Return a deterministic no-HintSpec L0 preview "
            "for a supported file or collection, with embedded minimal card metadata, samples, "
            "signals, raw_ref, and next-step guidance."
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
                    "description": "Path string or object with path/artifact_id.",
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "artifact_id": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    ],
                },
                "path": {"type": "string", "description": "Compatibility shortcut for target.path"},
                "artifact_id": {"type": "string", "description": "Compatibility shortcut for target.artifact_id"},
            },
        }

    async def execute(self, target: Any = None, path: str = "", artifact_id: str = "", **kwargs: Any) -> str:
        if target is None:
            target = {"artifact_id": artifact_id} if artifact_id else {"path": path}
        pack = self.orchestrator.preview(target)
        return json.dumps({"preview_pack": pack.to_dict()}, ensure_ascii=False, indent=2)


class SroRawTool(Tool):
    def __init__(self, orchestrator: SparseReadingOrchestrator):
        self.orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "sro_raw"

    @property
    def description(self) -> str:
        return "Retrieve original content behind a raw_ref returned by sro_preview, optionally by byte range or text selector."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "raw_ref": {"type": "string"},
                "range": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "integer"},
                        "end": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
                "selector": {"type": "string"},
            },
            "required": ["raw_ref"],
        }

    async def execute(
        self,
        raw_ref: str,
        range: dict[str, Any] | None = None,
        selector: str | None = None,
        **kwargs: Any,
    ) -> str:
        result = self.orchestrator.raw(raw_ref, range=range, selector=selector)
        return json.dumps({"raw": result}, ensure_ascii=False, indent=2)


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
            "For multi-question PDF/report tasks, the first read after sro_card should be mode=collect with hint.slots; do not use scout or a long needles list for that case. "
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
