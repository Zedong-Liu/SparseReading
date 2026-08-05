"""Agent-facing Sparse Reading Orchestrator tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from sparseread.core.models import (
    MAX_HINT_NEEDLES,
    MAX_HINT_SLOTS,
    VALID_SCOPES,
    VALID_TYPE_HINTS,
    VALID_WANTS,
)
from sparseread.core.orchestrator import SparseReadingOrchestrator

_EPISODE_HINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Optional host-model judgment at a reading-episode boundary.",
    "properties": {
        "relation": {"type": "string", "enum": ["new", "continue", "switch", "unknown"]},
        "goal": {
            "type": "string",
            "enum": [
                "selective_read",
                "cross_file_evidence",
                "structured_compute",
                "edit_or_execute",
                "full_fidelity",
                "unknown",
            ],
        },
        "coverage": {"type": "string", "enum": ["selective", "exhaustive", "unknown"]},
        "summary": {"type": "string", "maxLength": 500},
    },
    "additionalProperties": False,
}


class Tool:
    """Framework-neutral async tool contract used by SparseRead core.

    Adapters may register these objects directly in duck-typed registries or
    wrap them in a framework-specific tool base class.
    """

    _TYPE_MAP: ClassVar[dict[str, type | tuple[type, ...]]] = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    @property
    def read_only(self) -> bool:
        return False

    @property
    def concurrency_safe(self) -> bool:
        return self.read_only

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return params

    def set_context(self, context: Any) -> None:
        self.orchestrator.set_context(context)

    def _episode_payload(self, episode_hint: Any) -> dict[str, Any]:
        if not isinstance(episode_hint, dict):
            return {}
        episode = self.orchestrator.current_episode()
        if episode is None:
            return {}
        return {
            "episode": episode.to_dict(),
            "decision": {
                "mode": episode.decision.mode,
                "code": episode.decision.code,
                "reason": episode.decision.reason,
                "confidence": episode.decision.confidence,
                "preview_recommended": episode.decision.preview_recommended,
            },
        }

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if not isinstance(params, dict):
            return [f"parameters must be an object, got {type(params).__name__}"]
        return self._validate(params, {**self.parameters, "type": "object"})

    @classmethod
    def _validate(cls, value: Any, schema: dict[str, Any], path: str = "") -> list[str]:
        expected = schema.get("type")
        label = path or "parameter"
        if expected in cls._TYPE_MAP and not isinstance(value, cls._TYPE_MAP[expected]):
            return [f"{label} should be {expected}"]
        errors: list[str] = []
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if expected == "object":
            properties = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(f"missing required {path + '.' if path else ''}{key}")
            for key, item in value.items():
                if key in properties:
                    item_path = f"{path}.{key}" if path else key
                    errors.extend(cls._validate(item, properties[key], item_path))
        if expected == "array" and "items" in schema:
            for index, item in enumerate(value):
                errors.extend(cls._validate(item, schema["items"], f"{path}[{index}]"))
        return errors


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
                "episode_hint": _EPISODE_HINT_SCHEMA,
            },
            "required": ["path"],
        }

    async def execute(self, path: str, episode_hint: Any = None, **kwargs: Any) -> str:
        card = self.orchestrator.card(Path(path), episode_hint if isinstance(episode_hint, dict) else None)
        payload: dict[str, Any] = {
            "file_card": card.to_dict(),
            "compatibility_note": "sro_card is retained for benchmark/legacy flows; use sro_preview as the production entrypoint.",
            **self._episode_payload(episode_hint),
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
                "episode_hint": _EPISODE_HINT_SCHEMA,
            },
        }

    async def execute(
        self,
        target: Any = None,
        path: str = "",
        artifact_id: str = "",
        episode_hint: Any = None,
        **kwargs: Any,
    ) -> str:
        if target is None:
            target = {"artifact_id": artifact_id} if artifact_id else {"path": path}
        target_path = target.get("path") if isinstance(target, dict) else target
        if not isinstance(episode_hint, dict) and isinstance(target_path, str) and target_path:
            probe = self.orchestrator.episode_hint_probe(target_path, tool_name="sro_preview")
            if probe:
                return json.dumps(probe, ensure_ascii=False, indent=2)
        pack = self.orchestrator.preview(target, episode_hint if isinstance(episode_hint, dict) else None)
        return json.dumps(
            {"preview_pack": pack.to_dict(), **self._episode_payload(episode_hint)},
            ensure_ascii=False,
            indent=2,
        )


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
                "episode_hint": _EPISODE_HINT_SCHEMA,
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
        episode_hint: Any = None,
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
        else:
            hint = dict(hint)
        if not isinstance(episode_hint, dict) and isinstance(hint.get("episode_hint"), dict):
            episode_hint = hint.pop("episode_hint")
        pack = self.orchestrator.read(
            target,
            mode,
            hint,
            episode_hint if isinstance(episode_hint, dict) else None,
        )
        return json.dumps(
            {"evidence_pack": pack.to_dict(), **self._episode_payload(episode_hint)},
            ensure_ascii=False,
            indent=2,
        )
