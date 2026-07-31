"""Shared JSONL bridge server for SparseRead framework adapters."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable

from nanobot.sparse_reading.benefit_gate import BenefitDecision
from nanobot.sparse_reading.detector import FileInfo, inspect_file
from sparseread import SparseRead
from sparseread.config import SparseReadConfig
from sparseread.token_tracker import TokenTracker, DEFAULT_CONTEXT_WINDOW


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


def _resolve_file_size(path: str) -> int:
    """Return the size of a file in bytes, or 0 if the file doesn't exist."""
    try:
        return Path(path).stat().st_size
    except (OSError, TypeError, ValueError):
        return 0


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
        token_tracker: TokenTracker | None = None,
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
        self._token_tracker = token_tracker or TokenTracker(
            log_dir=Path.home() / ".claude",
            enable_log=(os.environ.get("SRO_TOKEN_LOG") or "1") != "0",
        )

    def preview(self, params: dict[str, Any]) -> dict[str, Any]:
        target = params.get("target")
        if target is None:
            target = {"artifact_id": params.get("artifact_id")} if params.get("artifact_id") else {"path": params.get("path")}
        pack = self.runtime.orchestrator.preview(target).to_dict()
        result = {"preview_pack": pack}
        card = pack.get("card") if isinstance(pack.get("card"), dict) else {}
        artifact_id = str(pack.get("artifact_id") or card.get("artifact_id") or "")
        card_path = str(card.get("path") or (target.get("path") if isinstance(target, dict) else "") or "")
        if artifact_id and card_path and not pack.get("error"):
            self._remember_adapter_card(
                artifact_id,
                {"file_card": copy.deepcopy(card)} if card else result,
                Path(card_path),
                once=str(card.get("type") or "") == "collection",
            )
        # Token tracking
        file_path = card_path or str(target.get("path") if isinstance(target, dict) else "")
        ext = Path(file_path).suffix if file_path else ""
        file_size = int(card.get("size_bytes") or 0)
        response_json = json.dumps(result, ensure_ascii=False) if result else ""
        self._token_tracker.record_preview(
            file_path=file_path, file_size_bytes=file_size, file_extension=ext,
            response_json=response_json, artifact_id=artifact_id,
        )
        self._record("sro_preview", {"target": target}, result)
        return result

    def raw(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_ref = self._require_str(params, "raw_ref")
        ready_artifact = self._adapter_ready_artifact_for_raw_ref(raw_ref)
        selector = str(params.get("selector") or "") or None
        if ready_artifact and self._adapter_ready_guard_covers_selector(ready_artifact, selector):
            result = {"raw": self._adapter_already_ready_raw(ready_artifact, raw_ref)}
            self._record("sro_raw", {"raw_ref": raw_ref}, result)
            return result
        result = {
            "raw": self.runtime.orchestrator.raw(
                raw_ref,
                range=params.get("range") if isinstance(params.get("range"), dict) else None,
                selector=selector,
            )
        }
        self._record("sro_raw", {"raw_ref": raw_ref}, result)

        # Token tracking — raw retrievals pull subset of original file
        artifact_id = ready_artifact or raw_ref.split(":", 1)[0] if ":" in raw_ref else ""
        root = self._adapter_artifact_roots.get(artifact_id)
        file_path = str(root) if root else ""
        ext = Path(file_path).suffix if file_path else ""
        file_size = _resolve_file_size(file_path)
        response_json = json.dumps(result, ensure_ascii=False) if result else ""
        self._token_tracker.record_raw(
            file_path=file_path, file_size_bytes=file_size, file_extension=ext,
            response_json=response_json, artifact_id=artifact_id,
        )

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
        # Token tracking
        response_json = json.dumps(result, ensure_ascii=False) if result else ""
        self._token_tracker.record_card(
            file_path=path, file_size_bytes=info.size_bytes, file_extension=Path(path).suffix,
            response_json=response_json,
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
        hint = self._normalize_bridge_hint(hint)
        ready_artifact = self._adapter_ready_artifact_for_target(target, hint)
        if ready_artifact and self._allow_bounded_ready_verify(ready_artifact, mode):
            ready_artifact = ""
        if ready_artifact:
            result = {"evidence_pack": self._adapter_already_ready_pack(ready_artifact, mode)}
            self._record("sro_read", {"target": target, "mode": mode, "hint": hint}, result)
            return result
        pack = self.runtime.orchestrator.read(target, mode, hint)
        packed = self._adapter_pack(pack.to_dict())
        self._remember_adapter_ready_pack(packed, hint)
        result = {"evidence_pack": packed}

        # Token tracking
        file_path = str(target.get("path") or "")
        if not file_path and target.get("artifact_id"):
            root = self._adapter_artifact_roots.get(str(target["artifact_id"]))
            file_path = str(root) if root else str(target["artifact_id"])
        ext = Path(file_path).suffix if file_path else ""
        file_size = _resolve_file_size(file_path)
        response_json = json.dumps(result, ensure_ascii=False) if result else ""
        artifact_id = str(packed.get("artifact_id") or target.get("artifact_id") or "")
        self._token_tracker.record_read(
            file_path=file_path, file_size_bytes=file_size, file_extension=ext,
            response_json=response_json, mode=str(mode), artifact_id=artifact_id,
        )

        self._record("sro_read", {"target": target, "mode": mode, "hint": hint}, result)
        return result

    def decide(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._require_str(params, "path")
        info, decision, gate = self._classified_gate_for_path(Path(path))
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

    def preflight(self, params: dict[str, Any]) -> dict[str, Any]:
        workspace = Path(str(params.get("workspace") or self.workspace or ".")).resolve(strict=False)
        max_candidates = self._bounded_int(params.get("max_candidates"), default=24, lower=1, upper=64)
        max_results = self._bounded_int(params.get("max_results"), default=3, lower=1, upper=5)
        handoffs: list[dict[str, Any]] = []
        for candidate in self._preflight_candidates(workspace, max_candidates=max_candidates):
            info, decision, gate = self._classified_gate_for_path(candidate)
            if (
                gate.get("mode") != "enforce"
                or gate.get("trajectory") != "sro_first"
                or gate.get("block_native_read") is not True
            ):
                continue
            handoff_path = Path(str(gate.get("handoff_path") or info.path)).resolve(strict=False)
            handoffs.append(
                {
                    "path": str(handoff_path),
                    "relative_path": self._relative_path(handoff_path, workspace),
                    "type": info.type,
                    "reason": str(gate.get("reason") or decision.reason),
                    "decision_mode": decision.mode,
                    "gate_mode": gate.get("mode"),
                    "trajectory": gate.get("trajectory"),
                }
            )
            if len(handoffs) >= max_results:
                break
        result: dict[str, Any] = {
            "workspace": str(workspace),
            "handoffs": handoffs,
            "handoff_count": len(handoffs),
        }
        if handoffs:
            first = handoffs[0]["relative_path"]
            result["first_action"] = {
                "tool": "sro_preview",
                "path": first,
                "instruction": (
                    f"Call sro_preview(path={first!r}) before native reads/listing/search "
                    "for this high-confidence evidence target."
                ),
            }
        self._record("sro_preflight", {"workspace": str(workspace)}, result)
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

    def usage(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return detailed token consumption metrics for the current session.

        Provides precise, SparseRead-side token metrics including:
          - Per-operation token estimates (full file vs SR response)
          - Cumulative session savings and context retention
          - Top savings by artifact
          - Gate decision summary (enforce / advisory / native split)
          - Native bypass estimate (reads that went through without SRO)
          - Token tracker state

        These are calculated from within SR — no host-platform API key needed.
        Use count_tokens_api() from token_tracker for ground-truth calibration.
        """
        tracker_state = self._token_tracker.to_dict()
        session = tracker_state["session"]

        # Gate decision summary
        gate_summary = self._gate_summary()

        # Native bypass estimate: gate events where mode=native represent
        # reads that bypassed SRO entirely. We estimate their token cost
        # based on file sizes from those gate events.
        native_bypass_estimate = self._native_bypass_estimate()

        return {
            "session": session,
            "record_count": tracker_state["record_count"],
            "uptime_seconds": tracker_state["uptime_seconds"],
            "log_path": tracker_state["log_path"],
            "context_window": tracker_state["context_window"],
            "top_savings": session.get("top_savings", [])[:10],
            "by_operation": session.get("by_operation", {}),
            "records": tracker_state.get("records", []),
            "gate_summary": gate_summary,
            "native_bypass_estimate": native_bypass_estimate,
            "interpretation": self._usage_interpretation(session),
        }

    def _gate_summary(self) -> dict[str, Any]:
        """Aggregate gate decisions (enforce / advisory / native)."""
        modes: dict[str, int] = {}
        trajectories: dict[str, int] = {}
        total_size_by_mode: dict[str, int] = {}
        for event in self.gate_events:
            mode = str(event.get("adapter_mode") or "unknown")
            modes[mode] = modes.get(mode, 0) + 1
            traj = str(event.get("trajectory") or "unknown")
            trajectories[traj] = trajectories.get(traj, 0) + 1
        return {
            "total_gate_decisions": len(self.gate_events),
            "by_mode": modes,
            "by_trajectory": trajectories,
            "enforce_pct": round(modes.get("enforce", 0) / max(1, len(self.gate_events)) * 100, 1),
            "advisory_pct": round(modes.get("advisory", 0) / max(1, len(self.gate_events)) * 100, 1),
            "native_pct": round(modes.get("native", 0) / max(1, len(self.gate_events)) * 100, 1),
        }

    def _native_bypass_estimate(self) -> dict[str, Any]:
        """Estimate tokens spent on native reads that bypassed SRO.

        Uses gate_events where mode=native or advisory (no SRO intervention).
        Each such event represents a file that was read natively.
        """
        from sparseread.token_tracker import estimate_file_tokens

        native_count = 0
        advisory_count = 0
        native_estimated_tokens = 0
        advisory_estimated_tokens = 0
        native_paths: list[str] = []

        for event in self.gate_events:
            mode = str(event.get("adapter_mode") or "")
            path = str(event.get("path") or "")
            ftype = str(event.get("type") or "")
            if mode == "native":
                native_count += 1
                # We don't have exact file sizes in gate_events, so use a
                # conservative estimate based on the path/type
                if path and native_count <= 10:
                    native_paths.append(path)
            elif mode == "advisory":
                advisory_count += 1
                advisory_estimated_tokens += 500  # rough per-file estimate

        return {
            "native_gate_count": native_count,
            "advisory_gate_count": advisory_count,
            "native_paths_sample": native_paths[:10],
            "advisory_estimated_tokens": advisory_estimated_tokens,
            "note": (
                "Native bypasses are reads the gate allowed through. "
                "To reduce these, lower CLAUDE_TEXT_ENFORCE_BYTES or "
                "adjust collection detection thresholds."
            ),
        }

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
        if method == "preflight":
            return self.preflight(params)
        if method == "native_event":
            return self.native_event(params)
        if method == "usage_event":
            return self.usage_event(params)
        if method == "usage":
            return self.usage(params)
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
        resolved_slots = self._resolved_slots(slot_digest)
        if resolved_slots["ids"]:
            slot_digest["resolved_slot_ids"] = sorted(resolved_slots["ids"])
        slot_digest["adapter_guard"] = "ready_for_write: do not call sro_read verify/refine for resolved slots"
        pack["protocol_next"] = "write_file_now"
        return pack

    def _remember_adapter_ready_pack(self, pack: dict[str, Any], hint: dict[str, Any] | None = None) -> None:
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
        resolved_slots = self._resolved_slots(slot_digest)
        requested_terms = self._requested_slot_terms(hint or {})
        existing = self._adapter_ready_artifacts.get(artifact_id) or {}
        merged_ids = set(str(item).lower() for item in existing.get("resolved_slot_ids") or []) | resolved_slots["ids"]
        merged_text = set(str(item).lower() for item in existing.get("resolved_slot_text") or []) | resolved_slots["text"] | requested_terms
        compact_digest = self._compact_slot_digest(slot_digest)
        if merged_ids:
            compact_digest["resolved_slot_ids"] = sorted(merged_ids)
            compact_digest["resolved_slot_count"] = len(merged_ids)
        self._adapter_ready_artifacts[artifact_id] = {
            "summary": pack.get("summary") or "evidence ready",
            "type": pack_type,
            "next_action": copy.deepcopy(next_action),
            "slot_digest": compact_digest,
            "resolved_slot_ids": sorted(merged_ids),
            "resolved_slot_text": sorted(merged_text),
            "evidence_anchors": [
                str(block.get("anchor"))
                for block in pack.get("evidence") or []
                if isinstance(block, dict) and block.get("anchor")
            ]
            or list(existing.get("evidence_anchors") or []),
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
        if artifact_id in self._adapter_ready_artifacts and self._adapter_ready_guard_covers_hint(artifact_id, hint):
            return artifact_id
        path = str(target.get("path") or "").strip()
        if not path:
            return ""
        path_artifact = self._adapter_ready_artifact_for_path(path)
        if path_artifact and self._adapter_ready_guard_covers_hint(path_artifact, hint):
            return path_artifact
        return ""

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

    def _adapter_ready_artifact_for_raw_ref(self, raw_ref: str) -> str:
        parts = raw_ref.split(":")
        if len(parts) < 2 or parts[0] != "raw":
            return ""
        artifact_id = parts[1]
        return artifact_id if artifact_id in self._adapter_ready_artifacts else ""

    def _adapter_ready_guard_covers_hint(self, artifact_id: str, hint: dict[str, Any]) -> bool:
        requested = self._requested_slot_terms(hint)
        if not requested:
            return True
        ready = self._adapter_ready_artifacts.get(artifact_id) or {}
        resolved_ids = {str(item).lower() for item in ready.get("resolved_slot_ids") or []}
        resolved_text = " ".join(str(item).lower() for item in ready.get("resolved_slot_text") or [])
        return all(term in resolved_ids or term in resolved_text for term in requested)

    def _adapter_ready_guard_covers_selector(self, artifact_id: str, selector: str | None) -> bool:
        if not selector:
            return True
        ready = self._adapter_ready_artifacts.get(artifact_id) or {}
        text = " ".join(str(item).lower() for item in ready.get("resolved_slot_text") or [])
        return selector.lower() in text

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

    def _adapter_already_ready_raw(self, artifact_id: str, raw_ref: str) -> dict[str, Any]:
        self._adapter_guard_hits += 1
        return {
            "raw_ref": raw_ref,
            "artifact_id": artifact_id,
            "adapter_guard": "ready_for_write: do not call sro_raw after resolved evidence",
            "summary": "adapter ready guard: evidence is already ready from the prior read; write the deliverable now",
            "matches": [],
            "truncated": False,
            "next_action": {
                "allowed_next": ["write_file"],
                "instruction": self._ready_instruction(artifact_id),
                "guard": self.policy.ready_guard,
                "prior_evidence_artifact": artifact_id,
            },
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
        resolved_slot_ids = [
            str(slot.get("id"))
            for slot in slot_digest.get("slots") or []
            if isinstance(slot, dict) and slot.get("status") == "resolved" and slot.get("id")
        ]
        compact = {
            "overall_status": slot_digest.get("overall_status"),
            "adapter_guard": "ready_for_write: do not call sro_read verify/refine for resolved slots",
            "resolved_slot_count": len(resolved_slot_ids),
            "resolved_slot_ids": resolved_slot_ids,
        }
        unresolved = slot_digest.get("unresolved_slots")
        if unresolved:
            compact["unresolved_slots"] = unresolved
        return compact

    @staticmethod
    def _resolved_slots(slot_digest: dict[str, Any]) -> dict[str, set[str]]:
        ids: set[str] = set()
        text: set[str] = set()
        for slot in slot_digest.get("slots") or []:
            if not isinstance(slot, dict) or slot.get("status") != "resolved":
                continue
            for key in ("id", "question", "candidate", "anchor"):
                value = str(slot.get(key) or "").strip()
                if value:
                    text.add(value.lower())
                    if key == "id":
                        ids.add(value.lower())
        return {"ids": ids, "text": text}

    @staticmethod
    def _requested_slot_terms(hint: dict[str, Any]) -> set[str]:
        terms: set[str] = set()
        slots = hint.get("slots")
        if isinstance(slots, dict):
            for slot_id, question in slots.items():
                if str(slot_id).strip():
                    terms.add(str(slot_id).strip().lower())
                if str(question).strip():
                    terms.add(str(question).strip().lower())
        elif isinstance(slots, list):
            for slot in slots:
                if not isinstance(slot, dict):
                    continue
                for key in ("id", "question"):
                    value = str(slot.get(key) or "").strip()
                    if value:
                        terms.add(value.lower())
        for needle in hint.get("needles") or []:
            value = str(needle or "").strip()
            if value:
                terms.add(value.lower())
        return terms

    @staticmethod
    def _normalize_bridge_hint(hint: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(hint)
        scope = str(normalized.get("scope") or "").strip().lower()
        if scope in {"targeted", "specific", "selected"} or any(
            term in scope for term in ("targeted", "specific", "selected")
        ):
            normalized["scope"] = "narrow"
        elif scope in {"document", "entire", "full", "all", "tail"} or any(
            term in scope for term in ("document", "entire", "full", "all", "tail", "file")
        ):
            normalized["scope"] = "new"
        want = str(normalized.get("want") or "").strip().lower()
        allowed_wants = {"fact", "count", "verbatim", "table", "schema", "list"}
        if want and want not in allowed_wants:
            if "count" in want or "how many" in want or "number" in want:
                normalized["want"] = "count"
            elif "list" in want:
                normalized["want"] = "list"
            elif "schema" in want:
                normalized["want"] = "schema"
            elif "table" in want:
                normalized["want"] = "table"
            elif "verbatim" in want or "exact" in want or "line" in want:
                normalized["want"] = "verbatim"
            else:
                normalized["want"] = "fact"
        type_hint = str(normalized.get("type_hint") or "").strip().lower()
        if type_hint in {"key-value", "key-value assignment", "kv", "markdown"}:
            normalized["type_hint"] = "text"
        return normalized

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

    def _classified_gate_for_path(self, path: Path) -> tuple[FileInfo, BenefitDecision, dict[str, Any]]:
        info = inspect_file(path)
        decision = self.runtime.orchestrator.benefit_gate.decide(info)
        gate = self.classifier(info, decision)
        if self._native_passthrough_path(path):
            gate = native_passthrough_gate(
                f"{self.policy.platform} native pass-through: generated/runtime artifacts should not re-enter SparseRead",
                include_search=self.policy.gate_key == "openclaw_gate",
            )
        else:
            parent_gate = self._force_collection_parent_gate(path)
            if parent_gate:
                gate = parent_gate
        ready_artifact = self._adapter_ready_artifact_for_path(path)
        if ready_artifact:
            gate = copy.deepcopy(gate)
            gate.update(
                {
                    "already_ready": True,
                    "protocol_next": "write_file_now",
                    "block_native_read": True,
                    "block_native_search": True,
                    "block_native_exec_dump": True,
                    "handoff_path": str(self._adapter_artifact_roots.get(ready_artifact) or path),
                    "ready_instruction": self._ready_instruction(ready_artifact),
                    "reason": "adapter closure already ready; write the deliverable instead of rereading source files",
                }
            )
        return info, decision, gate

    def _preflight_candidates(self, workspace: Path, *, max_candidates: int) -> list[Path]:
        candidates: list[Path] = []
        skip_names = {"node_modules", "__pycache__", ".git", ".pytest_cache"}
        if workspace.exists() and workspace.is_dir():
            for child in sorted(workspace.iterdir(), key=lambda item: item.name.lower()):
                if child.name in skip_names or child.name.startswith("."):
                    continue
                candidates.append(child)
                if len(candidates) >= max_candidates:
                    break
        return candidates or [workspace]

    @staticmethod
    def _relative_path(path: Path, root: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    @staticmethod
    def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return min(max(number, lower), upper)

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

        # Token tracker summary (SR-side estimates — always available)
        token_session = self._token_tracker.session_summary()
        token_summary = {
            "sr_operations": token_session.total_operations,
            "sr_full_file_tokens_est": token_session.total_full_file_tokens,
            "sr_response_tokens_est": token_session.total_sr_response_tokens,
            "sr_tokens_saved_est": token_session.total_tokens_saved,
            "sr_savings_ratio": round(token_session.overall_savings_ratio, 4),
            "sr_context_retained_pct": round(token_session.context_retained_pct, 2),
            "sr_top_savings": token_session.top_savings[:5],
            "sr_log_path": str(self._token_tracker._log_path),
        }

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
            "token_tracker": token_summary,
        }

    @staticmethod
    def _usage_interpretation(session: dict[str, Any]) -> str:
        """Human-readable interpretation of token metrics."""
        ratio = session.get("savings_ratio", 0)
        saved = session.get("tokens_saved", 0)
        context = session.get("context_window", DEFAULT_CONTEXT_WINDOW)
        retained = session.get("context_retained_pct", 0)
        ops = session.get("operations", 0)

        if ratio <= 0 or ops == 0:
            return "No SparseRead operations recorded yet — token tracking inactive."

        tier = (
            "excellent" if ratio > 0.9
            else "very good" if ratio > 0.7
            else "good" if ratio > 0.5
            else "moderate"
        )
        return (
            f"SparseRead saved ~{saved:,} tokens across {ops} operations "
            f"({ratio:.1%} savings — {tier}). "
            f"That preserved ~{retained:.1f}% of a {context:,}-token context window "
            f"for other work."
        )

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
        if kind == "sro_preflight":
            return {
                "handoff_count": result.get("handoff_count"),
                "first_action": result.get("first_action"),
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
