"""Sparse Reading Orchestrator state and routing."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from collections.abc import Callable
from typing import Any

from nanobot.sparse_reading.benefit_gate import BenefitDecision, BenefitGate
from nanobot.sparse_reading.detector import FileInfo, inspect_file
from nanobot.sparse_reading.models import (
    VALID_MODES,
    EvidenceBlock,
    EvidencePack,
    FileCard,
    HintSpec,
    PreviewPack,
)
from nanobot.sparse_reading.preview import PreviewBuilder
from nanobot.sparse_reading.readers.structured import StructuredReader
from nanobot.sparse_reading.readers.collection import CollectionReader
from nanobot.sparse_reading.readers.text import TextReader


class SparseReadingOrchestrator:
    """Coordinate FileCard, HintSpec, typed readers, and artifact continuity."""

    _STRUCTURED = {"csv", "xlsx", "json", "yaml", "xml"}
    _TEXT = {"pdf", "text", "txt", "md", "markdown", "rst"}
    _MAX_RAW_REFS = 512

    def __init__(
        self,
        workspace: Path | None = None,
        *,
        macro_activation_callback: Callable[[], None] | None = None,
        macro_available: bool = False,
        benefit_gate_override: str | None = None,
    ) -> None:
        self.workspace = workspace
        self._path_to_artifact: dict[str, str] = {}
        self._artifacts: dict[str, FileInfo] = {}
        self.structured_reader = StructuredReader()
        self.collection_reader = CollectionReader()
        self.benefit_gate = BenefitGate(self.collection_reader, override=benefit_gate_override)
        self.text_reader = TextReader()
        self.preview_builder = PreviewBuilder(self.collection_reader, self.text_reader)
        self._slot_digests: dict[str, dict[str, Any]] = {}
        self._collection_child_guards: dict[str, str] = {}
        self._ready_collection_child_guards: dict[str, str] = {}
        self._collection_artifact_children: dict[str, set[str]] = {}
        self._ready_collection_artifacts: dict[str, dict[str, Any]] = {}
        self._ready_collection_evidence: dict[str, list[EvidenceBlock]] = {}
        self._ready_collection_guard_counts: dict[str, int] = {}
        self._native_escape_collection_artifacts: set[str] = set()
        self._required_outputs_by_artifact: dict[str, set[str]] = {}
        self._written_outputs_by_artifact: dict[str, set[str]] = {}
        self._native_collection_roots: set[str] = set()
        self._diagnostic_sections: dict[str, dict[str, str]] = {}
        self._raw_refs: dict[str, Path] = {}
        self._macro_activation_callback = macro_activation_callback
        self._macro_available = macro_available
        self._macro_requested = False

    @property
    def macro_available(self) -> bool:
        return self._macro_available

    @property
    def macro_requested(self) -> bool:
        return self._macro_requested

    def mark_macro_available(self) -> None:
        self._macro_available = True

    def request_macro_activation(self) -> None:
        self._macro_requested = True
        if self._macro_available:
            return
        if self._macro_activation_callback is not None:
            self._macro_activation_callback()

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("SRO_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def disabled_for_low_sparse_workspace(cls, workspace: str | Path | None) -> bool:
        if not cls.enabled():
            return True
        if workspace is None:
            return False
        try:
            reader = CollectionReader()
            decision = BenefitGate(reader).decide(inspect_file(Path(workspace)))
            return decision.mode != "force_sro"
        except Exception:
            return False

    def inspect(self, path: str | Path) -> FileInfo:
        return inspect_file(path)

    @staticmethod
    def is_calc_artifact(path: str | Path) -> bool:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            resolved = Path(path)
        parts = resolved.parts
        return ".nanobot" in parts and "sro-calc" in parts

    @staticmethod
    def is_output_artifact(path: str | Path) -> bool:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            resolved = Path(path)
        generated_names = {
            "answer.txt",
            "command_classifications.json",
            "fetch-audit.md",
            "final_answer.md",
            "diagnosis_report.md",
            "did_results_summary.md",
            "metrics_summary.json",
            "monitoring-status.md",
            "analysis_results.json",
            "security_analysis_report.md",
            "solution_report.md",
            "eviction_analysis.json",
            "bug_fixes.json",
            "query_output.sparql",
            "filtered_query.sparql",
        }
        if resolved.name.lower() in generated_names:
            return True
        output_dirs = {"reports", "outputs", "results"}
        return any(part.lower() in output_dirs for part in resolved.parts)

    @staticmethod
    def is_skill_artifact(path: str | Path) -> bool:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            resolved = Path(path)
        parts = [part.lower() for part in resolved.parts]
        return "nanobot" in parts and "skills" in parts

    @staticmethod
    def is_runtime_artifact(path: str | Path) -> bool:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            resolved = Path(path)
        parts = {part.lower() for part in resolved.parts}
        return bool(parts & {".nanobot", "sessions", "bootstrap", "__pycache__"})

    def should_handoff_read(self, path: str | Path, *, offset: int = 1, limit: int | None = None, pages: str | None = None) -> bool:
        if not self.enabled():
            return False
        if self._outside_workspace(path):
            return False
        if self.is_calc_artifact(path):
            return False
        if self.is_output_artifact(path):
            return False
        if self.is_skill_artifact(path):
            return False
        if self.is_runtime_artifact(path):
            return False
        info = self.inspect(path)
        decision = self.benefit_gate.decide(info)
        if self._is_native_collection_child(path) and decision.action != "intercept":
            return False
        ready_child_artifact = self._ready_collection_child_artifact(path)
        if ready_child_artifact:
            return True
        if self._collection_child_guard(path):
            return True
        if self._parent_collection_artifact(path):
            return True
        if self._nearest_force_collection_root(path) is not None:
            return True
        if not self._macro_available and self._is_weak_lazy_child(path, info, decision):
            return False
        if self._is_child_of_nonforce_benefit_bundle(path, child_decision=decision):
            return False
        if decision.action != "intercept":
            return False
        if offset and offset > 1:
            return False
        if limit is not None and limit < 400:
            return False
        if pages:
            return False
        return True

    def should_handoff_list(self, path: str | Path) -> bool:
        if not self.enabled():
            return False
        if self._outside_workspace(path):
            return False
        try:
            if str(Path(path).resolve(strict=False)) in self._native_collection_roots:
                return False
        except Exception:
            pass
        info = self.inspect(path)
        artifact_id = self._path_to_artifact.get(str(info.path))
        if artifact_id and self._is_native_escape_collection(artifact_id):
            return False
        decision = self.benefit_gate.decide(info)
        if decision.mode == "native":
            self._remember_native_collection_root(info)
        return info.type == "collection" and decision.action == "intercept"

    def card(self, path: str | Path) -> FileCard:
        info = self.inspect(path)
        artifact_id = self._artifact_for(info)
        decision = self.benefit_gate.decide(info)
        reason = decision.reason
        recommended_mode = decision.recommended_mode
        details: dict[str, Any] = {}
        if not info.supported:
            reason = "unsupported type; use native tools"
        elif not info.large:
            reason = "small supported object; native read is acceptable"
        elif info.type == "collection":
            details = self.collection_reader.card_details(info.path)
            self._remember_collection_artifact_children(info.path, self._artifact_for(info))
            if decision.mode == "native":
                self._remember_native_collection_root(info)
        elif info.structured:
            details = self.structured_reader.card_details(info.path)
        return FileCard(
            path=str(info.path),
            artifact_id=artifact_id,
            type=info.type,
            size_bytes=info.size_bytes,
            estimated_chars=info.size_bytes,
            structured=info.structured,
            sparse_recommended=decision.action == "intercept",
            recommended_mode=recommended_mode,
            reason=reason,
            details=details,
        )

    def preview(self, target: Any) -> PreviewPack:
        artifact_id, info, err = self._resolve_preview_target(target)
        if err:
            return PreviewPack(
                artifact_id=artifact_id or "",
                card={},
                summary="preview target error",
                raw_ref="",
                error=err,
            )
        if info is None:
            return PreviewPack(
                artifact_id=artifact_id or "",
                card={},
                summary="preview target error",
                raw_ref="",
                error="preview target could not be resolved",
            )
        card = self.card(info.path)
        raw_ref = self._raw_ref_for(card.artifact_id, info.path)
        return self.preview_builder.build(info, card, raw_ref)

    def raw(self, raw_ref: str, *, range: dict[str, Any] | None = None, selector: str | None = None) -> dict[str, Any]:
        resolved_ref, path = self._resolve_raw_ref(str(raw_ref or ""))
        if path is None:
            return {
                "raw_ref": raw_ref,
                "error": "unknown or stale raw_ref; call sro_preview again",
            }
        parts = resolved_ref.split(":", 2)
        artifact_id = parts[1] if len(parts) == 3 else ""
        if artifact_id in self._ready_collection_artifacts:
            return {
                "raw_ref": resolved_ref,
                "sro_guard": True,
                "covered_by_artifact": artifact_id,
                "error": "collection evidence is already ready; raw retrieval is suppressed",
                "next_action": self._ready_collection_artifacts[artifact_id],
            }
        if path.is_dir():
            entries = [str(entry.relative_to(path)) for entry in sorted(path.rglob("*")) if entry.is_file()]
            if selector:
                query = selector.strip().strip("\"'")
                if query.startswith("./"):
                    query = query[2:]
                selected = None
                root = path.resolve(strict=False)
                direct = (path / query).resolve(strict=False)
                if direct.is_file():
                    try:
                        direct.relative_to(root)
                    except ValueError:
                        direct = None
                    else:
                        selected = direct
                if selected is None:
                    files = [entry for entry in sorted(path.rglob("*")) if entry.is_file()]
                    selected = next((entry for entry in files if str(entry.relative_to(path)) == query), None)
                    if selected is None:
                        selected = next((entry for entry in files if entry.name == query), None)
                    if selected is None:
                        selected = next((entry for entry in files if query.lower() in str(entry.relative_to(path)).lower()), None)
                if selected is not None:
                    try:
                        text, raw_view = self._raw_text_view(selected)
                    except Exception as exc:
                        return {
                            "raw_ref": resolved_ref,
                            "path": str(selected),
                            "selector": selector,
                            "error": f"could not read raw content: {exc}",
                        }
                    start, end = self._raw_range_bounds(text, range)
                    return {
                        "raw_ref": resolved_ref,
                        "path": str(selected),
                        "type": "collection_child",
                        "view": raw_view,
                        "selector": selector,
                        "range": {"start": start, "end": end},
                        "content": text[start:end],
                        "truncated": end < len(text),
                    }
            result = {
                "raw_ref": resolved_ref,
                "path": str(path),
                "type": "collection",
                "entries": entries[:500],
                "truncated": len(entries) > 500,
            }
            if selector:
                result["error"] = f"selector did not match a file in collection: {selector}"
            return result
        try:
            text, raw_view = self._raw_text_view(path)
        except Exception as exc:
            return {
                "raw_ref": resolved_ref,
                "path": str(path),
                "error": f"could not read raw content: {exc}",
            }
        start, end = self._raw_range_bounds(text, range)
        if selector:
            lines = text.splitlines()
            matched = [
                {"line": idx + 1, "text": line}
                for idx, line in enumerate(lines)
                if selector.lower() in line.lower()
            ]
            return {
                "raw_ref": resolved_ref,
                "path": str(path),
                "view": raw_view,
                "selector": selector,
                "matches": matched[:200],
                "truncated": len(matched) > 200,
            }
        return {
            "raw_ref": resolved_ref,
            "path": str(path),
            "view": raw_view,
            "range": {"start": start, "end": end},
            "content": text[start:end],
            "truncated": end < len(text),
        }

    def handoff_message(self, path: str | Path) -> str:
        self.request_macro_activation()
        child_guard = self._collection_child_guard(path)
        if child_guard:
            return child_guard
        parent_artifact = self._parent_collection_artifact(path)
        if parent_artifact:
            parent_info = self._artifacts[parent_artifact]
            payload = {
                "sro_handoff": True,
                "message": (
                    "This file is inside an already-bound collection artifact. "
                    "Do not open a separate single-file artifact or full-read the child file. "
                    "Use the parent collection artifact to collect the cross-file facts."
                ),
                "covered_by_artifact": parent_artifact,
                "file_card": self.card(parent_info.path).to_dict(),
                "next_action": {
                    "tool": "sro_read",
                    "target": {"artifact_id": parent_artifact},
                    "mode": "collect",
                    "instruction": "For audit, diagnosis, rules, or cross-file analysis, collect facts from the parent collection and then write the deliverable.",
                    "hint": {
                        "goal": "collect task facts from the parent collection",
                        "needles": [str(Path(path).name)],
                        "want": "fact",
                        "scope": "new",
                        "artifact": parent_artifact,
                        "type_hint": "collection",
                    },
                },
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        parent_root = self._nearest_force_collection_root(path)
        if parent_root is not None:
            parent_card = self.card(parent_root)
            payload = {
                "sro_handoff": True,
                "message": (
                    "This file is part of a force-SRO collection. Bind to the parent collection, "
                    "collect the cross-file facts once, then write the deliverable."
                ),
                "covered_by_artifact": parent_card.artifact_id,
                "file_card": parent_card.to_dict(),
                "next_action": {
                    "tool": "sro_read",
                    "target": {"artifact_id": parent_card.artifact_id},
                    "mode": "collect",
                    "instruction": "Use the parent collection artifact for audit/security/cross-file closure instead of opening separate child artifacts.",
                    "hint": {
                        "goal": "collect task facts from the parent collection",
                        "needles": [str(Path(path).name)],
                        "want": "fact",
                        "scope": "new",
                        "artifact": parent_card.artifact_id,
                        "type_hint": "collection",
                    },
                },
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        card = self.card(path)
        if card.structured:
            payload = {
                "sro_handoff": True,
                "message": "Large structured object detected. The FileCard includes schema/row-count metadata. For calculations, regressions, joins, and aggregations, write a short script that reads the local file path directly; do not request all rows into chat. Use sro_read only for additional schema or specific row evidence.",
                "file_card": card.to_dict(),
                "next_action": {
                    "allowed_next": [
                        "write or run a script that reads the local file path",
                        "sro_read scout for additional schema only",
                    ],
                    "instruction": "Do not call sro_read for all rows. Use the file path in code for exact computation.",
                },
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        payload = {
            "sro_handoff": True,
            "message": "Large supported object detected. Do not full-read it first, and do not keep calling read_file on the same object. Bind to the returned artifact_id and continue with sro_read using a HintSpec. For multi-question PDF/report QA, use mode='collect' with hint.slots as the first read. For collections, use mode='collect' to get source-keyed excerpts for the task; use mode='focus' only when you only need candidate filenames.",
            "file_card": card.to_dict(),
            "next_action": {
                "tool": "sro_read",
                "target": {"artifact_id": card.artifact_id},
                "mode": "collect" if card.type == "collection" else "scout",
                "hint": {
                    "goal": "state what evidence is needed from this object",
                    "needles": [],
                    "want": "fact",
                    "scope": "new",
                    "artifact": card.artifact_id,
                    "type_hint": "text" if card.type == "txt" else card.type,
                    "must_keep": [],
                },
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def read(self, target: Any, mode: str, hint_obj: Any) -> EvidencePack:
        if mode not in VALID_MODES:
            return EvidencePack("", mode, "unknown", "protocol error", error=f"mode must be one of {sorted(VALID_MODES)}")
        hint, errors = HintSpec.from_obj(hint_obj)
        if hint is None:
            return EvidencePack("", mode, "unknown", "protocol error", error="; ".join(errors))
        if mode in {"refine", "verify"} and not self._target_artifact_id(target, hint):
            return EvidencePack(
                "",
                mode,
                "unknown",
                "protocol error",
                error="refine and verify require target.artifact_id or hint.artifact",
                next_hint={
                    "action": (
                        "call the available discovery tool first "
                        "(sro_preview in auto, sro_card in bench_protocol/debug), "
                        "then use the returned artifact_id with a concrete HintSpec"
                    )
                },
            )
        artifact_id, info, err = self._resolve_target(target, hint)
        if err:
            return EvidencePack(artifact_id or "", mode, "unknown", "protocol error", error=err)
        if info is None:
            return EvidencePack(artifact_id or "", mode, "unknown", "protocol error", error="target could not be resolved")
        if info.type in self._TEXT or info.path.suffix.lower() in {".txt", ".md", ".markdown", ".rst", ".pdf"}:
            gated = self._text_readiness_gate(artifact_id, info.type, mode, hint)
            if gated:
                return gated
        # Filter repair_ok errors: these are auto-healing, not blocking
        blocking_errors = [e for e in errors if "repair_ok" not in e]
        if blocking_errors:
            return self._invalid_hint_pack(artifact_id, mode, info.type, hint, blocking_errors)
        if hint.artifact and hint.artifact != artifact_id:
            return EvidencePack(
                artifact_id,
                mode,
                info.type,
                "protocol warning",
                error=f"hint.artifact {hint.artifact!r} does not match target artifact_id {artifact_id!r}",
                unresolved=list(hint.needles),
            )

        if info.type == "collection":
            if self._is_native_escape_collection(artifact_id):
                return self._native_escape_pack(artifact_id, mode)
            decision = self.benefit_gate.decide(info)
            if decision.mode == "native":
                self._remember_native_collection_root(info)
                return self._native_collection_pack(artifact_id, mode, decision)
            gated = self._collection_readiness_gate(artifact_id, mode)
            if gated:
                return gated
            budget = self._collection_budget(mode)
            pack = self.collection_reader.read(info.path, artifact_id, mode, hint, budget)
            if pack.next_action and "_diagnostic_sections" in pack.next_action:
                self._diagnostic_sections[artifact_id] = pack.next_action.pop("_diagnostic_sections")
            if mode == "collect" and pack.evidence:
                self._remember_collection_children(info.path, artifact_id, pack)
            return pack
        budget = self._budget(mode)
        if info.type in self._STRUCTURED:
            return self.structured_reader.read(info.path, artifact_id, mode, hint, budget)
        if info.type in self._TEXT or info.path.suffix.lower() in {".txt", ".md", ".markdown", ".rst", ".pdf"}:
            pack = self.text_reader.read(info.path, artifact_id, mode, hint, budget)
            if pack.slot_digest:
                self._slot_digests[artifact_id] = pack.slot_digest
            return pack
        return EvidencePack(artifact_id, mode, info.type, "unsupported type", error=f"SRO does not support {info.path}")

    def _resolve_preview_target(self, target: Any) -> tuple[str, FileInfo | None, str]:
        if isinstance(target, str):
            target = {"path": target}
        if not isinstance(target, dict):
            return "", None, "target must be a path string or object with path/artifact_id"
        artifact_id = str(target.get("artifact_id") or "").strip()
        if artifact_id:
            info = self._artifacts.get(artifact_id)
            if info:
                return artifact_id, info, ""
            return artifact_id, None, f"unknown artifact_id {artifact_id!r}; call sro_preview with path first"
        path = str(target.get("path") or "").strip()
        if not path:
            return "", None, "target.path is required"
        info = self.inspect(path)
        artifact_id = self._artifact_for(info)
        return artifact_id, info, ""

    def _raw_ref_for(self, artifact_id: str, path: Path) -> str:
        key = f"raw:{artifact_id}:{hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:10]}"
        self._raw_refs.pop(key, None)
        self._raw_refs[key] = path
        while len(self._raw_refs) > self._MAX_RAW_REFS:
            self._raw_refs.pop(next(iter(self._raw_refs)))
        return key

    def _resolve_raw_ref(self, raw_ref: str) -> tuple[str, Path | None]:
        path = self._raw_refs.get(raw_ref)
        if path is not None:
            return raw_ref, path
        artifact_id = self._raw_ref_artifact_id(raw_ref)
        if artifact_id:
            prefix = f"raw:{artifact_id}:"
            matches = [key for key in self._raw_refs if key.startswith(prefix)]
            if len(matches) == 1:
                key = matches[0]
                return key, self._raw_refs[key]
        return raw_ref, None

    def _raw_text_view(self, path: Path) -> tuple[str, str]:
        if path.suffix.lower() == ".pdf":
            units, _, _ = self.text_reader._load_units(path)
            lines = []
            for unit in units:
                text = " ".join(unit.text.split())
                if text:
                    lines.append(f"{unit.anchor} {text}")
            return "\n".join(lines), "extracted_text"
        return path.read_text(encoding="utf-8", errors="replace"), "original_text"

    @staticmethod
    def _raw_ref_artifact_id(raw_ref: str) -> str:
        parts = raw_ref.split(":")
        if len(parts) < 2 or parts[0] != "raw" or not parts[1].startswith("sro_"):
            return ""
        return parts[1]

    @staticmethod
    def _raw_range_bounds(text: str, range_obj: dict[str, Any] | None) -> tuple[int, int]:
        if not range_obj:
            return 0, min(len(text), 50_000)
        try:
            start = max(0, int(range_obj.get("start", 0)))
        except (TypeError, ValueError):
            start = 0
        try:
            end = int(range_obj.get("end", start + 50_000))
        except (TypeError, ValueError):
            end = start + 50_000
        end = max(start, min(len(text), end))
        return start, end


    @staticmethod
    def _invalid_hint_pack(
        artifact_id: str,
        mode: str,
        info_type: str,
        hint: HintSpec,
        errors: list[str],
    ) -> EvidencePack:
        next_action = None
        if any("hint.slots" in error for error in errors):
            next_action = {
                "allowed_next": ["retry_sro_read"],
                "tool": "sro_read",
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "instruction": (
                    "Retry once with complete slots. Copy every user question into "
                    "hint.slots as compact {id, question, expected} objects; do not stop."
                ),
                "accepted_slot_ids": [slot.id for slot in hint.slots],
                "missing_or_invalid": errors,
            }
        return EvidencePack(
            artifact_id,
            mode,
            info_type,
            "invalid HintSpec",
            error="; ".join(errors),
            unresolved=list(hint.needles),
            next_action=next_action,
        )

    def _text_readiness_gate(self, artifact_id: str, info_type: str, mode: str, hint: HintSpec) -> EvidencePack | None:
        existing = self._slot_digests.get(artifact_id)
        if not existing:
            return None
        if hint.slots:
            existing_ids = {str(slot.get("id")) for slot in existing.get("slots", []) if slot.get("id")}
            requested_ids = {slot.id for slot in hint.slots}
            if not requested_ids or not requested_ids.issubset(existing_ids):
                return None
        status = str(existing.get("overall_status") or "")
        if status not in {"ready", "needs_verify"}:
            return None
        if mode == "verify" and hint.slots and self._allow_text_slot_verify(existing, hint):
            return None
        digest = dict(existing)
        if status == "ready":
            digest["guard"] = "slot coverage is ready; use the existing candidates and write the deliverable"
            digest["allowed_next"] = ["write_file"]
        else:
            digest["guard"] = "slot coverage is near-ready; do not broad-read this artifact again"
            digest["allowed_next"] = ["verify specific slots only", "write_file"]
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type=info_type,
            summary="slot coverage already available; broad collect/focus suppressed",
            slot_digest=digest,
            unresolved=list(existing.get("unresolved_slots", [])),
            next_action={"allowed_next": digest["allowed_next"], "unresolved_slots": digest.get("unresolved_slots", [])},
        )

    @staticmethod
    def _allow_text_slot_verify(existing: dict[str, Any], hint: HintSpec) -> bool:
        slots_by_id = {str(slot.get("id")): slot for slot in existing.get("slots", []) if slot.get("id")}
        requested = [(slot, slots_by_id.get(slot.id)) for slot in hint.slots]
        if not requested or any(existing_slot is None for _, existing_slot in requested):
            return False
        if str(existing.get("overall_status") or "") == "needs_verify":
            return True
        return all(
            SparseReadingOrchestrator._text_slot_candidate_is_suspicious(existing_slot, requested_slot)
            for requested_slot, existing_slot in requested
            if existing_slot
        )

    def has_text_slot_digest(self, path: str | Path) -> bool:
        try:
            info = self.inspect(path)
        except Exception:
            return False
        artifact_id = self._path_to_artifact.get(str(info.path))
        return bool(artifact_id and artifact_id in self._slot_digests)

    @staticmethod
    def _text_slot_candidate_is_suspicious(slot: dict[str, Any], requested_slot: Any | None = None) -> bool:
        candidate = str(slot.get("candidate") or "").strip()
        try:
            confidence = float(slot.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < 0.9:
            return True
        if slot.get("needs_verify_reason"):
            return True
        if not candidate:
            return True
        if candidate.endswith(("[clipped]", "...")):
            return True
        if len(candidate) <= 3 and not candidate.isdigit():
            return True
        return SparseReadingOrchestrator._text_candidate_format_mismatch(candidate, requested_slot)

    @staticmethod
    def _text_candidate_format_mismatch(candidate: str, requested_slot: Any | None) -> bool:
        if requested_slot is None:
            return False
        prompt = f"{getattr(requested_slot, 'expected', '')} {getattr(requested_slot, 'question', '')}".lower()
        candidate = candidate.strip()
        if "category" in prompt:
            if ":" not in candidate or not re.search(r"\d", candidate):
                return True
            label = candidate.split(":", 1)[0].strip().lower()
            if label in TextReader._MONTHS or candidate.endswith(","):
                return True
        if any(term in prompt for term in ("date", "when")):
            has_date = re.search(
                r"\b(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+\d{1,2},?)?\s+\d{4}\b|\b\d{4}[-/]\d{2}[-/]\d{2}\b|\b\d{4}\b",
                candidate,
            )
            if not has_date:
                return True
        if any(term in prompt for term in ("count", "how many", "number")) and not re.search(r"\d", candidate):
            return True
        return False

    def _collection_readiness_gate(self, artifact_id: str, mode: str) -> EvidencePack | None:
        ready = self._ready_collection_artifacts.get(artifact_id)
        if not ready:
            return None
        self._record_ready_collection_guard(artifact_id)
        allowed_next = ready.get("allowed_next") or ["write_file"]
        evidence = list(self._ready_collection_evidence.get(artifact_id, [])) if self._required_outputs_pending(artifact_id) else []
        instruction = ready.get(
            "instruction",
            "Use the existing ready collection digest to write the deliverable; do not reread resolved sources.",
        )
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary="collection evidence already ready; repeated collect/refine/verify suppressed",
            evidence=evidence,
            unresolved=[],
            next_action={
                "allowed_next": allowed_next,
                "instruction": instruction,
                "guard": "ready_collection_artifact",
            },
        )

    @staticmethod
    def _native_escape_pack(artifact_id: str, mode: str) -> EvidencePack:
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary="SRO one-shot escape active; collection source reads are now native/pass-through",
            evidence=[],
            unresolved=[],
            next_action={
                "allowed_next": ["native read selected source files", "write_file"],
                "instruction": (
                    "SRO already produced ready collection evidence and the agent requested more reads. "
                    "Use native reads only for targeted verification, or write the deliverable."
                ),
                "guard": "native_escape",
            },
        )

    @staticmethod
    def _native_collection_pack(artifact_id: str, mode: str, decision: BenefitDecision) -> EvidencePack:
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary=f"low-sparse fallback: {decision.reason}",
            evidence=[],
            next_action={
                "allowed_next": ["native read listed files", "run short analysis script", "write required deliverables"],
                "instruction": "Native path is cheaper than SRO negotiation for this bundle. Do not continue SRO unless a later source is genuinely large.",
                "recommended_mode": decision.recommended_mode,
            },
        )

    def _resolve_target(self, target: Any, hint: HintSpec) -> tuple[str, FileInfo | None, str]:
        if not isinstance(target, dict):
            return "", None, "target must be an object with path or artifact_id"
        artifact_id = str(target.get("artifact_id") or hint.artifact or "").strip()
        if artifact_id:
            info = self._artifacts.get(artifact_id)
            if info:
                return artifact_id, info, ""
            return artifact_id, None, f"unknown artifact_id {artifact_id!r}; call sro_card or scout with path first"
        path = str(target.get("path") or "").strip()
        if not path:
            return "", None, "target.path is required for first read"
        info = self.inspect(path)
        artifact_id = self._artifact_for(info)
        return artifact_id, info, ""

    @staticmethod
    def _target_artifact_id(target: Any, hint: HintSpec) -> str:
        if isinstance(target, dict) and target.get("artifact_id"):
            return str(target.get("artifact_id"))
        return hint.artifact

    def _artifact_for(self, info: FileInfo) -> str:
        key = str(info.path)
        existing = self._path_to_artifact.get(key)
        if existing:
            return existing
        digest = hashlib.sha1(f"{key}:{info.size_bytes}".encode("utf-8")).hexdigest()[:12]
        artifact_id = f"sro_{digest}"
        self._path_to_artifact[key] = artifact_id
        self._artifacts[artifact_id] = info
        return artifact_id

    def _remember_collection_children(self, root: Path, artifact_id: str, pack: EvidencePack) -> None:
        ready = (
            bool(pack.slot_digest and pack.slot_digest.get("overall_status") == "ready")
            or bool(pack.next_action and pack.next_action.get("overall_status") == "ready")
        )
        if ready:
            next_action = pack.next_action or {}
            self._ready_collection_artifacts[artifact_id] = {
                "allowed_next": next_action.get("allowed_next") or ["write_file"],
                "instruction": next_action.get(
                    "instruction",
                    "Use the existing ready collection digest to write the deliverable; do not reread resolved sources.",
                ),
            }
            self._ready_collection_evidence[artifact_id] = list(pack.evidence)
            self._ready_collection_guard_counts.setdefault(artifact_id, 0)
            required_outputs = next_action.get("required_outputs") or []
            if isinstance(required_outputs, list):
                names = {str(name).strip() for name in required_outputs if str(name).strip()}
                if names:
                    self._required_outputs_by_artifact[artifact_id] = names
                    self._written_outputs_by_artifact.setdefault(artifact_id, set())
        covered_sources = []
        if pack.next_action:
            raw_sources = pack.next_action.get("covered_sources") or []
            if isinstance(raw_sources, list):
                covered_sources = [str(source) for source in raw_sources if str(source).strip()]
        for source in covered_sources:
            child = (root / source).resolve(strict=False)
            try:
                if child.is_file():
                    self._collection_child_guards[str(child)] = artifact_id
                    if ready:
                        self._ready_collection_child_guards[str(child)] = artifact_id
            except OSError:
                continue
        for block in pack.evidence:
            child = (root / block.anchor).resolve(strict=False)
            try:
                if child.is_file():
                    self._collection_child_guards[str(child)] = artifact_id
                    if ready:
                        self._ready_collection_child_guards[str(child)] = artifact_id
            except OSError:
                continue

    def is_ready_collection_child(self, path: str | Path) -> bool:
        artifact_id = self._ready_collection_child_artifact(path)
        if not artifact_id:
            return False
        self._record_ready_collection_guard(artifact_id)
        return True

    def _collection_child_guard(self, path: str | Path) -> str:
        try:
            key = str(Path(path).resolve(strict=False))
            if key not in self._collection_child_guards and self.workspace and not Path(path).is_absolute():
                key = str((self.workspace / path).resolve(strict=False))
        except Exception:
            key = str(path)
        artifact_id = self._collection_child_guards.get(key)
        if not artifact_id:
            return ""
        if key in self._ready_collection_child_guards:
            self._record_ready_collection_guard(artifact_id)
        ready = self._ready_collection_artifacts.get(artifact_id, {})
        required = self._required_outputs_by_artifact.get(artifact_id, set())
        written = self._written_outputs_by_artifact.get(artifact_id, set())
        missing = sorted(required - written)
        instruction = ready.get(
            "instruction",
            "Use the existing collection digest or slot_digest to write the required deliverable. Do not verify resolved source facts.",
        )
        allowed = ["write_file"]
        payload = {
            "sro_guard": True,
            "message": (
                "This source file is already covered by the collection excerpt digest. "
                "Treat the digest as usable evidence, not as a preliminary summary. "
                "Do not broad-read this source again. Use sro_read focus on specific source "
                "files if you need to verify individual facts before writing."
            ),
            "covered_by_artifact": artifact_id,
            "evidence_complete_for_source": True,
            "allowed_next": allowed,
            "required_outputs_missing": missing,
            "next_action": {
                "tool": "write_file",
                "instruction": instruction,
            },
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _ready_collection_child_artifact(self, path: str | Path) -> str:
        try:
            key = str(Path(path).resolve(strict=False))
            if key not in self._ready_collection_child_guards and self.workspace and not Path(path).is_absolute():
                key = str((self.workspace / path).resolve(strict=False))
        except Exception:
            key = str(path)
        return self._ready_collection_child_guards.get(key, "")

    def _record_ready_collection_guard(self, artifact_id: str) -> None:
        self._ready_collection_guard_counts[artifact_id] = self._collection_ready_guard_count(artifact_id) + 1

    def _collection_ready_guard_count(self, artifact_id: str) -> int:
        return self._ready_collection_guard_counts.get(artifact_id, 0)

    def _is_native_escape_collection(self, artifact_id: str) -> bool:
        return artifact_id in self._native_escape_collection_artifacts

    def _mark_native_escape_collection(self, artifact_id: str) -> None:
        self._native_escape_collection_artifacts.add(artifact_id)

    def _required_outputs_pending(self, artifact_id: str) -> bool:
        required = self._required_outputs_by_artifact.get(artifact_id)
        if not required:
            return False
        written = self._written_outputs_by_artifact.get(artifact_id, set())
        return bool(required - written)

    def record_output_write(self, path: str | Path) -> str:
        try:
            name = Path(path).resolve(strict=False).name
        except Exception:
            name = Path(path).name
        if not name:
            return ""
        reminders: list[str] = []
        for artifact_id, required in self._required_outputs_by_artifact.items():
            if name not in required:
                continue
            written = self._written_outputs_by_artifact.setdefault(artifact_id, set())
            written.add(name)
            missing = sorted(required - written)
            if missing:
                reminders.append(
                    "SRO required-output reminder: still write "
                    + ", ".join(missing)
                    + " before finishing."
                )
            else:
                reminders.append("SRO required-output checklist complete.")
        return "\n".join(reminders)

    def _parent_collection_artifact(self, path: str | Path) -> str:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            resolved = Path(path)
        for artifact_id, info in self._artifacts.items():
            if info.type != "collection":
                continue
            if self._is_native_escape_collection(artifact_id):
                continue
            try:
                root = info.path.resolve(strict=False)
            except Exception:
                root = info.path
            if root != resolved and root in resolved.parents:
                children = self._collection_artifact_children.get(artifact_id)
                if children is not None and str(resolved) not in children:
                    continue
                if self.benefit_gate.decide(info).action != "intercept":
                    continue
                return artifact_id
        return ""

    def _remember_collection_artifact_children(self, root: Path, artifact_id: str) -> None:
        children: set[str] = set()
        try:
            for item in self.collection_reader.card_details(root, limit=10_000).get("files", []):
                name = str(item.get("name") or "")
                if name:
                    children.add(str((root / name).resolve(strict=False)))
        except Exception:
            children = set()
        self._collection_artifact_children[artifact_id] = children

    def _is_native_collection_child(self, path: str | Path) -> bool:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            return False
        return any(root == str(resolved) or Path(root) in resolved.parents for root in self._native_collection_roots)

    def _remember_native_collection_root(self, info: FileInfo) -> None:
        if info.type != "collection":
            return
        try:
            self._native_collection_roots.add(str(info.path.resolve(strict=False)))
        except Exception:
            self._native_collection_roots.add(str(info.path))

    def _outside_workspace(self, path: str | Path) -> bool:
        if self.workspace is None:
            return False
        try:
            resolved = Path(path).resolve(strict=False)
            workspace = self.workspace.resolve(strict=False)
        except Exception:
            return False
        return resolved != workspace and workspace not in resolved.parents

    def _is_child_of_nonforce_benefit_bundle(
        self,
        path: str | Path,
        *,
        child_decision: BenefitDecision | None = None,
    ) -> bool:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            return False
        if child_decision and child_decision.action == "intercept":
            return False
        candidates = [resolved.parent, *list(resolved.parents)[:3]]
        if self.workspace:
            try:
                workspace = self.workspace.resolve(strict=False)
                candidates = [parent for parent in candidates if parent == workspace or workspace in parent.parents]
            except Exception:
                pass
        for parent in candidates:
            if not parent.exists() or not parent.is_dir():
                continue
            try:
                if not self.collection_reader._items(parent):
                    continue
                decision = self.benefit_gate.decide(self.inspect(parent))
                if decision.mode == "native":
                    self._native_collection_roots.add(str(parent.resolve(strict=False)))
                    return True
            except Exception:
                continue
        return False

    def _is_weak_lazy_child(self, path: str | Path, info: FileInfo, decision: BenefitDecision) -> bool:
        if decision.action != "intercept":
            return False
        parent_decision = self._nearest_collection_decision(path)
        if parent_decision is None or parent_decision.action == "intercept":
            return False
        suffix = info.path.suffix.lower()
        if info.type == "pdf":
            return False
        if suffix == ".log" and info.size_bytes < self._lazy_text_threshold():
            return True
        if info.type in self._TEXT and info.size_bytes < self._lazy_text_threshold():
            return True
        return False

    def _nearest_collection_decision(self, path: str | Path) -> BenefitDecision | None:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            return None
        candidates = [resolved.parent, *list(resolved.parents)[:3]]
        if self.workspace:
            try:
                workspace = self.workspace.resolve(strict=False)
                candidates = [parent for parent in candidates if parent == workspace or workspace in parent.parents]
            except Exception:
                pass
        for parent in candidates:
            if not parent.exists() or not parent.is_dir():
                continue
            try:
                info = self.inspect(parent)
                if info.type == "collection" and self.collection_reader._items(parent):
                    return self.benefit_gate.decide(info)
            except Exception:
                continue
        return None

    def _nearest_force_collection_root(self, path: str | Path) -> Path | None:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            return None
        candidates = [resolved.parent, *list(resolved.parents)[:3]]
        if self.workspace:
            try:
                workspace = self.workspace.resolve(strict=False)
                candidates = [parent for parent in candidates if parent == workspace or workspace in parent.parents]
            except Exception:
                pass
        for parent in candidates:
            if not parent.exists() or not parent.is_dir():
                continue
            try:
                info = self.inspect(parent)
                if info.type != "collection" or not self.collection_reader._items(parent):
                    continue
                artifact_id = self._path_to_artifact.get(str(info.path))
                if artifact_id and self._is_native_escape_collection(artifact_id):
                    continue
                if self.benefit_gate.decide(info).action == "intercept":
                    return parent
            except Exception:
                continue
        return None

    @staticmethod
    def _lazy_text_threshold() -> int:
        try:
            return max(4096, int(os.environ.get("SRO_LAZY_TEXT_BYTES", "12288")))
        except ValueError:
            return 12_288

    @staticmethod
    def _budget(mode: str) -> int:
        defaults = {
            "scout": 1800,
            "focus": 3500,
            "refine": 3500,
            "verify": 1200,
            "collect": 2800,
        }
        key = f"SRO_{mode.upper()}_BUDGET_CHARS"
        try:
            return max(200, int(os.environ.get(key, defaults.get(mode, 2500))))
        except ValueError:
            return defaults.get(mode, 2500)

    @staticmethod
    def _collection_budget(mode: str) -> int:
        defaults = {
            "scout": 5000,
            "focus": 7000,
            "refine": 20000,
            "verify": 20000,
            "collect": 16000,
        }
        key = f"SRO_COLLECTION_{mode.upper()}_BUDGET_CHARS"
        try:
            return max(1000, int(os.environ.get(key, defaults.get(mode, 7000))))
        except ValueError:
            return defaults.get(mode, 7000)
