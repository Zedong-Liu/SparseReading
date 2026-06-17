"""Shared JSONL bridge server for SparseRead framework adapters."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable

from nanobot.sparse_reading.benefit_gate import BenefitDecision
from nanobot.sparse_reading.detector import FileInfo, inspect_file
from sparseread import SparseRead
from sparseread.config import SparseReadConfig


GateClassifier = Callable[[FileInfo, BenefitDecision], dict[str, Any]]

READ_LIKE_TOOLS = {"read", "read_file", "list", "list_dir", "dir_list", "grep", "exec", "bash", "shell"}
WRITE_LIKE_TOOLS = {"write", "write_file", "edit", "apply_patch"}


@dataclass(slots=True)
class BridgePolicy:
    platform: str
    gate_key: str
    ready_guard: str
    allow_bounded_text_verify: bool = False
    guard_cards_after_ready: bool = True


def native_passthrough_gate(reason: str, *, include_search: bool = False) -> dict[str, Any]:
    gate = {
        "mode": "native",
        "prompt_style": "native",
        "block_native_read": False,
        "nudge_native": False,
        "trajectory": "native",
        "reason": reason,
    }
    if include_search:
        gate["block_native_search"] = False
        gate["block_native_exec_dump"] = False
    return gate


class SparseReadBridgeServer:
    """Stateful adapter around one SparseRead runtime."""

    _MAX_ADAPTER_ARTIFACTS = 512

    def __init__(
        self,
        *,
        workspace: str | Path | None,
        mode: str = "auto",
        classifier: GateClassifier,
        policy: BridgePolicy,
    ) -> None:
        self.runtime = SparseRead(SparseReadConfig(mode=mode, workspace=workspace))
        self.workspace = str(Path(workspace).resolve()) if workspace else None
        self.classifier = classifier
        self.policy = policy
        self.started_at = time.time()
        self.events: list[dict[str, Any]] = []
        self.native_events: list[dict[str, Any]] = []
        self.usage_events: list[dict[str, Any]] = []
        self.gate_events: list[dict[str, Any]] = []
        self.ready_after_native_reads = 0
        self.deliverable_written = False
        self._adapter_once_artifacts: set[str] = set()
        self._adapter_ready_artifacts: dict[str, dict[str, Any]] = {}
        self._adapter_card_results: dict[str, dict[str, Any]] = {}
        self._adapter_artifact_roots: dict[str, Path] = {}
        self._adapter_verify_passes: dict[str, int] = {}
        self._adapter_guard_hits = 0

    def preview(self, params: dict[str, Any]) -> dict[str, Any]:
        target = params.get("target")
        if target is None:
            target = {"artifact_id": params.get("artifact_id")} if params.get("artifact_id") else {"path": params.get("path")}
        pack = self.runtime.orchestrator.preview(target).to_dict()
        result = {"preview_pack": pack}
        self._record("sro_preview", {"target": target}, result)
        return result

    def raw(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_ref = self._require_str(params, "raw_ref")
        result = {
            "raw": self.runtime.orchestrator.raw(
                raw_ref,
                range=params.get("range") if isinstance(params.get("range"), dict) else None,
                selector=str(params.get("selector") or "") or None,
            )
        }
        self._record("sro_raw", {"raw_ref": raw_ref}, result)
        return result

    def card(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._require_str(params, "path")
        if self.policy.guard_cards_after_ready:
            ready_artifact = self._adapter_ready_artifact_for_path(path)
            if ready_artifact:
                result = self._adapter_already_ready_card(ready_artifact, path)
                self._record("sro_card", {"path": path}, result)
                return result
        info = inspect_file(Path(path))
        decision = self.runtime.orchestrator.benefit_gate.decide(info)
        gate = self.classifier(info, decision)
        card = self.runtime.orchestrator.card(Path(path))
        if self._native_passthrough_path(Path(path)):
            gate = native_passthrough_gate(
                f"{self.policy.platform} native pass-through: generated/runtime artifacts should not re-enter SparseRead",
                include_search=self.policy.gate_key == "openclaw_gate",
            )
            card.sparse_recommended = False
            card.recommended_mode = "native"
            card.reason = gate["reason"]
        else:
            parent_gate = self._force_collection_parent_gate(Path(path))
            if parent_gate:
                parent_path = Path(str(parent_gate["handoff_path"]))
                card = self.runtime.orchestrator.card(parent_path)
                gate = parent_gate
        if gate.get("mode") == "native":
            card.sparse_recommended = False
            card.recommended_mode = "native"
            card.reason = str(gate.get("reason") or f"{self.policy.platform} native path is cheaper than SparseRead")
        result: dict[str, Any] = {"file_card": card.to_dict(), self.policy.gate_key: gate}
        if card.sparse_recommended:
            mode = "collect" if "collect" in card.recommended_mode else card.recommended_mode
            result["next_action"] = {
                "tool": "sro_read",
                "target": {"artifact_id": card.artifact_id},
                "mode": mode,
                "hint": {
                    "goal": "state the evidence needed from this artifact",
                    "type_hint": "text" if card.type == "txt" else card.type,
                },
            }
        if gate.get("trajectory") == "one_collect_then_write":
            result["protocol_note"] = (
                f"{self.policy.platform} trajectory: call exactly one sro_read(mode=collect) "
                "with explicit slots, then write requested deliverables when ready. Do not repeat "
                "sro_read after ready; use native reads only for small templates or named unresolved slots."
            )
        self._remember_adapter_card(
            card.artifact_id,
            result,
            Path(card.path),
            once=info.type == "collection" and gate.get("trajectory") in {"one_collect_then_write", "sro_first"},
        )
        self._record("sro_card", {"path": path}, result)
        self._record_gate(path, info, decision, gate)
        return result

    def read(self, params: dict[str, Any]) -> dict[str, Any]:
        target = params.get("target")
        mode = params.get("mode")
        hint = params.get("hint") or {}
        if isinstance(target, str) and target:
            target = {"artifact_id": target}
        if isinstance(mode, str):
            mode = {"full": "collect", "scan": "scout"}.get(mode, mode)
        if not isinstance(target, dict):
            raise ValueError("target must be an object with path or artifact_id")
        if not isinstance(mode, str):
            raise ValueError("mode must be a string")
        if not isinstance(hint, dict):
            raise ValueError("hint must be an object")
        ready_artifact = self._adapter_ready_artifact_for_target(target, hint)
        if ready_artifact and self._allow_bounded_ready_verify(ready_artifact, mode):
            ready_artifact = ""
        if ready_artifact:
            result = {"evidence_pack": self._adapter_already_ready_pack(ready_artifact, mode)}
            self._record("sro_read", {"target": target, "mode": mode, "hint": hint}, result)
            return result
        pack = self.runtime.orchestrator.read(target, mode, hint)
        packed = self._adapter_pack(pack.to_dict())
        self._remember_adapter_ready_pack(packed)
        result = {"evidence_pack": packed}
        self._record("sro_read", {"target": target, "mode": mode, "hint": hint}, result)
        return result

    def decide(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._require_str(params, "path")
        info = inspect_file(Path(path))
        decision = self.runtime.orchestrator.benefit_gate.decide(info)
        gate = self.classifier(info, decision)
        if self._native_passthrough_path(Path(path)):
            gate = native_passthrough_gate(
                f"{self.policy.platform} native pass-through: generated/runtime artifacts should not re-enter SparseRead",
                include_search=self.policy.gate_key == "openclaw_gate",
            )
        else:
            parent_gate = self._force_collection_parent_gate(Path(path))
            if parent_gate:
                gate = parent_gate
        result = {
            "path": str(info.path),
            "type": info.type,
            "supported": info.supported,
            "large": info.large,
            "structured": info.structured,
            "size_bytes": info.size_bytes,
            "decision": {
                "mode": decision.mode,
                "action": decision.action,
                "reason": decision.reason,
                "confidence": decision.confidence,
                "recommended_mode": decision.recommended_mode,
            },
            self.policy.gate_key: gate,
            "should_handoff_read": self.runtime.orchestrator.should_handoff_read(Path(path)),
        }
        self._record("sro_decide", {"path": path}, result)
        self._record_gate(path, info, decision, gate)
        return result

    def native_event(self, params: dict[str, Any]) -> dict[str, Any]:
        event = {
            "time": round(time.time(), 3),
            "phase": str(params.get("phase") or "unknown"),
            "tool": str(params.get("tool") or "unknown"),
            "params": self._jsonable(params.get("params") or {}),
            "truncated": bool(params.get("truncated", False)),
            "output_chars": int(params.get("output_chars") or params.get("outputChars") or 0),
            "reason": str(params.get("reason") or ""),
        }
        self.native_events.append(event)
        if event["phase"] == "after" and event["tool"] in WRITE_LIKE_TOOLS:
            self.deliverable_written = True
        if event["phase"] == "after" and event["tool"] in READ_LIKE_TOOLS and self._has_ready_evidence():
            self.ready_after_native_reads += 1
        return {"ok": True, "native_event_count": len(self.native_events)}

    def usage_event(self, params: dict[str, Any]) -> dict[str, Any]:
        event = {
            "time": round(time.time(), 3),
            "provider": params.get("provider"),
            "model": params.get("model"),
            "usage": self._jsonable(params.get("usage") or {}),
            "request_id": params.get("request_id") or params.get("requestId"),
        }
        self.usage_events.append(event)
        return {"ok": True, "usage_event_count": len(self.usage_events)}

    def trace(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        orchestrator = self.runtime.orchestrator
        artifacts: list[dict[str, Any]] = []
        for artifact_id, info in getattr(orchestrator, "_artifacts", {}).items():
            artifacts.append(
                {
                    "artifact_id": artifact_id,
                    "path": str(info.path),
                    "type": info.type,
                    "supported": info.supported,
                    "large": info.large,
                    "structured": info.structured,
                    "size_bytes": info.size_bytes,
                }
            )
        return {
            "workspace": self.workspace,
            "uptime_seconds": round(time.time() - self.started_at, 3),
            "artifacts": artifacts,
            "macro_requested": bool(getattr(orchestrator, "macro_requested", False)),
            "ready_collection_artifacts": sorted(getattr(orchestrator, "_ready_collection_artifacts", {}).keys()),
            "slot_digest_artifacts": sorted(getattr(orchestrator, "_slot_digests", {}).keys()),
            "adapter_ready_artifacts": sorted(self._adapter_ready_artifacts),
            "adapter_verify_passes": dict(sorted(self._adapter_verify_passes.items())),
            "adapter_guard_hits": self._adapter_guard_hits,
            "events": self.events,
            "native_events": self.native_events,
            "usage_events": self.usage_events,
            "gate_events": self.gate_events,
            "summary": self._trace_summary(),
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if method == "preview":
            return self.preview(params)
        if method == "raw":
            return self.raw(params)
        if method == "card":
            return self.card(params)
        if method == "read":
            return self.read(params)
        if method == "decide":
            return self.decide(params)
        if method == "native_event":
            return self.native_event(params)
        if method == "usage_event":
            return self.usage_event(params)
        if method == "trace":
            return self.trace(params)
        if method == "shutdown":
            return {"ok": True}
        raise ValueError(f"unknown method: {method}")

    def _adapter_pack(self, pack: dict[str, Any]) -> dict[str, Any]:
        slot_digest = pack.get("slot_digest")
        if not isinstance(slot_digest, dict) or slot_digest.get("overall_status") != "ready":
            return pack
        for slot in slot_digest.get("slots") or []:
            if isinstance(slot, dict):
                slot.pop("verify_ref", None)
        slot_digest["adapter_guard"] = "ready_for_write: do not call sro_read verify/refine for resolved slots"
        pack["protocol_next"] = "write_file_now"
        return pack

    def _remember_adapter_ready_pack(self, pack: dict[str, Any]) -> None:
        artifact_id = str(pack.get("artifact_id") or "")
        if not artifact_id:
            return
        next_action = pack.get("next_action") if isinstance(pack.get("next_action"), dict) else {}
        slot_digest = pack.get("slot_digest") if isinstance(pack.get("slot_digest"), dict) else {}
        ready = slot_digest.get("overall_status") == "ready" or next_action.get("overall_status") == "ready"
        if not ready:
            return
        pack_type = str(pack.get("type") or "")
        if pack_type == "collection" and artifact_id not in self._adapter_once_artifacts:
            return
        if pack_type not in {"collection", "pdf", "text", "txt", "md", "markdown", "rst"}:
            return
        self._adapter_ready_artifacts[artifact_id] = {
            "summary": pack.get("summary") or "evidence ready",
            "type": pack_type,
            "next_action": copy.deepcopy(next_action),
            "slot_digest": self._compact_slot_digest(slot_digest),
            "evidence_anchors": [
                str(block.get("anchor"))
                for block in pack.get("evidence") or []
                if isinstance(block, dict) and block.get("anchor")
            ],
        }
        self._prune_adapter_artifacts()

    def _remember_adapter_card(self, artifact_id: str, result: dict[str, Any], path: Path, *, once: bool) -> None:
        self._adapter_card_results.pop(artifact_id, None)
        self._adapter_artifact_roots.pop(artifact_id, None)
        self._adapter_card_results[artifact_id] = copy.deepcopy(result)
        self._adapter_artifact_roots[artifact_id] = path.resolve(strict=False)
        if once:
            self._adapter_once_artifacts.add(artifact_id)
        self._prune_adapter_artifacts()

    def _prune_adapter_artifacts(self) -> None:
        while len(self._adapter_artifact_roots) > self._MAX_ADAPTER_ARTIFACTS:
            artifact_id = next(iter(self._adapter_artifact_roots))
            self._adapter_artifact_roots.pop(artifact_id, None)
            self._adapter_card_results.pop(artifact_id, None)
            self._adapter_ready_artifacts.pop(artifact_id, None)
            self._adapter_verify_passes.pop(artifact_id, None)
            self._adapter_once_artifacts.discard(artifact_id)

    def _adapter_ready_artifact_for_target(self, target: dict[str, Any], hint: dict[str, Any]) -> str:
        artifact_id = str(target.get("artifact_id") or hint.get("artifact") or "").strip()
        if artifact_id in self._adapter_ready_artifacts:
            return artifact_id
        path = str(target.get("path") or "").strip()
        return self._adapter_ready_artifact_for_path(path) if path else ""

    def _adapter_ready_artifact_for_path(self, path: str | Path) -> str:
        if not path:
            return ""
        try:
            candidate = Path(path)
            if self.workspace and not candidate.is_absolute():
                candidate = Path(self.workspace) / candidate
            candidate = candidate.resolve(strict=False)
        except Exception:
            return ""
        for artifact_id in self._adapter_ready_artifacts:
            root = self._adapter_artifact_roots.get(artifact_id)
            if root and self._is_same_or_descendant(candidate, root):
                return artifact_id
        return ""

    def _allow_bounded_ready_verify(self, artifact_id: str, mode: str) -> bool:
        if not self.policy.allow_bounded_text_verify:
            return False
        ready = self._adapter_ready_artifacts.get(artifact_id) or {}
        if str(ready.get("type") or "") not in {"pdf", "text", "txt", "md", "markdown", "rst"}:
            return False
        if mode not in {"verify", "focus"}:
            return False
        used = self._adapter_verify_passes.get(artifact_id, 0)
        if used >= 1:
            return False
        self._adapter_verify_passes[artifact_id] = used + 1
        return True

    def _adapter_already_ready_card(self, artifact_id: str, requested_path: str) -> dict[str, Any]:
        self._adapter_guard_hits += 1
        base = copy.deepcopy(self._adapter_card_results.get(artifact_id) or {})
        ready = self._adapter_ready_artifacts.get(artifact_id, {})
        if not base:
            base = {
                "file_card": {
                    "artifact_id": artifact_id,
                    "path": str(self._adapter_artifact_roots.get(artifact_id) or requested_path),
                    "type": ready.get("type") or "collection",
                    "sparse_recommended": True,
                    "recommended_mode": "collect",
                    "reason": "adapter closure already ready",
                },
                self.policy.gate_key: {
                    "mode": "advisory",
                    "trajectory": "one_collect_then_write",
                    "reason": "adapter closure already ready",
                },
            }
        base["adapter_guard"] = "closure_once_already_ready"
        base["already_ready_closure"] = {
            "artifact_id": artifact_id,
            "requested_path": requested_path,
            "evidence_anchors": ready.get("evidence_anchors") or [],
            "instruction": self._ready_instruction(artifact_id),
        }
        base["next_action"] = {
            "tool": "write_file",
            "allowed_next": ["write_file"],
            "instruction": self._ready_instruction(artifact_id),
        }
        base["protocol_next"] = "write_file_now"
        return base

    def _adapter_already_ready_pack(self, artifact_id: str, mode: str) -> dict[str, Any]:
        self._adapter_guard_hits += 1
        ready = self._adapter_ready_artifacts.get(artifact_id, {})
        next_action = copy.deepcopy(ready.get("next_action") or {})
        required_outputs = next_action.get("required_outputs") if isinstance(next_action, dict) else []
        pack_next_action = {
            "allowed_next": ["write_file"],
            "instruction": self._ready_instruction(artifact_id),
            "guard": self.policy.ready_guard,
            "prior_evidence_artifact": artifact_id,
        }
        if required_outputs:
            pack_next_action["required_outputs"] = required_outputs
        return {
            "artifact_id": artifact_id,
            "mode": mode,
            "type": ready.get("type") or "text",
            "summary": "adapter ready guard: evidence is already ready from the prior read; write the deliverable now",
            "skeleton": [],
            "evidence": [],
            "unresolved": [],
            "slot_digest": ready.get("slot_digest") or None,
            "next_action": pack_next_action,
            "next_hint": None,
            "error": "",
            "protocol_next": "write_file_now",
        }

    def _ready_instruction(self, artifact_id: str) -> str:
        ready = self._adapter_ready_artifacts.get(artifact_id, {})
        next_action = ready.get("next_action") if isinstance(ready.get("next_action"), dict) else {}
        instruction = str(next_action.get("instruction") or "").strip()
        if instruction:
            return instruction
        return "Use the existing ready evidence from the prior collect and write the requested deliverable now."

    @staticmethod
    def _compact_slot_digest(slot_digest: dict[str, Any]) -> dict[str, Any]:
        if not slot_digest:
            return {}
        compact = {
            "overall_status": slot_digest.get("overall_status"),
            "adapter_guard": "ready_for_write: do not call sro_read verify/refine for resolved slots",
            "resolved_slot_count": len(
                [
                    slot
                    for slot in slot_digest.get("slots") or []
                    if isinstance(slot, dict) and slot.get("status") == "resolved"
                ]
            ),
        }
        unresolved = slot_digest.get("unresolved_slots")
        if unresolved:
            compact["unresolved_slots"] = unresolved
        return compact

    @staticmethod
    def _is_same_or_descendant(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    def _native_passthrough_path(self, path: Path) -> bool:
        orchestrator = self.runtime.orchestrator
        return (
            orchestrator.is_calc_artifact(path)
            or orchestrator.is_output_artifact(path)
            or orchestrator.is_skill_artifact(path)
            or orchestrator.is_runtime_artifact(path)
        )

    def _force_collection_parent_gate(self, path: Path) -> dict[str, Any] | None:
        child_info = inspect_file(path)
        if child_info.type == "collection":
            return None
        child_decision = self.runtime.orchestrator.benefit_gate.decide(child_info)
        if child_decision.mode == "force_sro":
            return None
        nearest = getattr(self.runtime.orchestrator, "_nearest_force_collection_root")(path)
        if nearest is None:
            return None
        try:
            if nearest.resolve(strict=False) == path.resolve(strict=False):
                return None
        except Exception:
            if str(nearest) == str(path):
                return None
        parent_info = inspect_file(nearest)
        parent_decision = self.runtime.orchestrator.benefit_gate.decide(parent_info)
        gate = self.classifier(parent_info, parent_decision)
        if gate.get("block_native_read") is not True:
            return None
        gate = copy.deepcopy(gate)
        gate["handoff_path"] = str(nearest)
        gate["reason"] = (
            f"child source belongs to force-SRO collection {nearest}; "
            "bind to the parent collection once instead of reading child files natively"
        )
        return gate

    def _record(self, kind: str, params: dict[str, Any], result: dict[str, Any]) -> None:
        self.events.append(
            {
                "time": round(time.time(), 3),
                "kind": kind,
                "params": self._jsonable(params),
                "summary": self._summarize_result(kind, result),
            }
        )

    def _record_gate(self, path: str, info: FileInfo, decision: BenefitDecision, gate: dict[str, Any]) -> None:
        self.gate_events.append(
            {
                "time": round(time.time(), 3),
                "path": str(path),
                "type": info.type,
                "decision_mode": decision.mode,
                "decision_reason": decision.reason,
                "adapter_mode": gate.get("mode"),
                "adapter_reason": gate.get("reason"),
                "trajectory": gate.get("trajectory"),
            }
        )

    def _has_ready_evidence(self) -> bool:
        orchestrator = self.runtime.orchestrator
        return bool(
            getattr(orchestrator, "_ready_collection_artifacts", {})
            or getattr(orchestrator, "_slot_digests", {})
            or self._adapter_ready_artifacts
        )

    def _trace_summary(self) -> dict[str, Any]:
        sro_preview_calls = sum(1 for event in self.events if event["kind"] == "sro_preview")
        sro_raw_calls = sum(1 for event in self.events if event["kind"] == "sro_raw")
        sro_card_calls = sum(1 for event in self.events if event["kind"] == "sro_card")
        sro_read_calls = sum(1 for event in self.events if event["kind"] == "sro_read")
        native_truncations = sum(1 for event in self.native_events if event.get("truncated"))
        total_tokens = 0
        for event in self.usage_events:
            usage = event.get("usage") or {}
            total_tokens += int(
                usage.get("total_tokens")
                or usage.get("totalTokens")
                or usage.get("total")
                or (
                    int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("input") or 0)
                    + int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("output") or 0)
                )
            )
        return {
            "tokens": total_tokens,
            "requests": len(self.usage_events),
            "tool_calls": len([event for event in self.native_events if event.get("phase") == "after"])
            + sro_preview_calls
            + sro_raw_calls
            + sro_card_calls
            + sro_read_calls,
            "native_truncations": native_truncations,
            "sro_preview_calls": sro_preview_calls,
            "sro_raw_calls": sro_raw_calls,
            "sro_card_calls": sro_card_calls,
            "sro_read_calls": sro_read_calls,
            "ready_after_native_reads": self.ready_after_native_reads,
            "deliverable_written": self.deliverable_written,
            "gate_modes": sorted({str(event.get("adapter_mode")) for event in self.gate_events}),
            "adapter_ready_artifacts": len(self._adapter_ready_artifacts),
            "adapter_guard_hits": self._adapter_guard_hits,
        }

    @staticmethod
    def _require_str(params: dict[str, Any], key: str) -> str:
        value = params.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be a non-empty string")
        return value

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if is_dataclass(value):
            return cls._jsonable(asdict(value))
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(k): cls._jsonable(v) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [cls._jsonable(item) for item in value]
        return value

    @staticmethod
    def _summarize_result(kind: str, result: dict[str, Any]) -> dict[str, Any]:
        if kind == "sro_preview":
            pack = result.get("preview_pack", {})
            return {
                "artifact_id": pack.get("artifact_id"),
                "type": (pack.get("card") or {}).get("type"),
                "recipe": (pack.get("compression") or {}).get("recipe"),
                "raw_ref": pack.get("raw_ref"),
            }
        if kind == "sro_raw":
            raw = result.get("raw", {})
            return {"raw_ref": raw.get("raw_ref"), "error": raw.get("error")}
        if kind == "sro_card":
            card = result.get("file_card", {})
            return {
                "artifact_id": card.get("artifact_id"),
                "sparse_recommended": card.get("sparse_recommended"),
                "recommended_mode": card.get("recommended_mode"),
                "reason": card.get("reason"),
            }
        if kind == "sro_read":
            pack = result.get("evidence_pack", {})
            slot_digest = pack.get("slot_digest") or {}
            return {
                "artifact_id": pack.get("artifact_id"),
                "mode": pack.get("mode"),
                "type": pack.get("type"),
                "evidence_blocks": len(pack.get("evidence", []) or []),
                "unresolved": pack.get("unresolved") or pack.get("unresolved_slots"),
                "overall_status": slot_digest.get("overall_status") or pack.get("overall_status"),
                "allowed_next": pack.get("allowed_next"),
            }
        if kind == "sro_decide":
            decision = result.get("decision", {})
            return {
                "mode": decision.get("mode"),
                "action": decision.get("action"),
                "reason": decision.get("reason"),
                "should_handoff_read": result.get("should_handoff_read"),
            }
        return {}


def write_response(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve_bridge(args: argparse.Namespace, bridge_factory: Callable[[str | Path | None, str], SparseReadBridgeServer]) -> int:
    bridge = bridge_factory(args.workspace, args.mode)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            request_id = request.get("id")
            result = bridge.handle(request)
            write_response({"id": request_id, "ok": True, "result": result})
            if request.get("method") == "shutdown":
                return 0
        except Exception as exc:
            write_response({"id": request_id, "ok": False, "error": str(exc)})
    return 0
