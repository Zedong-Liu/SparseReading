"""JSONL bridge used by the OpenCode SparseRead pilot.

The bridge keeps one SparseRead runtime alive per process so OpenCode plugin
tools can reuse artifact ids, reader caches, and ready-state across tool calls.
It deliberately delegates all reading decisions to the existing nanobot SRO
core; this module is only an I/O adapter.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from nanobot.sparse_reading.detector import inspect_file
from nanobot.sparse_reading.detector import FileInfo
from nanobot.sparse_reading.benefit_gate import BenefitDecision
from sparseread import SparseRead
from sparseread.config import SparseReadConfig


def classify_opencode_gate(info: FileInfo, decision: BenefitDecision) -> dict[str, Any]:
    """Adapt nanobot's benefit gate to OpenCode's higher tool overhead.

    The nanobot gate can hard-handoff filesystem tools because SR is native to
    the agent loop. OpenCode currently pays more schema/tool/cache overhead, so
    collection shapes that worked as hard force in nanobot need a narrower
    force boundary here.
    """

    reason = decision.reason.lower()
    profile: dict[str, Any] = {
        "mode": "native",
        "prompt_style": "native",
        "block_native_read": False,
        "nudge_native": False,
        "trajectory": "native",
        "reason": decision.reason,
    }
    if decision.mode == "native":
        return profile
    if decision.mode == "advisory":
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "optional",
                "nudge_native": True,
                "trajectory": "optional",
            }
        )
        return profile
    if info.type == "collection" and "command-security bundle" in reason:
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "closure_once",
                "block_native_read": False,
                "nudge_native": True,
                "trajectory": "one_collect_then_write",
                "reason": (
                    "command-security bundle has compact closure facts; in OpenCode prefer "
                    "one collection collect, but allow native reads for small templates and "
                    "named unresolved files"
                ),
            }
        )
        return profile
    enforceable_collection = (
        "audit bundle has code plus state/output evidence" in reason
        or "collection contains a long pdf/report" in reason
        or "multi-file text collection" in reason
        or "large audit/diagnosis bundle" in reason
        or "diagnosis bundle contains long log" in reason
    )
    if info.type == "collection" and not enforceable_collection:
        profile.update(
            {
                "mode": "advisory",
                "prompt_style": "optional",
                "nudge_native": True,
                "trajectory": "optional",
                "reason": f"OpenCode advisory adaptation: {decision.reason}",
            }
        )
        return profile
    profile.update(
        {
            "mode": "enforce",
            "prompt_style": "sro_first",
            "block_native_read": True,
            "nudge_native": True,
            "trajectory": "sro_first",
        }
    )
    return profile


def native_passthrough_gate(reason: str) -> dict[str, Any]:
    return {
        "mode": "native",
        "prompt_style": "native",
        "block_native_read": False,
        "nudge_native": False,
        "trajectory": "native",
        "reason": reason,
    }


class OpenCodeBridge:
    def __init__(self, *, workspace: str | Path | None, mode: str = "auto") -> None:
        self.runtime = SparseRead(SparseReadConfig(mode=mode, workspace=workspace))
        self.workspace = str(Path(workspace).resolve()) if workspace else None
        self.started_at = time.time()
        self.events: list[dict[str, Any]] = []
        self._adapter_ready_artifacts: dict[str, dict[str, Any]] = {}
        self._adapter_verify_passes: dict[str, int] = {}
        self._adapter_guard_hits = 0

    def preview(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._require_str(params, "path")
        budget = params.get("budget")
        info = inspect_file(Path(path))
        decision = self.runtime.orchestrator.benefit_gate.decide(info)
        gate = classify_opencode_gate(info, decision)
        preview_path = Path(path)
        if self._native_passthrough_path(preview_path):
            gate = native_passthrough_gate("OpenCode native pass-through: generated/runtime artifacts should not re-enter SparseRead")
        else:
            parent_gate = self._force_collection_parent_gate(preview_path)
            if parent_gate:
                preview_path = Path(str(parent_gate["handoff_path"]))
                gate = parent_gate
        result = self.runtime.orchestrator.preview(
            preview_path,
            budget=int(budget) if isinstance(budget, int) else None,
        )
        if gate.get("mode") == "native":
            card = result.get("file_card") if isinstance(result.get("file_card"), dict) else {}
            card["sparse_recommended"] = False
            card["recommended_mode"] = "native"
            card["reason"] = str(gate.get("reason") or "OpenCode native path is cheaper than SparseRead")
        result["opencode_gate"] = gate
        result["production_note"] = "sro_preview is the production entrypoint; sro_card remains for benchmark/legacy compatibility."
        if gate.get("trajectory") == "one_collect_then_write":
            result["protocol_note"] = (
                "OpenCode trajectory: after this preview, call exactly one sro_read(mode=collect) "
                "only when concrete slots are known, then write when ready."
            )
        self._record("sro_preview", {"path": path, "budget": budget}, result)
        return result

    def card(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._require_str(params, "path")
        info = inspect_file(Path(path))
        decision = self.runtime.orchestrator.benefit_gate.decide(info)
        gate = classify_opencode_gate(info, decision)
        card = self.runtime.orchestrator.card(Path(path))
        if self._native_passthrough_path(Path(path)):
            gate = native_passthrough_gate("OpenCode native pass-through: generated/runtime artifacts should not re-enter SparseRead")
            card.sparse_recommended = False
            card.recommended_mode = "native"
            card.reason = gate["reason"]
        else:
            parent_gate = self._force_collection_parent_gate(Path(path))
            if parent_gate:
                parent_path = Path(str(parent_gate["handoff_path"]))
                card = self.runtime.orchestrator.card(parent_path)
                gate = parent_gate
        result: dict[str, Any] = {"file_card": card.to_dict(), "opencode_gate": gate}
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
                "OpenCode trajectory: call exactly one sro_read(mode=collect) with explicit slots, "
                "then write the requested report/JSON when slot_digest is ready. Do not repeat "
                "sro_read after ready; use native reads only for named unresolved slots."
            )
        self._record("sro_card", {"path": path}, result)
        return result

    def read(self, params: dict[str, Any]) -> dict[str, Any]:
        target = params.get("target")
        mode = params.get("mode")
        hint = params.get("hint") or {}
        if isinstance(target, str) and target:
            target = {"artifact_id": target}
        if not isinstance(target, dict):
            raise ValueError("target must be an object with path or artifact_id")
        if isinstance(mode, str):
            mode = {"full": "collect", "scan": "scout", "preview": "scout"}.get(mode, mode)
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
        packed = self._opencode_pack(pack.to_dict())
        self._remember_adapter_ready_pack(packed)
        result = {"evidence_pack": packed}
        self._record("sro_read", {"target": target, "mode": mode, "hint": hint}, result)
        return result

    def decide(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._require_str(params, "path")
        info = inspect_file(Path(path))
        decision = self.runtime.orchestrator.benefit_gate.decide(info)
        gate = classify_opencode_gate(info, decision)
        if self._native_passthrough_path(Path(path)):
            gate = native_passthrough_gate("OpenCode native pass-through: generated/runtime artifacts should not re-enter SparseRead")
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
            "opencode_gate": gate,
            "should_handoff_read": self.runtime.orchestrator.should_handoff_read(Path(path)),
        }
        self._record("sro_decide", {"path": path}, result)
        return result

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
            "ready_collection_artifacts": sorted(
                getattr(orchestrator, "_ready_collection_artifacts", {}).keys()
            ),
            "slot_digest_artifacts": sorted(getattr(orchestrator, "_slot_digests", {}).keys()),
            "adapter_ready_artifacts": sorted(self._adapter_ready_artifacts),
            "adapter_verify_passes": dict(sorted(self._adapter_verify_passes.items())),
            "adapter_guard_hits": self._adapter_guard_hits,
            "events": self.events,
        }

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        if method == "card":
            return self.card(params)
        if method == "preview":
            return self.preview(params)
        if method == "read":
            return self.read(params)
        if method == "decide":
            return self.decide(params)
        if method == "trace":
            return self.trace(params)
        if method == "shutdown":
            return {"ok": True}
        raise ValueError(f"unknown method: {method}")

    def _remember_adapter_ready_pack(self, pack: dict[str, Any]) -> None:
        artifact_id = str(pack.get("artifact_id") or "")
        if not artifact_id:
            return
        next_action = pack.get("next_action") if isinstance(pack.get("next_action"), dict) else {}
        slot_digest = pack.get("slot_digest") if isinstance(pack.get("slot_digest"), dict) else {}
        ready = (
            slot_digest.get("overall_status") == "ready"
            or next_action.get("overall_status") == "ready"
        )
        if not ready:
            return
        pack_type = str(pack.get("type") or "")
        if pack_type not in {"collection", "pdf", "text", "txt", "md", "markdown", "rst"}:
            return
        self._adapter_ready_artifacts[artifact_id] = {
            "summary": pack.get("summary") or "evidence ready",
            "type": pack_type,
            "next_action": copy.deepcopy(next_action),
            "slot_digest": self._compact_slot_digest(slot_digest),
        }

    def _adapter_ready_artifact_for_target(self, target: dict[str, Any], hint: dict[str, Any]) -> str:
        artifact_id = str(target.get("artifact_id") or hint.get("artifact") or "").strip()
        return artifact_id if artifact_id in self._adapter_ready_artifacts else ""

    def _allow_bounded_ready_verify(self, artifact_id: str, mode: str) -> bool:
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

    @staticmethod
    def _opencode_pack(pack: dict[str, Any]) -> dict[str, Any]:
        slot_digest = pack.get("slot_digest")
        if not isinstance(slot_digest, dict) or slot_digest.get("overall_status") != "ready":
            return pack
        for slot in slot_digest.get("slots") or []:
            if isinstance(slot, dict):
                slot.pop("verify_ref", None)
        slot_digest["adapter_guard"] = "ready_for_write: do not call sro_read verify/refine for resolved slots"
        pack["protocol_next"] = "write_file_now"
        return pack

    def _adapter_already_ready_pack(self, artifact_id: str, mode: str) -> dict[str, Any]:
        self._adapter_guard_hits += 1
        ready = self._adapter_ready_artifacts.get(artifact_id, {})
        next_action = copy.deepcopy(ready.get("next_action") or {})
        pack_next_action = {
            "allowed_next": ["write_file"],
            "instruction": str(next_action.get("instruction") or "Use the existing ready evidence and write the requested deliverable now."),
            "guard": "opencode_adapter_ready_once",
            "prior_evidence_artifact": artifact_id,
        }
        required_outputs = next_action.get("required_outputs") if isinstance(next_action, dict) else []
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
        gate = classify_opencode_gate(parent_info, parent_decision)
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
        if kind in {"sro_card", "sro_preview"}:
            card = result.get("file_card", {})
            return {
                "artifact_id": card.get("artifact_id"),
                "sparse_recommended": card.get("sparse_recommended"),
                "recommended_mode": card.get("recommended_mode"),
                "reason": card.get("reason"),
                "entrypoint": result.get("entrypoint"),
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


def _write_response(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve(args: argparse.Namespace) -> int:
    bridge = OpenCodeBridge(workspace=args.workspace, mode=args.mode)
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
            _write_response({"id": request_id, "ok": True, "result": result})
            if request.get("method") == "shutdown":
                return 0
        except Exception as exc:
            _write_response({"id": request_id, "ok": False, "error": str(exc)})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SparseRead OpenCode JSONL bridge")
    parser.add_argument("--workspace", default=".", help="OpenCode workspace/worktree")
    parser.add_argument("--mode", default="auto", help="SparseRead mode")
    return serve(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
