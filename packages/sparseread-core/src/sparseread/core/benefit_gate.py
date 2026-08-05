"""Compact production benefit gate for SparseRead routing."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from sparseread.core.detector import FileInfo

BenefitMode = Literal["force_sro", "native", "advisory"]
BenefitAction = Literal["intercept", "pass", "nudge"]
GoalShape = Literal[
    "selective_read",
    "cross_file_evidence",
    "full_fidelity",
    "structured_compute",
    "edit_or_execute",
    "unknown",
]
EpisodeRelation = Literal["new", "continue", "switch", "unknown"]
CoverageShape = Literal["selective", "exhaustive", "unknown"]
_GOAL_SHAPES = {
    "selective_read",
    "cross_file_evidence",
    "full_fidelity",
    "structured_compute",
    "edit_or_execute",
    "unknown",
}
_EPISODE_RELATIONS = {"new", "continue", "switch", "unknown"}
_COVERAGE_SHAPES = {"selective", "exhaustive", "unknown"}


@dataclass(slots=True, frozen=True)
class GateContext:
    """Optional semantic hint supplied by the host model at an episode boundary."""

    goal: GoalShape = "unknown"
    relation: EpisodeRelation = "unknown"
    coverage: CoverageShape = "unknown"
    summary: str = ""

    @classmethod
    def from_value(cls, value: GateContext | Mapping[str, Any] | None) -> GateContext:
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            return cls()
        goal = str(value.get("goal") or "unknown").strip().lower()
        if goal == "compute_or_edit":
            goal = "edit_or_execute"
        relation = str(value.get("relation") or "unknown").strip().lower()
        coverage = str(value.get("coverage") or "unknown").strip().lower()
        return cls(
            goal=goal if goal in _GOAL_SHAPES else "unknown",  # type: ignore[arg-type]
            relation=relation if relation in _EPISODE_RELATIONS else "unknown",  # type: ignore[arg-type]
            coverage=coverage if coverage in _COVERAGE_SHAPES else "unknown",  # type: ignore[arg-type]
            summary=str(value.get("summary") or "").strip()[:500],
        )


@dataclass(slots=True, frozen=True)
class BenefitDecision:
    mode: BenefitMode
    reason: str
    confidence: float
    recommended_mode: str
    code: str = "unspecified"
    preview_recommended: bool = False
    scope_kind: str = "unknown"

    @property
    def action(self) -> BenefitAction:
        if self.mode == "force_sro":
            return "intercept"
        if self.mode == "native":
            return "pass"
        return "nudge"


class BenefitGate:
    """Small empirical gate with deterministic vetoes and optional model hints."""

    _TEXT_TYPES: ClassVar[set[str]] = {"pdf", "text", "txt", "md", "markdown", "rst", "html", "htm"}
    _TEXT_KINDS: ClassVar[set[str]] = {"text", "txt", "md", "markdown", "rst", "html", "htm", "eml"}
    _STRUCTURED_KINDS: ClassVar[set[str]] = {"csv", "tsv", "xls", "xlsx", "json", "yaml", "xml"}
    _TABULAR_KINDS: ClassVar[set[str]] = {"csv", "tsv", "xls", "xlsx"}
    _CODE_KINDS: ClassVar[set[str]] = {"py", "sh"}
    _NATIVE_TEXT_EXTS: ClassVar[set[str]] = {".py", ".sh", ".toml", ".ini", ".cfg", ".conf"}

    # These floors sit below the positive QwenClawBench examples (75K PDF,
    # 100K text, 17K audit bundle) while avoiding protocol tax on tiny inputs.
    _DOCUMENT_FORCE_BYTES = 4_096
    _COLLECTION_FORCE_BYTES = 8_192
    _TEXT_COLLECTION_FORCE_BYTES = 24_576
    _COLLECTION_FORCE_FILES = 8

    def __init__(self, collection_reader: Any, override: BenefitMode | None = None) -> None:
        self.collection_reader = collection_reader
        self.override = override

    def decide(
        self,
        info: FileInfo,
        context: GateContext | Mapping[str, Any] | None = None,
    ) -> BenefitDecision:
        ctx = GateContext.from_value(context)
        env_override = os.environ.get("SRO_BENEFIT_GATE_OVERRIDE", "").strip().lower()
        override = env_override or self.override or ""

        if not info.supported:
            return self._decision(
                "native",
                "unsupported type; use native tools",
                1.0,
                "native_read",
                "unsupported",
                scope_kind="unsupported",
            )
        if override in {"force_sro", "native", "advisory"}:
            source = "SRO_BENEFIT_GATE_OVERRIDE" if env_override else "SparseRead config"
            return self._decision(
                override,  # type: ignore[arg-type]
                f"benefit gate overridden by {source}={override}",
                1.0,
                "override",
                "override",
                preview=override != "native",
                scope_kind=self._scope_kind(info),
            )
        if info.type == "collection":
            return self._decide_collection(info.path, ctx)
        if not info.large:
            return self._decision(
                "native",
                "small object; native path is cheaper than SparseRead negotiation",
                0.95,
                "native_read",
                "below_benefit_floor",
                scope_kind=self._scope_kind(info),
            )
        if info.path.suffix.lower() in self._NATIVE_TEXT_EXTS:
            return self._decision(
                "native",
                "code or configuration work stays on native tools",
                0.95,
                "native_read",
                "native_code_or_config",
                scope_kind="code_or_config",
            )
        if ctx.goal in {"full_fidelity", "edit_or_execute"}:
            return self._decision(
                "native",
                "the requested operation needs complete source coverage or native execution",
                0.95,
                "native_read",
                f"native_{ctx.goal}",
                scope_kind=self._scope_kind(info),
            )
        if info.type in self._TEXT_TYPES:
            if info.size_bytes < self._DOCUMENT_FORCE_BYTES:
                return self._decision(
                    "advisory",
                    "document is near the sparse-reading benefit boundary",
                    0.65,
                    "sro_optional",
                    "document_boundary",
                    preview=True,
                    scope_kind="single_document",
                )
            code = "long_document_selective" if ctx.goal == "selective_read" else "long_document"
            return self._decision(
                "force_sro",
                "long document has a proven sparse-reading path; use collect+slots for multi-fact goals",
                0.9 if ctx.goal == "selective_read" else 0.82,
                "collect_if_multi_fact_else_scout",
                code,
                preview=True,
                scope_kind="single_document",
            )
        if info.structured:
            if ctx.goal == "structured_compute":
                return self._decision(
                    "native",
                    "single-table exact computation is cheaper on native tools",
                    0.92,
                    "native_read",
                    "native_single_table_compute",
                    scope_kind="structured_file",
                )
            return self._decision(
                "advisory",
                "large structured data benefits from SparseRead only for targeted evidence",
                0.7,
                "sro_optional",
                "structured_targeted_only",
                preview=True,
                scope_kind="structured_file",
            )
        return self._decision(
            "native",
            "no validated sparse-reading benefit for this resource shape",
            0.8,
            "native_read",
            "no_validated_benefit",
            scope_kind=self._scope_kind(info),
        )

    def _decide_collection(self, path: Path, ctx: GateContext) -> BenefitDecision:
        items = self.collection_reader._items(path)
        contains_long_pdf = self._contains_long_pdf(path)
        if not items:
            if contains_long_pdf:
                if ctx.goal in {"full_fidelity", "edit_or_execute", "structured_compute"}:
                    return self._decision(
                        "native",
                        "the requested operation needs complete source coverage or native execution",
                        0.95,
                        "native_read",
                        f"native_{ctx.goal}",
                        scope_kind="collection",
                    )
                return self._decision(
                    "force_sro",
                    "collection contains a long document with a proven sparse-reading path",
                    0.88,
                    "collect_if_multi_fact_else_scout",
                    "collection_long_document",
                    preview=True,
                    scope_kind="collection",
                )
            return self._decision(
                "native",
                "empty or unsupported collection; use native tools",
                1.0,
                "native_read",
                "empty_collection",
                scope_kind="collection",
            )

        total_size = sum(item.size for item in items)
        kinds = [str(item.kind).lower() for item in items]
        text_count = sum(kind in self._TEXT_KINDS for kind in kinds)
        structured_count = sum(kind in self._STRUCTURED_KINDS for kind in kinds)
        tabular_count = sum(kind in self._TABULAR_KINDS for kind in kinds)
        code_count = sum(kind in self._CODE_KINDS for kind in kinds)
        log_count = sum(kind == "log" for kind in kinds)
        count = len(items)

        structured_analysis = (
            count >= 5
            and structured_count >= 3
            and total_size >= 20_000
            and structured_count + text_count + code_count >= 4
        )
        edit_structured_prelude = (
            structured_analysis
            and tabular_count >= 3
            and structured_count * 2 > count
            and log_count == 0
        )

        if ctx.goal == "full_fidelity":
            return self._decision(
                "native",
                "the collection goal requires complete source coverage",
                0.95,
                "native_read",
                "native_full_fidelity",
                scope_kind="collection",
            )

        if ctx.goal in {"structured_compute", "edit_or_execute"}:
            if structured_analysis and (
                ctx.goal == "structured_compute" or edit_structured_prelude
            ):
                return self._decision(
                    "force_sro",
                    "multi-file structured inputs benefit from one bounded schema/table plan before native compute or editing",
                    0.88,
                    "collect",
                    "structured_analysis_plan",
                    preview=True,
                    scope_kind="collection",
                )
            if ctx.goal == "edit_or_execute":
                return self._decision(
                    "native",
                    "small or mixed editing/execution work is cheaper on native tools",
                    0.95,
                    "native_read",
                    "native_edit_or_execute",
                    scope_kind="collection",
                )
            return self._decision(
                "native",
                "small or single-source exact computation is cheaper on native tools",
                0.9,
                "native_read",
                "native_small_structured_compute",
                scope_kind="collection",
            )

        if contains_long_pdf:
            return self._decision(
                "force_sro",
                "collection contains a long document with a proven sparse-reading path",
                0.88,
                "collect_if_multi_fact_else_scout",
                "collection_long_document",
                preview=True,
                scope_kind="collection",
            )

        if ctx.goal == "cross_file_evidence":
            evidence_sources = text_count + structured_count + code_count + log_count
            structured_only = structured_count == count
            broad_exhaustive = ctx.coverage == "exhaustive" and (
                count >= 10 or total_size >= 40_000
            )
            if broad_exhaustive and not structured_only:
                return self._decision(
                    "advisory",
                    "broad exhaustive evidence work has token upside but uncertain latency benefit",
                    0.7,
                    "sro_optional",
                    "broad_evidence_boundary",
                    preview=True,
                    scope_kind="collection",
                )
            if count >= 3 and total_size >= self._COLLECTION_FORCE_BYTES and not structured_only and evidence_sources >= 3:
                return self._decision(
                    "force_sro",
                    "cross-file evidence goal matches a validated collection-reading path",
                    0.9,
                    "collect",
                    "multi_file_evidence",
                    preview=True,
                    scope_kind="collection",
                )
            return self._decision(
                "advisory",
                "cross-file goal is plausible but the collection is below the force boundary",
                0.68,
                "sro_optional",
                "multi_file_evidence_boundary",
                preview=True,
                scope_kind="collection",
            )

        text_heavy = text_count >= max(3, (count + 1) // 2)
        broad_text_collection = text_heavy and (
            total_size >= self._TEXT_COLLECTION_FORCE_BYTES
            or count >= self._COLLECTION_FORCE_FILES
        )
        if broad_text_collection:
            return self._decision(
                "advisory",
                "large text collection can benefit from source selection once the episode goal is known",
                0.72,
                "sro_optional",
                "large_text_collection_boundary",
                preview=True,
                scope_kind="collection",
            )

        # Mixed collections are where task intent matters most. Keep preview
        # available, but do not infer audit or computation from filenames.
        mixed = len(set(kinds)) >= 2 or (code_count and structured_count)
        if mixed or total_size >= self._COLLECTION_FORCE_BYTES or count >= 3:
            return self._decision(
                "advisory",
                "collection benefit depends on whether the goal is cross-file evidence or native computation",
                0.62,
                "sro_optional",
                "collection_goal_required",
                preview=True,
                scope_kind="collection",
            )
        return self._decision(
            "native",
            "small collection; native tools are cheaper",
            0.9,
            "native_read",
            "small_collection",
            scope_kind="collection",
        )

    @classmethod
    def _contains_long_pdf(cls, path: Path) -> bool:
        try:
            return any(
                entry.is_file()
                and entry.suffix.lower() == ".pdf"
                and entry.stat().st_size >= cls._DOCUMENT_FORCE_BYTES
                for entry in path.rglob("*")
            )
        except OSError:
            return False

    @staticmethod
    def _scope_kind(info: FileInfo) -> str:
        if info.type == "collection":
            return "collection"
        if info.structured:
            return "structured_file"
        if info.type in BenefitGate._TEXT_TYPES:
            return "single_document"
        return "single_file"

    @staticmethod
    def _decision(
        mode: BenefitMode,
        reason: str,
        confidence: float,
        recommended_mode: str,
        code: str,
        *,
        preview: bool = False,
        scope_kind: str,
    ) -> BenefitDecision:
        return BenefitDecision(
            mode=mode,
            reason=reason,
            confidence=confidence,
            recommended_mode=recommended_mode,
            code=code,
            preview_recommended=preview,
            scope_kind=scope_kind,
        )
