"""Protocol data models for Sparse Reading Orchestrator."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Want = Literal["fact", "count", "verbatim", "table", "schema", "list"]
Scope = Literal["new", "narrow", "expand", "verify"]
Mode = Literal["scout", "focus", "refine", "verify", "collect"]
TypeHint = Literal["auto", "pdf", "text", "csv", "xlsx", "json", "yaml", "xml", "mixed", "collection"]

VALID_WANTS = {"fact", "count", "verbatim", "table", "schema", "list"}
VALID_SCOPES = {"new", "narrow", "expand", "verify"}
VALID_MODES = {"scout", "focus", "refine", "verify", "collect"}
VALID_TYPE_HINTS = {"auto", "pdf", "text", "csv", "xlsx", "json", "yaml", "xml", "mixed", "collection"}
MAX_HINT_NEEDLES = 10
MAX_HINT_SLOTS = 12
WANT_ALIASES = {
    "facts": "fact",
    "answer": "fact",
    "answers": "list",
    "number": "count",
    "numbers": "count",
    "total": "count",
    "totals": "count",
    "exact": "verbatim",
    "quote": "verbatim",
    "quotes": "verbatim",
    "string": "verbatim",
    "strings": "verbatim",
    "row": "table",
    "rows": "table",
    "record": "table",
    "records": "table",
    "complete data": "table",
    "all data": "table",
    "data rows": "table",
    "full structure": "schema",
    "structure overview": "schema",
    "structure": "schema",
    "overview": "schema",
}
SCOPE_ALIASES = {
    "all": "expand",
    "full": "expand",
    "more": "expand",
    "overview": "new",
}


@dataclass(slots=True)
class SlotSpec:
    id: str
    question: str
    expected: str = "fact"
    aliases: list[str] = field(default_factory=list)

    @classmethod
    def from_obj(cls, obj: Any, index: int) -> tuple["SlotSpec | None", list[str]]:
        if not isinstance(obj, dict):
            return None, [f"hint.slots[{index}] must be an object"]
        errors: list[str] = []
        slot_id = str(obj.get("id") or f"slot_{index + 1}").strip()
        question = str(obj.get("question") or obj.get("goal") or "").strip()
        expected = str(obj.get("expected") or "fact").strip().lower()
        aliases_raw = obj.get("aliases") or []
        if not slot_id:
            errors.append(f"hint.slots[{index}].id is required")
        if not question:
            if len(slot_id) > 20:
                question = slot_id[:240]
                slot_id = f"slot_{index + 1}"
            else:
                errors.append(f"hint.slots[{index}].question is required")
        if isinstance(aliases_raw, str):
            aliases = HintSpec._normalize_arrayish_text(aliases_raw, limit=8)
        elif isinstance(aliases_raw, list):
            aliases = [str(item).strip() for item in aliases_raw if str(item).strip()][:8]
        else:
            errors.append(f"hint.slots[{index}].aliases must be an array")
            aliases = []
        return cls(id=slot_id, question=question, expected=expected, aliases=aliases), errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HintSpec:
    goal: str
    needles: list[str] = field(default_factory=list)
    want: Want = "fact"
    scope: Scope = "new"
    artifact: str = ""
    type_hint: TypeHint = "auto"
    must_keep: list[str] = field(default_factory=list)
    slots: list[SlotSpec] = field(default_factory=list)

    @classmethod
    def from_obj(cls, obj: Any) -> tuple["HintSpec | None", list[str]]:
        if not isinstance(obj, dict):
            return None, ["hint must be an object matching the HintSpec schema"]
        errors: list[str] = []
        goal = str(obj.get("goal") or "").strip()
        slots, slot_errors = cls._parse_slots(obj.get("slots") or [])
        if not slots and "question" in json.dumps(obj, ensure_ascii=False):
            slots = cls._repair_embedded_slots(obj)
            if slots:
                slot_errors = []
        errors.extend(slot_errors)
        if not goal and not slots:
            errors.append("hint.goal is required")
        needles_raw = obj.get("needles") or []
        if isinstance(needles_raw, str):
            needles = cls._normalize_arrayish_text(needles_raw, limit=MAX_HINT_NEEDLES)
        elif not isinstance(needles_raw, list):
            errors.append("hint.needles must be an array")
            needles = []
        else:
            needles = [str(item).strip() for item in needles_raw if str(item).strip()]
            if len(needles) > MAX_HINT_NEEDLES:
                overflow = needles[MAX_HINT_NEEDLES:]
                needles = needles[:MAX_HINT_NEEDLES]
                # Auto-convert overflow needles to slots so collection reads don't fail
                auto_slots = []
                for idx, needle in enumerate(overflow, start=1):
                    auto_slots.append(SlotSpec(
                        id=f"needle_overflow_{idx}",
                        question=f"Find information about: {needle}",
                        expected="fact",
                        aliases=[needle],
                    ))
                if auto_slots and not slots:
                    slots = auto_slots
                    # Repair note: overflow handled, not a blocking error
                    errors.append(f"hint.needles truncated from {len(needles) + len(overflow)} to {MAX_HINT_NEEDLES}; {len(auto_slots)} overflow auto-converted (repair_ok)")
        want_raw = str(obj.get("want") or "fact").strip()
        want = cls._normalize_want(want_raw)
        if want not in VALID_WANTS:
            errors.append(f"hint.want must be one of {sorted(VALID_WANTS)}")
            want = "fact"
        scope = cls._normalize_scope(str(obj.get("scope") or "new").strip())
        if scope not in VALID_SCOPES:
            errors.append(f"hint.scope must be one of {sorted(VALID_SCOPES)}")
            scope = "new"
        type_hint = str(obj.get("type_hint") or "auto").strip()
        if type_hint not in VALID_TYPE_HINTS:
            type_hint = "auto"
        must_raw = obj.get("must_keep") or []
        if not isinstance(must_raw, list):
            errors.append("hint.must_keep must be an array")
            must_keep: list[str] = []
        else:
            must_keep = [str(item).strip() for item in must_raw if str(item).strip()]
        hint = cls(
            goal=goal,
            needles=needles,
            want=want,  # type: ignore[arg-type]
            scope=scope,  # type: ignore[arg-type]
            artifact=str(obj.get("artifact") or "").strip(),
            type_hint=type_hint,  # type: ignore[arg-type]
            must_keep=must_keep,
            slots=slots,
        )
        return hint, errors

    @classmethod
    def _parse_slots(cls, raw: Any) -> tuple[list[SlotSpec], list[str]]:
        if not raw:
            return [], []
        if not isinstance(raw, list):
            return [], ["hint.slots must be an array"]
        errors: list[str] = []
        slots: list[SlotSpec] = []
        for index, item in enumerate(raw[:MAX_HINT_SLOTS]):
            slot, slot_errors = SlotSpec.from_obj(item, index)
            repaired = cls._repair_embedded_slots(item) if slot_errors or cls._looks_like_embedded_slots(item) else []
            if repaired:
                slots.extend(repaired)
            elif slot is not None:
                errors.extend(slot_errors)
                slots.append(slot)
            else:
                errors.extend(slot_errors)
        if len(raw) > MAX_HINT_SLOTS:
            errors.append(f"hint.slots must contain at most {MAX_HINT_SLOTS} items")
        deduped: list[SlotSpec] = []
        seen_ids: set[str] = set()
        for slot in slots:
            slot_id = slot.id
            if slot_id in seen_ids:
                slot_id = f"{slot_id}_{len(seen_ids) + 1}"
                slot = SlotSpec(slot_id, slot.question, slot.expected, slot.aliases)
            seen_ids.add(slot_id)
            deduped.append(slot)
            if len(deduped) >= MAX_HINT_SLOTS:
                break
        return deduped, errors

    @staticmethod
    def _looks_like_embedded_slots(obj: Any) -> bool:
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except TypeError:
            text = str(obj)
        text = HintSpec._normalize_embedded_slot_text(text)
        return len(re.findall(r'"id"\s*:', text)) > 1 and '"question"' in text

    @staticmethod
    def _repair_embedded_slots(obj: Any) -> list[SlotSpec]:
        """Recover slots when a model smears JSON/DSML into a slot field."""
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except TypeError:
            text = str(obj)
        text = HintSpec._normalize_embedded_slot_text(text)
        text = re.sub(r"<[^>]+>", " ", text)
        chunks = re.split(r'(?="id"\s*:\s*"[^"]+")', text)
        repaired: list[SlotSpec] = []
        for chunk in chunks:
            id_match = re.search(r'"id"\s*:\s*"([^"]{1,40})"', chunk)
            if not id_match:
                continue
            question_matches = re.findall(
                r'"question"\s*:\s*(?:"question"\s*:\s*)?"([^"]{6,240})"',
                chunk,
            )
            question = next((value.strip() for value in question_matches if value.strip().lower() != "question"), "")
            if not question:
                continue
            expected_match = re.search(r'"expected"\s*:\s*"([^"]{1,40})"', chunk)
            slot_id = re.sub(r"[^A-Za-z0-9_-]+", "_", id_match.group(1).strip()).strip("_")
            if not slot_id:
                slot_id = f"slot_{len(repaired) + 1}"
            expected = (expected_match.group(1).strip().lower() if expected_match else "fact") or "fact"
            repaired.append(SlotSpec(slot_id, question, expected, []))
        return repaired

    @staticmethod
    def _normalize_embedded_slot_text(text: str) -> str:
        text = text.replace("\\n", " ")
        return re.sub(r'\\+"', '"', text)

    @staticmethod
    def _normalize_want(value: str) -> str:
        lowered = value.strip().lower()
        if lowered in VALID_WANTS:
            return lowered
        for canonical in VALID_WANTS:
            if canonical in lowered:
                return canonical
        if lowered in WANT_ALIASES:
            return WANT_ALIASES[lowered]
        for alias, canonical in WANT_ALIASES.items():
            if alias in lowered:
                return canonical
        return lowered

    @staticmethod
    def _normalize_arrayish_text(value: str, *, limit: int) -> list[str]:
        parts = [part.strip() for part in value.replace("\n", ",").split(",")]
        out = [part for part in parts if part]
        return out[:limit]

    @staticmethod
    def _normalize_scope(value: str) -> str:
        lowered = value.strip().lower()
        if lowered in VALID_SCOPES:
            return lowered
        if lowered in SCOPE_ALIASES:
            return SCOPE_ALIASES[lowered]
        for alias, canonical in SCOPE_ALIASES.items():
            if alias == lowered:
                return canonical
        return lowered

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FileCard:
    path: str
    artifact_id: str
    type: str
    size_bytes: int
    estimated_chars: int
    structured: bool
    sparse_recommended: bool
    recommended_mode: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceBlock:
    anchor: str
    text: str
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidencePack:
    artifact_id: str
    mode: str
    type: str
    summary: str
    skeleton: list[str] = field(default_factory=list)
    evidence: list[EvidenceBlock] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    calc_ready: dict[str, Any] | None = None
    slot_digest: dict[str, Any] | None = None
    next_action: dict[str, Any] | None = None
    next_hint: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = [block.to_dict() for block in self.evidence]
        return data
