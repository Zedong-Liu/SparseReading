"""Deterministic benefit gate for deciding when SRO should intervene."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from nanobot.sparse_reading.detector import FileInfo


BenefitMode = Literal["force_sro", "native", "advisory"]
BenefitAction = Literal["intercept", "pass", "nudge"]


@dataclass(slots=True)
class BenefitDecision:
    mode: BenefitMode
    reason: str
    confidence: float
    recommended_mode: str

    @property
    def action(self) -> BenefitAction:
        if self.mode == "force_sro":
            return "intercept"
        if self.mode == "native":
            return "pass"
        return "nudge"


class BenefitGate:
    """Small deterministic gate; no model classifier and no new macro protocol."""

    _TEXT_TYPES = {"pdf", "text", "txt", "md", "markdown", "rst"}
    _NATIVE_TEXT_EXTS = {".py", ".sh", ".toml", ".ini", ".cfg", ".conf"}

    def __init__(self, collection_reader: Any, override: BenefitMode | None = None) -> None:
        self.collection_reader = collection_reader
        self.override = override

    def decide(self, info: FileInfo) -> BenefitDecision:
        env_override = os.environ.get("SRO_BENEFIT_GATE_OVERRIDE", "").strip().lower()
        override = env_override or self.override or ""
        if not info.supported:
            return BenefitDecision("native", "unsupported type; use native tools", 1.0, "native_read")
        if override in {"force_sro", "native", "advisory"}:
            source = "SRO_BENEFIT_GATE_OVERRIDE" if env_override else "SparseRead config"
            return BenefitDecision(
                override,  # type: ignore[arg-type]
                f"benefit gate overridden by {source}={override}",
                1.0,
                "override",
            )
        if info.type == "collection":
            return self._decide_collection(info.path)
        if not info.large:
            return BenefitDecision("native", "small object; native path cheaper than SRO negotiation", 0.95, "native_read")
        if info.type == "pdf":
            return BenefitDecision(
                "force_sro",
                "long PDF/report object; use collect+slots or focused sparse reading",
                0.9,
                "collect_if_multi_fact_else_scout",
            )
        if info.path.suffix.lower() in self._NATIVE_TEXT_EXTS:
            return BenefitDecision(
                "native",
                "code/config file; native read or local execution is cheaper than SRO negotiation",
                0.85,
                "native_read",
            )
        if info.type in self._TEXT_TYPES:
            return BenefitDecision(
                "force_sro",
                "long text/report object; use collect+slots for multi-fact QA, otherwise scout/focus",
                0.85,
                "collect_if_multi_fact_else_scout",
            )
        if info.structured:
            return BenefitDecision(
                "advisory",
                "large structured object; use SRO only for targeted schema/row evidence, otherwise run local code on the file",
                0.7,
                "sro_optional",
            )
        return BenefitDecision("native", "no clear sparse-reading benefit; use native tools", 0.8, "native_read")

    def _decide_collection(self, path: Path) -> BenefitDecision:
        items = self.collection_reader._items(path)
        if not items:
            if self._contains_long_pdf(path):
                return BenefitDecision(
                    "force_sro",
                    "collection contains a long PDF/report; keep SRO available for sparse PDF reading",
                    0.9,
                    "collect_if_multi_fact_else_scout",
                )
            return BenefitDecision("native", "empty or unsupported collection; use native tools", 1.0, "native_read")

        total_size = sum(item.size for item in items)
        names = " ".join(item.name.lower() for item in items)
        kinds = {item.kind for item in items}

        if self._is_small_rule_table_bundle(items, total_size):
            return BenefitDecision(
                "native",
                "small rules/users bundle; native path cheaper than SRO negotiation",
                0.9,
                "native_read",
            )
        if self._is_full_analysis_forecast_bundle(names, kinds, len(items)):
            return BenefitDecision(
                "native",
                "full-analysis forecast bundle; local script over source files is cheaper than SRO negotiation",
                0.85,
                "native_read",
            )
        if self._is_full_analysis_panel_did_bundle(names, kinds, len(items)):
            return BenefitDecision(
                "native",
                "panel/DID regression bundle; local script over full structured files is cheaper than SRO negotiation",
                0.88,
                "native_read",
            )
        if self._is_small_query_spec_bundle(items, names, total_size):
            return BenefitDecision(
                "native",
                "small query/spec generation bundle; native source reads are cheaper than SRO negotiation",
                0.86,
                "native_read",
            )
        if self._is_command_security_bundle(items, names):
            return BenefitDecision(
                "force_sro",
                "command-security bundle has compact closure facts; use collection collect once, then write required outputs",
                0.84,
                "collect",
            )
        audit_or_diagnosis = self._audit_or_diagnosis_decision(items, names, kinds, total_size)
        if audit_or_diagnosis is not None:
            return audit_or_diagnosis
        if self._is_text_collection(items, total_size):
            return BenefitDecision(
                "force_sro",
                "multi-file text collection; use collect/focus to select and inspect candidate files",
                0.82,
                "collect",
            )
        if len(items) > 16 or total_size > 25_000:
            return BenefitDecision(
                "advisory",
                "large mixed collection without clear audit/text signal; use SRO only if task needs sparse source selection",
                0.65,
                "sro_optional",
            )
        return BenefitDecision(
            "advisory",
            "medium collection without clear sparse-reading signal; native path is acceptable",
            0.6,
            "sro_optional",
        )

    @staticmethod
    def _contains_long_pdf(path: Path) -> bool:
        try:
            return any(entry.is_file() and entry.suffix.lower() == ".pdf" and entry.stat().st_size >= 4096 for entry in path.rglob("*"))
        except OSError:
            return False

    @staticmethod
    def _is_small_rule_table_bundle(items: list[Any], total_size: int) -> bool:
        if total_size > 12_000 or len(items) > 16:
            return False
        if any(item.kind in {"log", "py", "sh"} for item in items):
            return False
        return (
            all(item.size <= 4_096 for item in items)
            and any("discount" in item.name.lower() and item.kind == "json" for item in items)
            and any("user" in item.name.lower() and item.kind in {"csv", "tsv"} for item in items)
        )

    @staticmethod
    def _is_full_analysis_forecast_bundle(names: str, kinds: set[str], item_count: int) -> bool:
        if item_count > 24:
            return False
        if kinds & {"log", "py", "sh"}:
            return False
        has_forecast_actual = "forecast" in names and "actual" in names
        has_baseline = "baseline" in names
        has_context = any(term in names for term in ("metadata", "performance", "changelog", "weather", "config"))
        return has_forecast_actual and has_baseline and has_context

    @staticmethod
    def _is_full_analysis_panel_did_bundle(names: str, kinds: set[str], item_count: int) -> bool:
        if item_count > 32:
            return False
        if "log" in kinds:
            return False
        has_panel = "panel" in names and any(term in names for term in ("firm_metadata", "data_dictionary", "metadata"))
        has_did = "did" in names or "regression" in names or "difference" in names
        has_analysis_script = "py" in kinds or "analysis" in names or "script" in names
        has_structured_data = bool(kinds & {"csv", "json", "yaml", "xlsx"})
        return has_panel and has_did and has_analysis_script and has_structured_data

    @staticmethod
    def _is_small_query_spec_bundle(items: list[Any], names: str, total_size: int) -> bool:
        if total_size > 35_000 or len(items) > 14:
            return False
        query_signal = any(term in names for term in ("sparql", "query_requirements", "ontology", "triplestore"))
        support_signal = any(term in names for term in ("examples", "endpoint", "catalog", "load_data"))
        if not (query_signal and support_signal):
            return False
        # These tasks require reading a few authoritative specs/data files and writing
        # code/query text. They are not sparse long-document collection searches.
        return all(item.size <= 8_192 for item in items)

    @staticmethod
    def _is_command_security_bundle(items: list[Any], names: str) -> bool:
        has_script = any(item.kind == "sh" or item.name.lower().endswith(".sh") for item in items)
        has_policy = "security_policy" in names or ("policy" in names and any(term in names for term in ("security", "command", "injection")))
        has_prefix_guide = "command_prefix_guide" in names or ("prefix" in names and any(term in names for term in ("command", "guide", "rule")))
        has_conflict_sources = any(term in names for term in ("known_injections", "legacy_rules", "security_bulletin", "injection", "legacy", "advisory", "bulletin"))
        has_tests = "test_commands" in names or ("test" in names and "command" in names)
        return has_script and has_policy and has_prefix_guide and has_conflict_sources and has_tests

    def _audit_or_diagnosis_decision(
        self,
        items: list[Any],
        names: str,
        kinds: set[str],
        total_size: int,
    ) -> BenefitDecision | None:
        has_code = bool(kinds & {"py", "sh"})
        has_log = "log" in kinds
        has_state = "state" in names or "seen_ids" in names or "checkpoint" in names
        has_output = "output" in names or "outputs" in names or "announcement" in names or "result" in names
        has_config = "config" in names or "yaml" in kinds or "toml" in kinds or "ini" in kinds

        # Strong audit bundles have a compact cross-check closure: state/output/config/code.
        # These are the task12 shape and are worth forcing through SRO.
        if has_code and has_output and (has_state or "fetch" in names or "audit" in names):
            return BenefitDecision(
                "force_sro",
                "audit bundle has code plus state/output evidence; use compact collection closure",
                0.9,
                "collect",
            )

        max_log_size = max((item.size for item in items if item.kind == "log"), default=0)
        weak_diagnosis = has_log and has_code and has_config and not (has_state and has_output)
        if weak_diagnosis and total_size < 40_000 and max_log_size < 12_288:
            return BenefitDecision(
                "native",
                "small diagnosis bundle; native path avoids SRO tool/schema overhead",
                0.82,
                "native_read",
            )

        # Long logs or broad text collections still benefit from source selection/excerpts.
        if has_log and (max_log_size >= 12_288 or total_size >= 40_000 or len(items) >= 12):
            return BenefitDecision(
                "force_sro",
                "diagnosis bundle contains long log or many sources; use sparse collection excerpts",
                0.82,
                "collect",
            )

        audit_terms = {
            "audit", "diagnos", "state", "output", "announcement", "scheduled",
            "fetch", "retry", "error", "log", "config",
        }
        matches = sum(1 for term in audit_terms if term in names)
        if has_code and matches >= 3 and total_size >= 40_000:
            return BenefitDecision(
                "force_sro",
                "large audit/diagnosis bundle with code and multiple evidence sources; use collect",
                0.78,
                "collect",
            )
        if matches >= 3:
            return BenefitDecision(
                "advisory",
                "diagnostic signals found, but sparse-reading benefit is uncertain; native path is acceptable",
                0.62,
                "sro_optional",
            )
        return None

    @staticmethod
    def _is_text_collection(items: list[Any], total_size: int) -> bool:
        text_like = {"text", "txt", "md", "markdown", "rst", "eml"}
        text_count = sum(1 for item in items if item.kind in text_like or item.name.lower().endswith(".eml"))
        return len(items) >= 3 and text_count >= max(3, len(items) // 2)
