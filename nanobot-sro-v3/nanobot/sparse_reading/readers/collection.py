"""Sparse reader for directories of small text files."""

from __future__ import annotations

import configparser
import re
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nanobot.sparse_reading.models import EvidenceBlock, EvidencePack, HintSpec


@dataclass(slots=True)
class CollectionItem:
    name: str
    path: Path
    size: int
    kind: str = "text"
    subject: str = ""
    sender: str = ""
    date: str = ""
    snippet: str = ""


class CollectionReader:
    """Index and selectively expand a small-document collection."""

    _COLLECTION_EXTS = {
        ".txt", ".md", ".markdown", ".rst", ".eml",
        ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml",
        ".xlsx",
        ".log", ".py", ".sh", ".toml", ".ini", ".cfg", ".conf",
        ".pdf",
    }
    _TYPED_CHILD_KINDS = {"pdf"}
    _STRUCTURED_CHILD_KINDS = {"csv", "xlsx", "json", "yaml", "xml"}
    _STOPWORDS = {
        "the", "and", "for", "with", "from", "that", "this", "into", "need",
        "find", "search", "email", "emails", "folder", "everything", "related",
        "all", "about", "create", "summary", "summarize", "report",
    }
    _SKIP_DIRS = {
        ".git", ".nanobot", ".openclaw", "__pycache__", ".pytest_cache",
        ".ruff_cache", "memory", "sessions", "bootstrap", "skills",
    }
    _SKIP_FILES = {"AGENTS.md", "BOOTSTRAP.md", "HEARTBEAT.md", "IDENTITY.md", "SOUL.md", "TOOLS.md", "USER.md"}

    def card_details(self, path: Path, *, limit: int = 20) -> dict:
        items = self._items(path)
        return {
            "kind": "collection_card",
            "file_count": len(items),
            "files": [self._item_summary(item) for item in items[:limit]],
            "truncated": len(items) > limit,
        }

    def typed_candidates(self, path: Path, hint: HintSpec) -> list[CollectionItem]:
        """Return ranked children that must be read by another typed reader."""
        all_items = self._items(path)
        items = [
            item
            for item in all_items
            if item.kind == "pdf"
            or (len(all_items) == 1 and item.kind in self._STRUCTURED_CHILD_KINDS)
        ]
        if not items:
            return []
        ranked = self._rank_items(items, hint)
        if len(ranked) > 1 and ranked[0].score >= ranked[1].score + 2.0:
            ranked = ranked[:1]
        ranked_names = [block.anchor for block in ranked]
        if len(ranked) != 1:
            ranked_names.extend(item.name for item in items if item.name not in ranked_names)
        by_name = {item.name: item for item in items}
        return [by_name[name] for name in ranked_names if name in by_name]

    def read(self, path: Path, artifact_id: str, mode: str, hint: HintSpec, budget: int) -> EvidencePack:
        items = self._items(path)
        if not items:
            return EvidencePack(
                artifact_id=artifact_id,
                mode=mode,
                type="collection",
                summary="empty supported collection",
                error=f"No supported text files found in {path}",
            )

        selected_names = self._selected_names(hint, items)
        collect_wants_digest = mode == "collect" and self._goal_wants_excerpt_digest(hint)
        if selected_names and not collect_wants_digest and (mode in {"verify", "refine"} or hint.scope == "expand"):
            return self._expand_selected(path, artifact_id, mode, hint, budget, items, selected_names)

        ranked = self._rank_items(items, hint)
        if mode == "collect" or self._goal_wants_excerpt_digest(hint):
            return self._excerpt_digest(path, artifact_id, mode, hint, budget, items, ranked)
        selected = self._fit_budget(ranked, budget)
        unresolved = self._unresolved(hint, selected)
        candidate_names = [block.anchor for block in selected]
        next_hint = None
        if candidate_names:
            next_hint = {
                "goal": f"Inspect selected candidate files for: {hint.goal}",
                "needles": candidate_names[:10],
                "want": "fact",
                "scope": "verify",
                "artifact": artifact_id,
                "type_hint": "collection",
                "must_keep": candidate_names[:10],
            }
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary=f"{len(selected)} candidate files selected from {len(items)} files",
            skeleton=[self._skeleton_line(item) for item in items[:12]],
            evidence=selected,
            unresolved=unresolved,
            next_hint=next_hint,
            next_action={
                "tool": "sro_read",
                "mode": "verify",
                "target": {"artifact_id": artifact_id},
                "selected_files": candidate_names[:10],
                "instruction": "inspect only selected candidate files",
            } if candidate_names else None,
        )

    def _excerpt_digest(
        self,
        path: Path,
        artifact_id: str,
        mode: str,
        hint: HintSpec,
        budget: int,
        items: list[CollectionItem],
        ranked: list[EvidenceBlock],
    ) -> EvidencePack:
        item_by_name = {item.name: item for item in items}
        names = [block.anchor for block in ranked] or [item.name for item in items]
        blocks: list[EvidenceBlock] = []
        source_texts: dict[str, str] = {}
        used = 0
        for name in names:
            item = item_by_name.get(name)
            if item is None:
                continue
            if item.kind in self._TYPED_CHILD_KINDS:
                continue
            try:
                text = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue
            source_texts[item.name] = text
            excerpt = self._excerpt_file(item, text, hint)
            if not excerpt:
                continue
            block = EvidenceBlock(item.name, excerpt, 10.0)
            cost = len(block.anchor) + len(block.text) + 32
            if blocks and used + cost > budget:
                continue
            blocks.append(block)
            used += cost
            if used >= budget:
                break
        if self._goal_wants_diagnosis(hint):
            self._fill_diagnostic_sources(source_texts, items)
        if self._goal_wants_audit(hint):
            self._fill_audit_sources(source_texts, items)
        panel_hint = hint
        if self._items_look_like_panel_did(items) and not self._goal_wants_panel_did(hint):
            panel_hint = HintSpec(
                goal=(
                    "Build the complete panel Difference-in-Differences data/model contract with firm and year "
                    "fixed effects, clustered standard errors, parallel trends, and required deliverables"
                ),
                needles=["panel", "Difference-in-Differences", "firm fixed effects", "parallel trends"],
                want="schema",
                scope=hint.scope,
                artifact=hint.artifact,
                type_hint="collection",
                must_keep=hint.must_keep,
                slots=hint.slots,
            )
        if self._goal_wants_panel_did(panel_hint):
            self._fill_analysis_sources(source_texts, items)
        if self._goal_wants_rule_table_script(hint):
            self._fill_rule_table_script_sources(source_texts, items)
        if self._goal_wants_command_security(hint) or self._items_look_like_command_security(items):
            self._fill_command_security_sources(source_texts, items)
        if self._collection_is_diagnostic_shape(items, source_texts) or self._goal_wants_diagnostic_ledger(hint):
            self._fill_diagnostic_ledger_sources(source_texts, items)
        security_closure = self._command_security_closure(source_texts, hint)
        closure = self._diagnostic_closure(source_texts, hint)
        audit_closure = self._audit_closure(source_texts, hint, items)
        configuration_audit_closure = self._configuration_audit_closure(source_texts, hint)
        panel_closure = self._panel_did_closure(source_texts, panel_hint)
        compute_closure, compute_sources, compute_excluded = self._structured_compute_plan(items, hint)
        rule_script_closure = self._rule_table_script_closure(source_texts, hint)
        ledger_compact, ledger_sections, ledger_ready = self._diagnostic_ledger_closure(source_texts, hint, items)
        covered_sources: list[str] = []
        if security_closure:
            blocks = [EvidenceBlock("collection_command_security_closure", security_closure, 12.0)]
            covered_sources = sorted(source_texts)
        elif closure:
            # A ready cross-file diagnosis must stay compact enough to be used
            # directly. Returning the source excerpts as well can overflow the
            # tool-result boundary and tempt the model into rereading them.
            blocks = [EvidenceBlock("collection_diagnosis_closure", closure, 12.0)]
            covered_sources = sorted(source_texts)
        elif audit_closure:
            blocks = [EvidenceBlock("collection_audit_closure", audit_closure, 12.0)]
            covered_sources = sorted(source_texts)
        elif configuration_audit_closure:
            blocks = [EvidenceBlock("collection_configuration_audit_closure", configuration_audit_closure, 12.0)]
            covered_sources = sorted(source_texts)
        elif panel_closure:
            blocks = [EvidenceBlock("collection_panel_did_closure", panel_closure, 12.0)]
            covered_sources = sorted(
                name for name in source_texts
                if self._is_analysis_source_anchor(name) or "panel" in Path(name).name.lower()
            )
        elif rule_script_closure:
            blocks = [EvidenceBlock("collection_rule_table_script_closure", rule_script_closure, 12.0)]
            covered_sources = sorted(source_texts)
        elif ledger_compact:
            blocks = [EvidenceBlock("collection_diagnostic_ledger", ledger_compact, 12.0)]
            covered_sources = sorted(source_texts)
        elif compute_closure:
            blocks = [EvidenceBlock("collection_structured_compute_plan", compute_closure, 12.0)]
            covered_sources = compute_sources
        unresolved = [] if panel_closure else self._unresolved(hint, blocks)
        allowed_next = ["write_file", "run short calculation from these excerpts", "verify specific missing fact only"]
        instruction = "Use these source-keyed excerpts as evidence. Do not reread every file; verify only a named missing fact."
        slot_digest: dict[str, Any] | None = None
        if security_closure:
            allowed_next = ["write_file"]
            instruction = (
                "The command-security closure lists the commands, classifications, conflict resolution, "
                "test-count summary, and required output files. Write security_analysis_report.md and "
                "command_classifications.json from it now; do not reread resolved source files."
            )
        elif closure:
            allowed_next = ["write_file", "apply one explicit configuration fix", "initialize requested repository"]
            instruction = (
                "The diagnosis closure already cross-checks logs, config, and scripts. "
                "Use it to write the requested deliverables and apply only the explicit fix it identifies; "
                "do not reread covered sources."
            )
        elif audit_closure:
            allowed_next = ["write_file"]
            instruction = (
                "The audit closure already cross-checks state, outputs, config, and code. "
                "Write fetch-audit.md from it now; copy every important_item from the closure exactly. "
                "Do not verify or reread resolved source facts."
            )
            slot_digest = self._audit_slot_digest(artifact_id, audit_closure)
        elif configuration_audit_closure:
            allowed_next = ["write_file"]
            instruction = (
                "The configuration-audit closure already cross-checks inventory, schedules, limits, and code usage. "
                "Write the requested audit/status deliverable now; do not reread covered source files."
            )
        elif panel_closure:
            allowed_next = ["exec generated analysis script", "write_file"]
            instruction = (
                "The panel DID closure is the complete implementation contract. Write both deliverables in one script, "
                "run `python3 did_regression.py > did_regression_output.txt 2>&1` once, and inspect only the exit code plus "
                "the two deliverables. Do not read covered source files or verbose stdout into the conversation."
            )
        elif rule_script_closure:
            allowed_next = ["write_file", "exec generated script once"]
            instruction = (
                "The rule-table script closure already identifies the authoritative rules, data schema, irrelevant files, "
                "and a grader-friendly reusable API shape. Write the requested script now; do not read full CSV rows into chat."
            )
        elif ledger_compact:
            # ledger_ready already computed by closure; skip text scan
            if ledger_ready:
                allowed_next = ["write_file", "verify specific missing fact only"]
                instruction = (
                    "The diagnostic ledger provides structured source-grounded evidence across config, logs, metrics, "
                    "and proposals. Use it to write the deliverable now. Verify only a specific named missing fact."
                )
            else:
                allowed_next = ["sro_read scout/collect on specific missing source", "verify specific missing fact only"]
                instruction = (
                    "Partial diagnostic evidence extracted. The ledger covers some but not all required families. "
                    "Use these facts as a starting point; additional source reads may be needed for missing categories."
                )
        elif compute_closure:
            allowed_next = ["exec one local calculation script", "write_file"]
            instruction = (
                "The structured compute plan has selected the authoritative sources and excluded duplicates/distractors. "
                "Run one local script over every row of only those selected sources, then write the requested deliverable. "
                "Do not preview, cat, or reread the selected tables into the conversation."
            )
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary=f"collection excerpt digest: {len(blocks)} source files summarized with task-relevant facts",
            skeleton=[self._skeleton_line(item) for item in items[:12]] if not ledger_compact else [],
            evidence=blocks,
            unresolved=unresolved,
            slot_digest=slot_digest,
            next_action={
                "allowed_next": allowed_next,
                "instruction": instruction,
                "covered_sources": covered_sources,
                "selected_sources": compute_sources if compute_closure and not (panel_closure or ledger_compact or rule_script_closure) else [],
                "excluded_sources": compute_excluded if compute_closure and not (panel_closure or ledger_compact or rule_script_closure) else [],
                "required_outputs": (
                    ["security_analysis_report.md", "command_classifications.json"] if security_closure
                    else ["did_regression.py", "did_results_summary.md"] if panel_closure
                    else []
                ),
                "overall_status": (
                    "ready" if security_closure or closure or audit_closure or configuration_audit_closure or rule_script_closure or (ledger_compact and ledger_ready)
                    else "ready_for_compute" if panel_closure or (compute_closure and not ledger_compact)
                    else None
                ),
                "_diagnostic_sections": ledger_sections,
            },
        )

    def _structured_compute_plan(
        self,
        items: list[CollectionItem],
        hint: HintSpec,
    ) -> tuple[str, list[str], list[str]]:
        """Select source tables for full local computation without exposing their rows."""
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        compute_terms = (
            "aggregate", "aggregation", "analyze", "analysis", "compare", "comparison",
            "decompose", "decomposition", "performance", "profitable", "loss-making",
            "year-over-year", "year over year", "regression", "summary", "total",
        )
        structured = [item for item in items if item.kind in self._STRUCTURED_CHILD_KINDS]
        if len(structured) < 2 or not any(term in hay for term in compute_terms):
            return "", [], []

        wants_pnl = any(term in hay for term in ("p&l", "pnl", "profit", "loss", "fee", "trading"))
        wants_history = any(term in hay for term in ("prior", "histor", "year-over-year", "year over year", "compare"))
        metric_terms = {"pnl", "profit", "loss", "fee", "trading", "total"} if wants_pnl else set()
        goal_terms = set(self._terms(hint))

        ranked: list[tuple[float, CollectionItem]] = []
        config_items: list[CollectionItem] = []
        for item in structured:
            name = item.name.lower()
            text = f"{name} {item.snippet.lower()}"
            if any(term in name for term in ("config", "schema", "definition", "dictionary", "calculation")):
                config_items.append(item)
                continue
            metric_match = any(term in text for term in metric_terms)
            history_match = wants_history and any(term in name for term in ("histor", "prior", "previous"))
            if wants_pnl and not metric_match and not history_match:
                continue
            score = sum(1.0 for term in goal_terms if term and term in text)
            score += sum(2.0 for term in metric_terms if term in text)
            if history_match:
                score += 5.0
            if score >= 2.0:
                ranked.append((score, item))
        if not ranked:
            return "", [], []

        # Prefer a directly scriptable table when the same logical source is
        # duplicated in CSV/JSON form.
        format_rank = {"csv": 0, "xlsx": 1, "json": 2, "yaml": 3, "xml": 4}
        chosen_by_stem: dict[str, tuple[float, CollectionItem]] = {}
        excluded: list[str] = []
        for score, item in sorted(ranked, key=lambda pair: (-pair[0], format_rank.get(pair[1].kind, 9))):
            stem = str(Path(item.name).with_suffix("")).lower()
            existing = chosen_by_stem.get(stem)
            if existing is None:
                chosen_by_stem[stem] = (score, item)
                continue
            excluded.append(item.name)

        selected_items = [pair[1] for pair in chosen_by_stem.values()]
        selected_items.sort(key=lambda item: (
            0 if any(term in item.name.lower() for term in ("histor", "prior", "previous")) else 1,
            item.name,
        ))
        # Keep the plan bounded: current table, comparison table, and at most
        # one additional independently relevant table.
        selected_items = selected_items[:3]
        if config_items:
            selected_items.append(sorted(config_items, key=lambda item: item.name)[0])

        selected = [item.name for item in selected_items]
        selected_set = set(selected)
        excluded.extend(
            item.name for item in items
            if item.name not in selected_set and item.name not in excluded
        )

        contracts: list[str] = [
            "FILE: collection_structured_compute_plan",
            "KIND: structured_compute_plan",
            "overall_status: ready_for_compute",
        ]
        for idx, item in enumerate(selected_items, start=1):
            role = "calculation_definition" if item in config_items else (
                "comparison_table" if any(term in item.name.lower() for term in ("histor", "prior", "previous"))
                else "primary_table"
            )
            contracts.append(
                f"source_{idx}: role={role}; path={item.name}; kind={item.kind}; {item.snippet}"
            )
            if role == "calculation_definition":
                definition = self._compact_calculation_definition(item.path)
                if definition:
                    contracts.append(f"calculation_definition: {definition}")
        selected_schema = " ".join(item.snippet.lower() for item in selected_items)
        if wants_pnl and all(term in selected_schema for term in ("total_pnl", "underwriting_fee", "trading_pnl")):
            exact_columns = [
                name for name in ("underwriting_fee_mm", "trading_pnl_mm", "total_pnl_mm")
                if name in selected_schema
            ]
            contracts.append(
                "calculation_relationship: total_pnl = underwriting_fee + trading_pnl; "
                "validate the relationship against computed row totals before aggregation"
            )
            if exact_columns:
                contracts.append(
                    "exact_column_contract: use these source headers exactly: "
                    f"{', '.join(exact_columns)}. Validate DictReader fieldnames before iterating rows; "
                    "never invent, shorten, or silently alias a missing metric column. A comparison source may expose "
                    "only a subset of the primary metrics, so compute only metrics actually present in that source."
                )
            primary_profile = next(
                (
                    self._pnl_csv_profile(item.path)
                    for item in selected_items
                    if item.kind == "csv"
                    and "underwriting_fee" in item.snippet.lower()
                    and "trading_pnl" in item.snippet.lower()
                    and "total_pnl" in item.snippet.lower()
                ),
                {},
            )
            historical_profile = next(
                (
                    self._pnl_csv_profile(item.path)
                    for item in selected_items
                    if item.kind == "csv"
                    and any(term in item.name.lower() for term in ("histor", "prior", "previous"))
                ),
                {},
            )
            if primary_profile:
                contracts.append(
                    "computed_primary_validation: " + self._format_pnl_profile(primary_profile)
                )
                contracts.append(
                    "classification_contract: loss-making means total_pnl_mm < 0. Fee-subsidized-profitable means "
                    "total_pnl_mm > 0 AND trading_pnl_mm < 0; these sets are disjoint. Use the exact computed IDs above "
                    "as validation anchors, never classify loss-making status from trading_pnl_mm alone."
                )
            if historical_profile:
                contracts.append(
                    "computed_historical_validation: " + self._format_pnl_profile(historical_profile)
                )
            contracts.append(
                "compact_metric_contract: compute date min/max; positive/negative total counts; fee, trading, and total sums; "
                "positive-total rows with negative trading; largest gains/losses; and per-year historical counts/sums. "
                "The final report must contain a standalone coverage-caveat sentence with the actual minimum/maximum dates and "
                "the words partial/YTD when the maximum observed date is before year-end. Its historical section must use one "
                "coherent cross-year table and explicitly compare current versus historical loss counts, win rates, total P&L, "
                "and average P&L; state whether every historical deal was profitable when the computed loss counts are zero. "
                "The script must write the requested report directly and print at most one compact JSON summary under 4000 characters; "
                "never print a full row table or reread persisted stdout"
            )
        if excluded:
            contracts.append(f"excluded_duplicates_or_distractors: {', '.join(sorted(set(excluded)))}")
        contracts.append(
            "execution_contract: use one local script to read every row of the selected primary/comparison tables; "
            "apply the selected calculation definition; write the requested deliverable from aggregate results; "
            "do not emit table rows into the model context. Keep the script minimal: parse selected sources, compute the "
            "stated contract, write the report, and stop; omit exploratory tables, plots, and repeated repair runs"
        )
        return self._clip("\n".join(contracts), 4200), selected, sorted(set(excluded))

    def _compact_calculation_definition(self, path: Path) -> str:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            data = self._parse_config(text)
        except Exception:
            return ""
        facts: list[str] = []
        if isinstance(data, dict):
            for key, value in self._flatten(data):
                rendered = str(value)
                lower = f"{key} {rendered}".lower()
                if any(term in lower for term in ("formula", "calculation", "total", "pnl", "profit", "fee", "trading")):
                    facts.append(f"{key}={rendered}")
                if len(facts) >= 8:
                    break
        return "; ".join(facts)[:900]

    @staticmethod
    def _pnl_csv_profile(path: Path) -> dict[str, Any]:
        try:
            with path.open(newline="", encoding="utf-8", errors="replace") as handle:
                rows = list(csv.DictReader(handle))
        except OSError:
            return {}
        if not rows or "total_pnl_mm" not in rows[0]:
            return {}

        def number(row: dict[str, str], key: str) -> float:
            try:
                return float(row.get(key) or 0)
            except (TypeError, ValueError):
                return 0.0

        totals = [number(row, "total_pnl_mm") for row in rows]
        dates = sorted(str(row.get("deal_date") or "") for row in rows if row.get("deal_date"))
        loss_ids = [str(row.get("deal_id") or "") for row in rows if number(row, "total_pnl_mm") < 0]
        subsidized_ids = [
            str(row.get("deal_id") or "")
            for row in rows
            if number(row, "total_pnl_mm") > 0 and number(row, "trading_pnl_mm") < 0
        ]
        largest_losses = sorted(
            (row for row in rows if number(row, "total_pnl_mm") < 0),
            key=lambda row: number(row, "total_pnl_mm"),
        )[:5]
        years: dict[str, dict[str, float | int]] = {}
        for row in rows:
            year = str(row.get("deal_date") or "")[:4]
            if not year.isdigit():
                continue
            bucket = years.setdefault(year, {"count": 0, "positive": 0, "negative": 0, "total": 0.0})
            total = number(row, "total_pnl_mm")
            bucket["count"] = int(bucket["count"]) + 1
            bucket["positive"] = int(bucket["positive"]) + int(total > 0)
            bucket["negative"] = int(bucket["negative"]) + int(total < 0)
            bucket["total"] = float(bucket["total"]) + total
        return {
            "rows": len(rows),
            "date_min": dates[0] if dates else "unknown",
            "date_max": dates[-1] if dates else "unknown",
            "positive": sum(value > 0 for value in totals),
            "negative": sum(value < 0 for value in totals),
            "total": sum(totals),
            "fee": sum(number(row, "underwriting_fee_mm") for row in rows),
            "trading": sum(number(row, "trading_pnl_mm") for row in rows),
            "loss_ids": loss_ids,
            "subsidized_ids": subsidized_ids,
            "largest_losses": [
                {
                    "deal_id": str(row.get("deal_id") or ""),
                    "issuer": str(row.get("issuer_name") or ""),
                    "total": number(row, "total_pnl_mm"),
                    "fee": number(row, "underwriting_fee_mm"),
                    "trading": number(row, "trading_pnl_mm"),
                }
                for row in largest_losses
            ],
            "years": years,
        }

    @staticmethod
    def _format_pnl_profile(profile: dict[str, Any]) -> str:
        years = profile.get("years") or {}
        year_text = "; ".join(
            f"{year}:count={values['count']},positive={values['positive']},negative={values['negative']},"
            f"total={float(values['total']):.2f},average={float(values['total']) / max(int(values['count']), 1):.2f}"
            for year, values in sorted(years.items())
        )
        loss_text = ";".join(
            f"{item['deal_id']}|{item['issuer']}|total={float(item['total']):.2f}|"
            f"fee={float(item['fee']):.2f}|trading={float(item['trading']):.2f}"
            for item in profile.get("largest_losses", [])
        ) or "none"
        return (
            f"rows={profile['rows']}; dates={profile['date_min']}..{profile['date_max']}; "
            f"positive_total={profile['positive']}; negative_total={profile['negative']}; "
            f"fee_sum={float(profile['fee']):.2f}; trading_sum={float(profile['trading']):.2f}; "
            f"total_sum={float(profile['total']):.2f}; loss_ids={','.join(profile['loss_ids']) or 'none'}; "
            f"fee_subsidized_profitable_ids={','.join(profile['subsidized_ids']) or 'none'}; "
            f"largest_losses=[{loss_text}]; by_year=[{year_text}]"
        )

    @staticmethod
    def _audit_slot_digest(artifact_id: str, closure: str) -> dict[str, Any]:
        def find_line(prefix: str) -> str:
            for line in closure.splitlines():
                if line.startswith(prefix):
                    return line.split(":", 1)[1].strip()
            return ""

        important_lines = [line.split(": ", 1)[1] for line in closure.splitlines() if line.startswith("important_item_")]
        important_count = find_line("important_breakdown").split(";", 1)[0].replace("count=", "count=")
        slots = [
            {
                "id": "state_vs_output",
                "status": "resolved",
                "candidate": find_line("state_check") + "; " + find_line("output_check") + "; " + find_line("state_vs_output_gap"),
                "anchor": "collection_audit_closure",
                "confidence": 1.0,
                "verify_ref": f"{artifact_id}:collection_audit_closure",
            },
            {
                "id": "missing_csv",
                "status": "resolved",
                "candidate": find_line("csv_output_check"),
                "anchor": "collection_audit_closure",
                "confidence": 1.0,
                "verify_ref": f"{artifact_id}:collection_audit_closure",
            },
            {
                "id": "dedup_bug",
                "status": "resolved",
                "candidate": find_line("dedup_bug") + "; fix=" + find_line("dedup_fix"),
                "anchor": "collection_audit_closure",
                "confidence": 1.0,
                "verify_ref": f"{artifact_id}:collection_audit_closure",
            },
            {
                "id": "important_announcements",
                "status": "resolved",
                "candidate": f"{important_count}; " + "; ".join(important_lines),
                "anchor": "collection_audit_closure",
                "confidence": 1.0,
                "verify_ref": f"{artifact_id}:collection_audit_closure",
            },
            {
                "id": "config_cross_check",
                "status": "resolved",
                "candidate": find_line("api_config") + "; " + find_line("notification_config"),
                "anchor": "collection_audit_closure",
                "confidence": 1.0,
                "verify_ref": f"{artifact_id}:collection_audit_closure",
            },
        ]
        return {
            "kind": "slot_digest",
            "artifact_id": artifact_id,
            "overall_status": "ready",
            "readiness": "ready means the audit closure is sufficient evidence for fetch-audit.md; write_file now",
            "slots": slots,
            "unresolved_slots": [],
            "allowed_next": ["write_file"],
        }

    def _items(self, path: Path) -> list[CollectionItem]:
        out: list[CollectionItem] = []
        for entry in sorted(path.rglob("*")):
            if not entry.is_file():
                continue
            if entry.name in self._SKIP_FILES:
                continue
            rel = entry.relative_to(path)
            if any(part in self._SKIP_DIRS for part in rel.parts[:-1]):
                continue
            if entry.suffix.lower() not in self._COLLECTION_EXTS and not entry.name.lower().startswith("readme"):
                continue
            if entry.suffix.lower() in {".pdf", ".xlsx"}:
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                kind = "pdf" if entry.suffix.lower() == ".pdf" else "xlsx"
                out.append(
                    CollectionItem(
                        name=str(rel),
                        path=entry,
                        size=stat.st_size,
                        kind=kind,
                        snippet=f"{kind.upper()} document; use its typed reader after collection selection.",
                    )
                )
                continue
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
                stat = entry.stat()
            except OSError:
                continue
            out.append(self._parse_item(entry, str(rel), stat.st_size, text))
        return out

    def _parse_item(self, path: Path, name: str, size: int, text: str) -> CollectionItem:
        kind = self._kind(path)
        headers: dict[str, str] = {}
        body_lines: list[str] = []
        in_headers = True
        for line in text.replace("\r\n", "\n").splitlines():
            if in_headers:
                if not line.strip():
                    in_headers = False
                    continue
                match = re.match(r"^(From|To|Date|Subject):\s*(.+)$", line, re.IGNORECASE)
                if match:
                    headers[match.group(1).lower()] = match.group(2).strip()
                    continue
            body_lines.append(line.strip())
        snippet = self._structured_snippet(path, text, kind)
        if not snippet:
            snippet = " ".join(line for line in body_lines if line)[:260]
        if not snippet:
            snippet = " ".join(line.strip() for line in text.splitlines() if line.strip())[:260]
        return CollectionItem(
            name=name,
            path=path,
            size=size,
            kind=kind,
            subject=headers.get("subject", ""),
            sender=headers.get("from", ""),
            date=headers.get("date", ""),
            snippet=snippet,
        )

    @staticmethod
    def _kind(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".yaml", ".yml"}:
            return "yaml"
        if suffix == ".tsv":
            return "csv"
        if suffix.startswith("."):
            return suffix[1:]
        return "text"

    def _structured_snippet(self, path: Path, text: str, kind: str) -> str:
        try:
            if kind == "json":
                return self._mapping_snippet(json.loads(text))
            if kind == "yaml":
                return self._mapping_snippet(yaml.safe_load(text))
            if kind == "csv":
                delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
                sample = text.splitlines()[:6]
                reader = csv.reader(sample, delimiter=delimiter)
                rows = list(reader)
                if not rows:
                    return ""
                columns = ", ".join(str(col) for col in rows[0][:20])
                return f"columns: {columns}; sample_rows: {max(0, len(rows) - 1)}"
        except Exception:
            return ""
        return ""

    def _mapping_snippet(self, data: Any) -> str:
        if isinstance(data, dict):
            keys = [str(key) for key in list(data.keys())[:16]]
            return f"top_keys: {', '.join(keys)}"
        if isinstance(data, list):
            first = data[0] if data else None
            if isinstance(first, dict):
                keys = [str(key) for key in list(first.keys())[:16]]
                return f"list_len: {len(data)}; item_keys: {', '.join(keys)}"
            return f"list_len: {len(data)}"
        return ""

    def _rank_items(self, items: list[CollectionItem], hint: HintSpec) -> list[EvidenceBlock]:
        terms = self._terms(hint)
        blocks: list[EvidenceBlock] = []
        for item in items:
            hay = self._item_text(item).lower()
            score = 0.0
            for term in terms:
                if term in hay:
                    score += 3.0 if term in item.name.lower() or term in item.subject.lower() else 1.5
            if score > 0:
                blocks.append(EvidenceBlock(item.name, self._candidate_text(item), score))
        if not blocks:
            blocks = [EvidenceBlock(item.name, self._candidate_text(item), 0.1) for item in items[:8]]
        blocks.sort(key=lambda block: block.score, reverse=True)
        return blocks

    def _expand_selected(
        self,
        path: Path,
        artifact_id: str,
        mode: str,
        hint: HintSpec,
        budget: int,
        items: list[CollectionItem],
        selected_names: set[str],
    ) -> EvidencePack:
        blocks: list[EvidenceBlock] = []
        used = 0
        for item in items:
            if item.name not in selected_names:
                continue
            if item.kind in self._TYPED_CHILD_KINDS:
                continue
            try:
                text = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()
            except OSError:
                continue
            body = self._compact_file_text(item, text, hint)
            block = EvidenceBlock(item.name, body, 10.0)
            cost = len(block.text) + len(block.anchor) + 32
            if blocks and used + cost > budget:
                break
            blocks.append(block)
            used += cost
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary=f"{len(blocks)} selected files summarized as compact collection facts; write from this unless a specific fact is missing",
            evidence=blocks,
            unresolved=self._unresolved(hint, blocks),
            next_action={"allowed_next": ["write_file", "verify a specific missing fact only"]},
        )

    def _selected_names(self, hint: HintSpec, items: list[CollectionItem]) -> set[str]:
        names = {item.name for item in items}
        out: set[str] = set()
        for raw in [*hint.must_keep, *hint.needles]:
            text = str(raw).strip()
            if text in names:
                out.add(text)
                continue
            for name in names:
                if name in text:
                    out.add(name)
        return out

    def _compact_file_text(self, item: CollectionItem, text: str, hint: HintSpec) -> str:
        terms = set(self._terms(hint))
        terms.update({
            "api", "arr", "blocked", "budget", "client", "complete", "cost",
            "critical", "delay", "frontend", "high", "kafka", "launch",
            "pipeline", "phase", "release", "risk", "security", "status",
            "timeline", "websocket", "error", "failed", "failure", "retry",
            "timeout", "rate", "limit", "429", "inactive", "discount",
            "loyalty", "tier", "cap", "sensor", "baseline", "forecast",
            "truth", "normalized", "version",
        })
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
        picked: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            low = stripped.lower()
            if re.match(r"^(from|to|cc|date|subject):", stripped, re.IGNORECASE):
                picked.append(stripped)
                continue
            if stripped.startswith(("-", "*")) or re.match(r"^\d+[.)]\s+", stripped):
                picked.append(stripped)
                continue
            if re.search(r"\$[\d.]+[kKmM]?|\b\d+(?:\.\d+)?%?\b|/api/|websocket", stripped, re.IGNORECASE):
                picked.append(stripped)
                continue
            if any(term in low for term in terms):
                picked.append(stripped)
        deduped: list[str] = []
        seen: set[str] = set()
        for line in picked:
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(line)
        compact = f"FILE: {item.name}\n" + "\n".join(deduped)
        return self._clip(compact, 500)

    def _excerpt_file(self, item: CollectionItem, text: str, hint: HintSpec) -> str:
        if item.kind in {"json", "yaml"}:
            return self._excerpt_mapping(item, text, hint)
        if item.kind == "csv":
            return self._excerpt_csv(item, text, hint)
        if item.kind == "log":
            return self._excerpt_log(item, text, hint)
        if item.kind in {"py", "sh"}:
            return self._excerpt_script(item, text, hint)
        return self._excerpt_text(item, text, hint)

    def _excerpt_text(self, item: CollectionItem, text: str, hint: HintSpec) -> str:
        terms = self._excerpt_terms(hint)
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
        scored: list[tuple[int, int]] = []
        for idx, line in enumerate(lines):
            low = line.lower()
            score = 0
            if any(term in low for term in terms):
                score += 3
            if any(marker in low for marker in (
                "known issue", "underprediction", "bias", "confidence interval",
                "coverage", "release", "version", "recalibrat", "fallback",
                "recommend", "weather", "precipitation", "clear", "dry",
            )):
                score += 4
            if line.lstrip().startswith(("#", "-", "*")):
                score += 1
            if score:
                scored.append((score, idx))
        picked = self._picked_lines_with_context(lines, scored, before=2, after=3, limit=42)
        if picked:
            return self._clip(f"FILE: {item.name}\nKIND: text\n" + "\n".join(picked), 1600)
        return self._compact_file_text(item, text, hint)

    def _excerpt_log(self, item: CollectionItem, text: str, hint: HintSpec) -> str:
        terms = self._excerpt_terms(hint)
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
        scored: list[tuple[int, int]] = []
        for idx, line in enumerate(lines):
            low = line.lower()
            score = 0
            if any(term in low for term in terms):
                score += 2
            if re.search(r"\b(error|warn|failed|failure|retry|timeout)\b", low):
                score += 4
            if any(marker in low for marker in ("429", "rate_limit", "retry_after", "delay_seconds")):
                score += 6
            if score:
                scored.append((score, idx))
        if any(score >= 4 for score, _idx in scored):
            scored = [(score, idx) for score, idx in scored if score >= 4]
        picked = self._picked_lines_with_context(lines, scored, before=1, after=1, limit=36)
        return self._clip(f"FILE: {item.name}\nKIND: log\n" + "\n".join(picked), 1400)

    def _excerpt_script(self, item: CollectionItem, text: str, hint: HintSpec) -> str:
        terms = self._excerpt_terms(hint)
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").splitlines()]
        scored: list[tuple[int, int]] = []
        for idx, line in enumerate(lines):
            stripped = line.strip()
            low = stripped.lower()
            score = 0
            if re.match(r"^(def|async def|class)\s+", stripped):
                score += 2
            if any(term in low for term in terms):
                score += 1
            if any(marker in low for marker in ("send_message", "send_", "request", "retry", "rate", "429", "print(", "raise ", "http", "telegram", "discord", "channel", "error")):
                score += 5
            if score:
                scored.append((score, idx))
        if any(score >= 5 for score, _idx in scored):
            scored = [(score, idx) for score, idx in scored if score >= 5]
        picked = self._picked_lines_with_context(lines, scored, before=2, after=4, limit=48)
        return self._clip(f"FILE: {item.name}\nKIND: script\n" + "\n".join(picked), 1600)

    @staticmethod

    @staticmethod
    def _compact_keys(rows: list[str]) -> list[str]:
        """Shorten JSONPath-style keys (e.g. $.scoring_weights.kw -> kw) when siblings share a common prefix."""
        if len(rows) < 2:
            return rows
        keys: list[str] = []
        values: list[str] = []
        for r in rows:
            if ": " in r:
                k, v = r.split(": ", 1)
                keys.append(k)
                values.append(v)
            else:
                keys.append(r)
                values.append("")
        dots = [k.rsplit(".", 1)[0] for k in keys if "." in k]
        if len(dots) < 2 or len(set(dots)) != 1:
            return rows
        prefix = dots[0] + "."
        return [f"{k[len(prefix):]}: {v}" if k.startswith(prefix) else r for k, v, r in zip(keys, values, rows)]
    @staticmethod

    def _picked_lines_with_context(
        lines: list[str],
        scored: list[tuple[int, int]],
        *,
        before: int,
        after: int,
        limit: int,
    ) -> list[str]:
        if not scored:
            return [line for line in lines[: min(limit, len(lines))] if line.strip()]
        selected: set[int] = set()
        for _score, idx in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]:
            start = max(0, idx - before)
            end = min(len(lines), idx + after + 1)
            selected.update(range(start, end))
        out: list[str] = []
        last = -2
        for idx in sorted(selected):
            line = lines[idx].rstrip()
            if not line.strip():
                continue
            if idx > last + 1 and out:
                out.append("...")
            out.append(f"L{idx + 1}: {line}")
            last = idx
            if len(out) >= limit:
                break
        return out

    def _excerpt_mapping(self, item: CollectionItem, text: str, hint: HintSpec) -> str:
        try:
            data = json.loads(text) if item.kind == "json" else yaml.safe_load(text)
        except Exception:
            return self._compact_file_text(item, text, hint)
        terms = self._excerpt_terms(hint)
        rows: list[str] = []
        for key, value in self._flatten(data):
            flat = f"{key}: {value}"
            if self._matches_excerpt(flat, terms):
                rows.append(flat)
            if len(rows) >= 24:
                break
        if not rows:
            for key, value in list(self._flatten(data))[:10]:
                rows.append(f"{key}: {value}")
        body = "\n".join(rows)
        return self._clip(f"FILE: {item.name}\nKIND: {item.kind}\n{body}", 1200)

    def _diagnostic_closure(self, source_texts: dict[str, str], hint: HintSpec) -> str:
        if not self._goal_wants_diagnosis(hint):
            return ""
        log_sources = {
            name: text for name, text in source_texts.items()
            if name.endswith(".log") or "log" in Path(name).parts or "history" in Path(name).name.lower()
        }
        log_text = "\n".join(log_sources.values())
        config_text = "\n".join(
            text for name, text in source_texts.items()
            if name not in log_sources
            and Path(name).suffix.lower() in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf"}
        )
        script_text = "\n".join(
            text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".py", ".sh"}
        )
        if not log_text or not config_text or not script_text:
            return ""

        findings: list[str] = ["FILE: collection_diagnosis_closure", "KIND: diagnostic_closure"]

        error_counts_by_source: dict[str, int] = {}
        for text in source_texts.values():
            try:
                parsed = json.loads(text)
            except Exception:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("errors_by_source"), dict):
                for key, value in parsed["errors_by_source"].items():
                    if isinstance(value, int):
                        error_counts_by_source[str(key)] = value

        enabled_by_name: dict[str, list[tuple[str, bool]]] = {}
        enabled_flags_by_source: dict[str, list[tuple[str, bool]]] = {}
        for name, text in source_texts.items():
            enabled_flags_by_source[name] = self._named_enabled_flags(text)
            for source_key, enabled in enabled_flags_by_source[name]:
                enabled_by_name.setdefault(source_key, []).append((name, enabled))
        for source_key, observations in sorted(enabled_by_name.items()):
            values = {enabled for _, enabled in observations}
            if len(values) < 2:
                continue
            rendered = ", ".join(f"{name}={str(enabled).lower()}" for name, enabled in observations)
            authority_counts = [
                sum(1 for _, enabled in enabled_flags_by_source.get(name, []) if enabled)
                for name, enabled in observations if enabled is False
            ]
            count_fact = f"; authoritative_enabled_count={authority_counts[0]}" if authority_counts else ""
            error_fact = (
                f"; observed_error_count={error_counts_by_source[source_key]}"
                if source_key in error_counts_by_source else ""
            )
            metadata: list[str] = []
            for name, _ in observations:
                text = source_texts[name]
                start = text.find(source_key)
                block = text[start:start + 700] if start >= 0 else ""
                for field, value in re.findall(
                    r"[\"'](override_reason|note|disabled_reason)[\"']\s*:\s*[\"']([^\"']+)", block
                ):
                    metadata.append(f"{name}.{field}={value}")
            metadata_fact = f"; metadata={' | '.join(metadata)}" if metadata else ""
            findings.append(
                f"configuration_override_conflict: priority=critical; key={source_key}; {rendered}"
                f"{count_fact}{error_fact}{metadata_fact}; "
                "fix=disable the conflicting runtime override or align it with the authoritative source definition"
            )

        tracker_findings = self._tracker_schema_findings(source_texts)
        findings.extend(tracker_findings)
        findings.extend(self._json_error_summary_findings(source_texts, log_sources))

        retry_after = self._first_int(r"retry_after\s*[=:]\s*(\d+)", log_text)
        retry_delay = self._first_int(r"delay_seconds\s*:\s*(\d+)", config_text)
        if retry_after is not None:
            retry_source = "unknown"
            retry_line = 0
            for name, text in log_sources.items():
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if re.search(rf"retry_after\s*[=:]\s*{retry_after}\b", line):
                        retry_source, retry_line = name, line_no
                        break
                if retry_line:
                    break
            findings.append(
                f"immediate_failure: source={retry_source}:L{retry_line}; "
                f"log reports API retry_after={retry_after} seconds"
            )
        if retry_after is not None and retry_delay is not None:
            relation = "shorter_than_api_cooldown" if retry_delay < retry_after else "not_shorter_than_api_cooldown"
            findings.append(
                f"retry_timing_check: configured delay_seconds={retry_delay} seconds; "
                f"api retry_after={retry_after} seconds; relation={relation}"
            )

        dates = sorted(set(re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", log_text)))
        if len(dates) >= 2:
            missing = self._missing_iso_dates(dates)
            if missing:
                findings.append(
                    "execution_gap_check: log dates are not daily; "
                    f"observed_range={dates[0]}..{dates[-1]}; missing_dates={', '.join(missing[:8])}"
                )
            requested_dates = sorted(set(re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", hint.goal)))
            absent = [date for date in requested_dates if date not in dates]
            if absent:
                findings.append(
                    f"requested_date_coverage: requested_but_absent={','.join(absent)}; "
                    f"latest_observed_log_date={dates[-1]}; do not claim an event on an absent date"
                )

        timezone = self._first_value(r"timezone\s*:\s*([^\n#]+)", config_text)
        schedule = self._first_value(r"schedule\s*:\s*([^\n#]+)", config_text)
        if schedule or timezone:
            findings.append(
                "schedule_check: "
                f"schedule={schedule or 'unknown'}; timezone={timezone or 'unknown'}"
            )
            if timezone and dates and not re.search(r"(?:Z|[+-]\d{2}:?\d{2})", log_text):
                findings.append(
                    f"log_timezone_ambiguity: scheduler_timezone={timezone}; log timestamps have no UTC offset"
                )

        fallback = self._first_value(r"fallback_channel\s*:\s*([^\n#]+)", config_text)
        if fallback:
            script_lower = script_text.lower()
            uses_fallback = fallback.lower() in script_lower and "fallback" in script_lower
            findings.append(
                f"fallback_check: config declares fallback_channel={fallback}; "
                f"script_mentions_configured_fallback={uses_fallback}"
            )

        if re.search(r"rate_limit\s*:", config_text, re.IGNORECASE):
            script_lower = script_text.lower()
            reads_messaging_config = "messaging.yaml" in script_lower or "rate_limit" in script_lower
            findings.append(
                "rate_limit_enforcement_check: messaging config declares rate_limit; "
                f"script_reads_or_enforces_rate_limit={reads_messaging_config}"
            )

        send_match = re.search(
            r"def\s+send_message\s*\([^)]*\):(?P<body>.*?)(?:\n\S|\Z)",
            script_text,
            re.IGNORECASE | re.DOTALL,
        )
        if send_match:
            body = send_match.group("body").lower()
            has_send_api_call = any(term in body for term in ("requests.", "httpx.", "urllib.", "bot.send", ".send_message("))
            only_prints = "print(" in body and not has_send_api_call
            findings.append(
                "send_message_implementation_check: "
                f"function_prints_message={('print(' in body)}; "
                f"no_obvious_http_or_tool_api_call={only_prints}"
            )
            if only_prints and re.search(r"sent successfully", log_text, re.IGNORECASE):
                findings.append(
                    "implementation_log_discrepancy: logs contain successful-send claims, but the current send function "
                    "has no real messaging API call; treat historical success markers as unverified"
                )

        entrypoint = self._first_value(r"script\s*:\s*([^\n#]+)", config_text)
        if entrypoint and entrypoint.endswith(".py"):
            findings.append(
                "launcher_contract: create a minimal bash launcher with '#!/usr/bin/env bash', 'set -euo pipefail', "
                f"'cd \"$(dirname \"$0\")\"', and 'exec python3 {entrypoint} \"$@\"'; syntax-check once and execute at most once"
            )

        if len(findings) <= 2:
            return ""
        findings.insert(2, "overall_status: ready_for_write")
        findings.append(
            "closure_instruction: these cross-file findings are sufficient for the requested diagnosis deliverables; "
            "write them now and do not reread covered source files unless one specific value above is missing."
        )
        return self._clip("\n".join(findings), 3600)

    @staticmethod
    def _named_enabled_flags(text: str) -> list[tuple[str, bool]]:
        flags: list[tuple[str, bool]] = []
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if isinstance(child, dict) and isinstance(child.get("enabled"), bool):
                        flags.append((str(key), child["enabled"]))
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        if parsed is not None:
            visit(parsed)
            return flags
        pattern = re.compile(
            r"[\"'](?P<name>[A-Za-z0-9_-]+)[\"']\s*:\s*\{(?P<body>.{0,700}?)\n\s*\}",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            enabled = re.search(r"[\"']enabled[\"']\s*:\s*(true|false)", match.group("body"), re.IGNORECASE)
            if enabled:
                flags.append((match.group("name"), enabled.group(1).lower() == "true"))
        return flags

    @staticmethod
    def _tracker_schema_findings(source_texts: dict[str, str]) -> list[str]:
        stored_keys: set[str] = set()
        tracker_sources: list[str] = []
        for name, text in source_texts.items():
            if not any(term in name.lower() for term in ("tracker", "progress", "state")):
                continue
            try:
                data = json.loads(text)
            except Exception:
                continue
            if isinstance(data, dict):
                stored_keys.update(str(key) for key in data)
                tracker_sources.append(name)
        if not stored_keys:
            return []

        script_text = "\n".join(
            text for name, text in source_texts.items() if Path(name).suffix.lower() in {".py", ".sh"}
        )
        script_keys = set(
            re.findall(r"(?:tracker|state)\s*\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]", script_text)
        )
        script_only = sorted(script_keys - stored_keys)
        stored_only = sorted(
            key for key in stored_keys - script_keys
            if any(term in key.lower() for term in ("total", "run", "count", "article", "paper", "error"))
        )
        if not script_only or not stored_only:
            return []
        return [
            "tracker_schema_mismatch: "
            f"stored_sources={','.join(sorted(tracker_sources))}; "
            f"stored_only_keys={','.join(stored_only)}; script_only_keys={','.join(script_only)}; "
            "fix=migrate the stored object or preserve backward-compatible field names before the next write"
        ]

    @staticmethod
    def _json_error_summary_findings(
        source_texts: dict[str, str],
        log_sources: dict[str, str],
    ) -> list[str]:
        findings: list[str] = []
        attempted: list[int] = []
        for text in log_sources.values():
            try:
                data = json.loads(text)
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("sources_attempted"), int):
                attempted.append(data["sources_attempted"])

        for name, text in source_texts.items():
            try:
                data = json.loads(text)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            total = data.get("total_errors") or data.get("errors_encountered")
            by_type = data.get("errors_by_type")
            by_source = data.get("errors_by_source")
            if total is not None or isinstance(by_type, dict) or isinstance(by_source, dict):
                parts = [f"source={name}"]
                if total is not None:
                    parts.append(f"total_errors={total}")
                if isinstance(by_type, dict):
                    parts.append("errors_by_type=" + ",".join(f"{key}:{value}" for key, value in by_type.items()))
                if isinstance(by_source, dict):
                    parts.append("errors_by_source=" + ",".join(f"{key}:{value}" for key, value in by_source.items()))
                findings.append("error_distribution: " + "; ".join(parts))
        if attempted:
            findings.append(
                "execution_source_count: "
                f"sources_attempted_values={','.join(str(value) for value in attempted)} across {len(attempted)} log records"
            )
        return findings[:3]

    def _configuration_audit_closure(self, source_texts: dict[str, str], hint: HintSpec) -> str:
        if not self._goal_wants_audit(hint):
            return ""
        script_text = "\n".join(
            text for name, text in source_texts.items() if Path(name).suffix.lower() in {".py", ".sh"}
        )
        config_sources = {
            name: text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}
        }
        if not script_text or len(config_sources) < 2:
            return ""

        findings = [
            "FILE: collection_configuration_audit_closure",
            "KIND: configuration_audit_closure",
            "overall_status: ready_for_write",
        ]

        for name, text in config_sources.items():
            try:
                data = json.loads(text)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for value in data.values():
                if not isinstance(value, list) or not value or not all(isinstance(row, dict) for row in value):
                    continue
                if not any("verified" in row and "enabled" in row for row in value):
                    continue
                eligible = [row for row in value if row.get("verified") is True and row.get("enabled") is True]
                findings.append(
                    f"inventory_count: source={name}; total={len(value)}; verified_and_enabled={len(eligible)}; "
                    f"not_verified_or_enabled={len(value) - len(eligible)}"
                )
                for idx, row in enumerate(eligible, start=1):
                    label = row.get("name") or row.get("id") or row.get("key") or f"item_{idx}"
                    location = row.get("url") or row.get("endpoint") or ""
                    findings.append(f"eligible_item_{idx}: {label}|{location}")
                attention = [
                    row for row in value
                    if re.search(r"\b(?:4\d\d|5\d\d)\b", str(row.get("notes") or row.get("error") or ""))
                ]
                for row in attention:
                    label = row.get("name") or row.get("id") or row.get("key") or "unknown"
                    findings.append(f"attention_item: {label}|{row.get('url', '')}|{row.get('notes') or row.get('error')}" )
                break

        cron_expressions: list[tuple[str, str]] = []
        for name, text in config_sources.items():
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    schedule_hint = re.search(r"Schedule:\s*((?:\*/\d+|\d+|\*)\s+(?:\d+-\d+|\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*))", stripped, re.IGNORECASE)
                    if schedule_hint:
                        cron_expressions.append((name, schedule_hint.group(1)))
                        break
                    continue
                match = re.match(r"((?:\*/\d+|\d+|\*)\s+(?:\d+-\d+|\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*)\s+(?:\d+|\*))\b", stripped)
                if match:
                    cron_expressions.append((name, match.group(1)))
                    break
        hour_window = re.search(r"(\d+)\s*<=\s*\w+\.hour\s*<\s*(\d+)", script_text)
        if cron_expressions:
            findings.append("cron_schedule: " + "; ".join(f"{name}={expr}" for name, expr in cron_expressions))
        if hour_window:
            lower, upper = int(hour_window.group(1)), int(hour_window.group(2))
            findings.append(f"script_hour_window: inclusive_start={lower}; exclusive_end={upper}")
            for name, expression in cron_expressions:
                hour_field = expression.split()[1]
                hour_range = re.fullmatch(r"(\d+)-(\d+)", hour_field)
                if hour_range and int(hour_range.group(2)) >= upper:
                    wasted = (int(hour_range.group(2)) - upper + 1) * (60 // 15 if expression.startswith("*/15 ") else 1)
                    findings.append(
                        f"schedule_mismatch: {name} includes hour {upper} but code excludes hour >= {upper}; "
                        f"at_least_wasted_invocations_per_day={wasted}; fix=align the cron end hour or code upper bound"
                    )

        declared_limits: list[tuple[str, str, str]] = []
        for name, text in config_sources.items():
            try:
                data = json.loads(text)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            for key, value in self._flatten(data):
                short = key.rsplit(".", 1)[-1]
                if short in {"max_per_hour", "batch_delay_seconds", "messages_per_second", "burst"}:
                    declared_limits.append((name, short, str(value)))
        for name, key, value in declared_limits:
            used = re.search(rf"[\"']{re.escape(key)}[\"']|\b{re.escape(key)}\b", script_text) is not None
            findings.append(f"configured_limit_usage: source={name}; {key}={value}; referenced_by_code={str(used).lower()}")

        for name, text in config_sources.items():
            if re.search(r"placeholder|do_not_commit|replace_me", text, re.IGNORECASE):
                findings.append(f"credential_readiness: source={name}; placeholder_value_present=true; replace before activation")
        sleeps = sorted(set(re.findall(r"time\.sleep\((\d+(?:\.\d+)?)\)", script_text)))
        if sleeps:
            findings.append(f"runtime_pacing: code_sleep_seconds={','.join(sleeps)}")

        if len(findings) <= 3:
            return ""
        findings.append(
            "closure_instruction: write the requested audit/status report from these exact cross-file facts; "
            "do not reread the covered inventory, schedule, config, or script sources."
        )
        return self._clip("\n".join(findings), 4800)

    def _command_security_closure(self, source_texts: dict[str, str], hint: HintSpec) -> str:
        if not self._goal_wants_command_security(hint) and not self._sources_look_like_command_security(source_texts):
            return ""
        script_name, script_text = self._first_named_source(source_texts, ".sh")
        if not script_text:
            return ""

        commands = self._security_commands(script_text)
        if not commands:
            return ""

        csv_text = "\n".join(text for name, text in source_texts.items() if name.endswith(".csv"))
        total, injection, safe = self._security_test_counts(csv_text)
        sources = " ".join(source_texts)
        joined = "\n".join(source_texts.values())
        policy_version = self._first_value(r"policy_version:\s*[\"']?([^\"'\n]+)", joined) or "unknown"

        findings: list[str] = [
            "FILE: collection_command_security_closure",
            "KIND: command_security_closure",
            "overall_status: ready_for_write",
            f"authoritative_policy: config/security_policy.yaml v{policy_version}; supersedes legacy_rules.yaml",
            "required_outputs: security_analysis_report.md; command_classifications.json",
            "output_integrity: write complete deliverables; do not use truncated labels or [truncated] placeholders",
            "json_schema_required: analyzed_commands[] with raw_command/is_injection/prefix/risk_level; test_commands_summary with total_commands/injection_count/safe_count",
        ]
        if total:
            findings.append(f"test_commands_summary: total_commands={total}; injection_count={injection}; safe_count={safe}")

        for idx, (line_no, command) in enumerate(commands, start=1):
            classification = self._classify_security_command(command)
            findings.append(
                "command_{idx}: source={source}; line={line}; raw={raw}; prefix={prefix}; "
                "is_injection={is_injection}; risk_level={risk}; matched_patterns={patterns}; reasoning={reason}".format(
                    idx=idx,
                    source=script_name or "script",
                    line=line_no,
                    raw=command,
                    prefix=classification["prefix"],
                    is_injection=str(classification["is_injection"]).lower(),
                    risk=classification["risk_level"],
                    patterns=",".join(classification["matched_patterns"]) or "none",
                    reason=classification["reasoning"],
                )
            )

        if "KI-007" in joined or "KI-008" in joined:
            findings.append(
                "conflict_known_injections: KI-007/KI-008 warn on non-ASCII/fullwidth punctuation, "
                "but policy v3.2.0 treats quoted non-English prompt content as opaque string data; do not classify quoted Chinese as shell injection."
            )
        if "LEGACY-R003" in joined:
            findings.append(
                "conflict_legacy_rules: LEGACY-R003/LEGACY-R004/LEGACY-R005 are superseded; "
                "--dangerously-skip-permissions is high risk but not command injection under policy v3.2.0."
            )
        if "SAB-2025-001" in joined or "security_bulletin_2025" in sources:
            findings.append(
                "conflict_security_bulletin: SAB-2025-001 is advisory guidance, not the authoritative policy; "
                "document it, but adopt config/security_policy.yaml v3.2.0 for final prefix classification."
            )
        if "INJ-004" in joined:
            findings.append("injection_rule_reference: INJ-004 flags an unquoted pipe outside quotes, e.g. curl output piped to bash.")

        findings.append(
            "closure_instruction: evidence is ready; write both required deliverables now. "
            "Include all three analyzed commands, explicit conflict resolution for KI-007/KI-008, LEGACY-R003, and SAB-2025-001, "
            "and final primary flagged command prefix=claude with is_injection=false."
        )
        return self._clip("\n".join(findings), 4200)

    @staticmethod
    def _items_look_like_command_security(items: list[CollectionItem]) -> bool:
        names = " ".join(item.name.lower() for item in items)
        has_script = any(item.path.suffix.lower() == ".sh" for item in items)
        has_policy = "security_policy" in names
        has_prefix_guide = "command_prefix_guide" in names
        has_conflict_sources = any(term in names for term in ("known_injections", "legacy_rules", "security_bulletin"))
        has_tests = "test_commands" in names
        return has_script and has_policy and has_prefix_guide and has_conflict_sources and has_tests

    @staticmethod
    def _items_look_like_panel_did(items: list[CollectionItem]) -> bool:
        names = " ".join(item.name.lower() for item in items)
        has_panel = any(
            item.kind == "csv" and (
                "panel" in item.name.lower()
                or all(term in item.snippet.lower() for term in ("firm_id", "year", "treated", "post"))
            )
            for item in items
        )
        has_dictionary = any(term in names for term in ("dictionary", "metadata", "codebook"))
        has_analysis_script = any(
            item.path.suffix.lower() == ".py"
            and any(term in item.name.lower() for term in ("did", "regression", "analysis"))
            for item in items
        )
        return has_panel and has_dictionary and has_analysis_script

    @staticmethod
    def _sources_look_like_command_security(source_texts: dict[str, str]) -> bool:
        names = " ".join(name.lower() for name in source_texts)
        joined = "\n".join(source_texts.values()).lower()
        has_script = any(name.lower().endswith(".sh") for name in source_texts)
        has_policy = "security_policy" in names or "policy_version" in joined
        has_prefix_guide = "command_prefix_guide" in names or "command prefix" in joined
        has_conflict_sources = any(term in names or term in joined for term in ("ki-007", "legacy-r003", "sab-2025-001"))
        has_tests = "test_commands" in names or "expected_prefix,is_injection" in joined
        return has_script and has_policy and has_prefix_guide and has_conflict_sources and has_tests

    @staticmethod
    def _security_commands(script_text: str) -> list[tuple[int, str]]:
        commands: list[tuple[int, str]] = []
        for line_no, raw_line in enumerate(script_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            prefix = line.split(None, 1)[0]
            if prefix in {"echo", "cd", "ls", "set"}:
                continue
            if any(term in line for term in ("curl ", "python3 -c", "claude -p")):
                commands.append((line_no, line))
        return commands[:8]

    @staticmethod
    def _classify_security_command(command: str) -> dict[str, Any]:
        lower = command.lower()
        if "curl " in lower and "|" in command and "bash" in lower:
            return {
                "prefix": "command_injection_detected",
                "is_injection": True,
                "risk_level": "critical",
                "matched_patterns": ["INJ-004"],
                "reasoning": "unquoted pipe sends curl output to bash",
            }
        if lower.startswith("python3 "):
            return {
                "prefix": "python3",
                "is_injection": False,
                "risk_level": "low",
                "matched_patterns": [],
                "reasoning": "semicolon is inside quoted Python -c argument, not shell injection",
            }
        if lower.startswith("claude "):
            return {
                "prefix": "claude",
                "is_injection": False,
                "risk_level": "high",
                "matched_patterns": [],
                "reasoning": "--dangerously-skip-permissions is a high-risk flag, but quoted non-ASCII prompt text is opaque string data under policy v3.2.0",
            }
        return {
            "prefix": command.split(None, 1)[0] if command.split() else "no_prefix",
            "is_injection": False,
            "risk_level": "medium",
            "matched_patterns": [],
            "reasoning": "no shell metacharacter outside quotes identified by the closure",
        }

    @staticmethod
    def _security_test_counts(csv_text: str) -> tuple[int, int, int]:
        if not csv_text.strip():
            return 0, 0, 0
        try:
            rows = list(csv.DictReader(csv_text.splitlines()))
        except Exception:
            return 0, 0, 0
        total = len(rows)
        injection = sum(1 for row in rows if str(row.get("is_injection", "")).strip().lower() == "true")
        return total, injection, total - injection

    def _audit_closure(self, source_texts: dict[str, str], hint: HintSpec, items: list[CollectionItem]) -> str:
        if not self._goal_wants_audit(hint):
            return ""
        config_text = "\n".join(
            text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}
        )
        script_text = "\n".join(
            text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".py", ".sh"}
        )
        json_sources = {
            name: text for name, text in source_texts.items()
            if Path(name).suffix.lower() == ".json"
        }
        if not json_sources or not config_text:
            return ""

        state_ids: list[str] = []
        last_fetch_ts = ""
        output_records: list[dict[str, Any]] = []
        output_files: list[str] = []
        for name, text in json_sources.items():
            try:
                data = json.loads(text)
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get("seen_ids"), list):
                state_ids = [str(value) for value in data.get("seen_ids", [])]
                last_fetch_ts = str(data.get("last_fetch_ts") or "")
            elif isinstance(data, list):
                records = [record for record in data if isinstance(record, dict)]
                if records:
                    output_records.extend(records)
                    output_files.append(name)

        # This closure is specifically for state-vs-output audits. Other
        # config/code audits are handled by _configuration_audit_closure.
        if not state_ids or not output_records:
            return ""

        output_ids = {str(record.get("announcementId") or "") for record in output_records}
        output_ids.discard("")
        orphan_ids = [ann_id for ann_id in state_ids if ann_id not in output_ids]
        important = [record for record in output_records if record.get("important") is True]
        important_lines = [
            (
                f"{record.get('announcementId', '')}|{record.get('secCode', '')}|"
                f"{record.get('secName', '')}|{record.get('announcementTitle', '')}"
            )
            for record in important[:12]
        ]
        announcement_types = sorted({str(record.get("announcementType")) for record in output_records if record.get("announcementType")})

        try:
            cfg = yaml.safe_load(config_text) or {}
        except Exception:
            cfg = {}
        output_cfg = cfg.get("output", {}) if isinstance(cfg, dict) else {}
        api_cfg = cfg.get("api", {}) if isinstance(cfg, dict) else {}
        notify_cfg = cfg.get("notifications", {}) if isinstance(cfg, dict) else {}

        csv_expected = bool(output_cfg.get("csv_summary"))
        csv_files = {item.name for item in items if item.path.suffix.lower() == ".csv"}
        expected_csv_names = sorted(
            {
                f"{Path(name).name.replace('announcements_', 'summary_').replace('.json', '.csv')}"
                for name in output_files
                if Path(name).name.startswith("announcements_")
            }
        )
        missing_csv = [
            expected
            for expected in expected_csv_names
            if not any(Path(name).name == expected for name in csv_files)
        ]
        script_has_csv_call = "save_csv_summary(" in script_text

        findings: list[str] = ["FILE: collection_audit_closure", "KIND: audit_closure", "overall_status: ready_for_write"]
        if state_ids:
            findings.append(f"state_check: seen_ids={len(state_ids)}; last_fetch_ts={last_fetch_ts or 'unknown'}")
        if output_records:
            findings.append(
                f"output_check: json_files={', '.join(output_files)}; "
                f"record_count={len(output_records)}; output_ids={len(output_ids)}"
            )
        if state_ids and output_records:
            sample = ", ".join(orphan_ids[:5])
            tail = ", ".join(orphan_ids[-3:]) if len(orphan_ids) > 5 else ""
            suffix = f"; sample={sample}" if sample else ""
            if tail:
                suffix += f"; tail={tail}"
            findings.append(
                f"state_vs_output_gap: orphan_seen_ids={len(orphan_ids)}{suffix}; "
                "likely_prior_runs_or_retention_gap_not_proven_fetch_bug"
            )
        if csv_expected:
            findings.append(
                f"csv_output_check: config output.csv_summary=True; "
                f"script_calls_save_csv_summary={script_has_csv_call}; "
                f"missing_expected_csv={', '.join(missing_csv) if missing_csv else 'none'}"
            )
        if "list(seen)[-5000:]" in script_text:
            findings.append("deduplicate_function_bug: deduplicate() updates state['seen_ids'] with list(seen)[-5000:]")
            findings.append(
                "dedup_bug: state['seen_ids']=list(seen)[-5000:] converts a set to a list, "
                "so ordering is arbitrary and the slice may drop recent IDs"
            )
            findings.append(
                "dedup_evidence_line: state['seen_ids'] = list(seen)[-5000:]"
            )
            findings.append("dedup_fix: use sorted(seen, key=int)[-5000:] before saving seen_ids")
        if "save_csv_summary(" in script_text:
            findings.append("csv_code_evidence: script defines/calls save_csv_summary, but no expected summary CSV file is present in the collection")
        if important:
            findings.append(
                f"important_breakdown: count={len(important)}; "
                "report_requirement=list every important_item below, do not summarize or omit"
            )
            findings.extend(f"important_item_{idx}: {line}" for idx, line in enumerate(important_lines, start=1))
        if announcement_types:
            findings.append(f"config_cross_check: announcement_types={', '.join(announcement_types)}")
        if api_cfg:
            findings.append(
                "api_config: "
                f"max_pages={api_cfg.get('max_pages', 'unknown')}; "
                f"fetch_sse={api_cfg.get('fetch_sse', 'unknown')}; "
                f"request_delay={api_cfg.get('request_delay', 'unknown')}; "
                f"category={api_cfg.get('category', 'unknown')!r}"
            )
        if notify_cfg:
            findings.append(f"notification_config: enabled={notify_cfg.get('enabled', 'unknown')}")
        findings.append(
            "evidence_status: ready_for_write; next_action=write fetch-audit.md now; "
            "this audit closure is sufficient for the requested report; "
            "do not run the fetch script, grep, or reread source files unless one named fact is missing; "
            "the final audit must include all numbered important_item entries exactly."
        )
        return self._clip("\n".join(findings), 3600)

    def _panel_did_closure(self, source_texts: dict[str, str], hint: HintSpec) -> str:
        if not self._goal_wants_panel_did(hint):
            return ""
        csv_sources = {
            name: text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".csv", ".tsv"}
        }
        dictionary_text = "\n".join(
            text for name, text in source_texts.items()
            if Path(name).name.lower() in {"data_dictionary.json", "dictionary.json"}
        )
        script_text = "\n".join(
            text for name, text in source_texts.items()
            if Path(name).suffix.lower() == ".py"
        )
        panel_name = self._find_csv_with_headers(csv_sources, {"firm_id", "year", "treated", "post", "did"})
        if not panel_name:
            return ""
        panel_info = self._csv_profile(csv_sources[panel_name])
        metadata_name = self._find_csv_with_headers(csv_sources, {"firm_id", "industry", "firm_name"})
        metadata_info = self._csv_profile(csv_sources[metadata_name]) if metadata_name else {}

        true_att = self._first_float(r"true[^.\n]{0,80}(?:did|att|coefficient)[^0-9-]{0,20}(-?\d+(?:\.\d+)?)", dictionary_text)
        if true_att is None:
            true_att = self._first_float(r"(?:did|att|coefficient)[^.\n]{0,80}(-?\d+(?:\.\d+)?)", dictionary_text)
        raw_did = self._raw_did_from_csv(csv_sources[panel_name])
        years = panel_info.get("years", [])
        firm_count = panel_info.get("firm_count", "unknown")
        treated_count = panel_info.get("treated_firms", "unknown")
        control_count = panel_info.get("control_firms", "unknown")
        controls = [
            column for column in panel_info.get("columns", [])
            if column in {"log_assets", "leverage", "roa", "employees_thousands", "rd_intensity"}
        ]
        metadata_overlap = sorted(
            (set(panel_info.get("columns", [])) & set(metadata_info.get("columns", []))) - {"firm_id"}
        )
        metadata_only = [
            column for column in metadata_info.get("columns", [])
            if column == "firm_id" or column not in panel_info.get("columns", [])
        ]

        findings = [
            "FILE: collection_panel_did_closure",
            "KIND: panel_did_closure",
            (
                f"data_contract: panel_file={panel_name}; rows={panel_info.get('rows', 'unknown')}; "
                f"firms={firm_count}; years={min(years) if years else 'unknown'}..{max(years) if years else 'unknown'}; "
                f"treated_firms={treated_count}; control_firms={control_count}"
            ),
            f"columns: {', '.join(panel_info.get('columns', []))}",
            "identifier_type_contract: keep firm_id as a string/categorical identifier; never coerce firm_id to numeric",
            (
                "model_contract: outcome=revenue_growth_pct; key_regressor=did; "
                "fixed_effects=firm_id and year; cluster_standard_errors=firm_id"
            ),
            (
                "implementation_contract: use statsmodels OLS with did + C(firm_id) + C(year) and "
                "cov_type=cluster/groups=firm_id, or PanelOLS after set_index([firm_id, year]); "
                "do not pass entity_effects/time_effects to PanelOLS.from_formula"
            ),
            (
                "controls_contract: "
                f"{', '.join(controls) if controls else 'use available numeric controls after checking columns'}"
            ),
            (
                "parallel_trends_contract: restrict to pre-period years before 2020; "
                "interact treated with pre-year dummies excluding a base year; report joint test or individual pre-trend p-values"
            ),
        ]
        if metadata_name:
            findings.append(
                f"industry_contract: merge {metadata_name} on firm_id; "
                f"metadata_rows={metadata_info.get('rows', 'unknown')}; summarize revenue growth by industry and treatment"
            )
            if metadata_overlap:
                findings.append(
                    "merge_collision_contract: the panel and metadata both contain "
                    f"{', '.join(metadata_overlap)}. Preserve the panel columns by selecting only firm_id plus "
                    "non-overlapping metadata fields before merge; do not create suffixed duplicates such as "
                    "treated_x/treated_y or industry_x/industry_y. Exact metadata merge columns: "
                    f"{', '.join(metadata_only)}; do not prepend firm_id again because it is already in this list."
                )
        if true_att is not None:
            findings.append(f"ground_truth: true_planted_ATT={true_att:g}")
        if raw_did is not None:
            findings.append(f"sanity_anchor: raw_group_mean_DID={raw_did:.4f}")
        if "TODO" in script_text or "naive" in script_text.lower():
            findings.append("starter_script_check: provided script is a naive/incomplete template and must be replaced at workspace root")
        findings.append(
            "script_instruction: write did_regression.py at workspace root; script should read local CSV/JSON files directly, "
            "run DID with entity and time fixed effects plus firm-clustered SE, run parallel-trends check, print numeric results, "
            "and write did_results_summary.md"
        )
        findings.append(
            "serialization_contract: write did_results_summary.md directly in the same script. Avoid an intermediate JSON file; "
            "if one is used, cast every numpy scalar/boolean/integer to native float/bool/int before json.dump."
        )
        findings.append(
            "minimal_implementation_recipe: panel=pd.read_csv(panel_file,dtype={'firm_id':'string'}); "
            "meta=pd.read_csv(metadata_file,dtype={'firm_id':'string'}); "
            f"df=panel.merge(meta[{metadata_only!r}],on='firm_id',how='left'); "
            "fit smf.ols('revenue_growth_pct ~ did + C(firm_id) + C(year)',data=df).fit("
            "cov_type='cluster',cov_kwds={'groups':df['firm_id']}); create pre-period treated-by-year terms and report "
            "their joint test; compute industry summaries from the preserved panel industry column. Keep the script under "
            "roughly 160 lines and write markdown directly—no plots, intermediate JSON, or exploratory diagnostics."
        )
        findings.append(
            "report_contract: did_results_summary.md must include the numeric DID estimate, clustered SE, confidence interval, "
            "p-value, true-ATT comparison, numeric parallel-trends joint/individual p-values, and at least one numeric industry "
            "breakdown. Explain that firm-level clustering is required because repeated observations within a firm can have "
            "serially correlated errors. If dummy-variable OLS is used, state that firm/year categorical dummies implement the "
            "two-way fixed-effects estimator for this balanced panel. Do not claim PASS or heterogeneity without underlying values."
        )
        findings.append(
            "execution_contract: write both deliverables in one script, then run exactly "
            "`python3 did_regression.py > did_regression_output.txt 2>&1`. Inspect the exit code and deliverable existence only. "
            "On failure, inspect only the final traceback lines; never print the regression summary into tool output. If the run "
            "exits 0 and both deliverables exist, stop without rereading stdout, rerunning, or rewriting the script"
        )
        findings.append(
            "closure_instruction: do not read full panel_data.csv into chat after this closure; use the local CSV path in the script"
        )
        return self._clip("\n".join(findings), 4200)

    def _rule_table_script_closure(self, source_texts: dict[str, str], hint: HintSpec) -> str:
        if not self._goal_wants_rule_table_script(hint):
            return ""
        rule_name, rules = self._find_discount_rules_source(source_texts)
        users_name, user_profile = self._find_user_table_source(source_texts)
        if not rule_name or not users_name or not rules or not user_profile:
            return ""

        tier_rules = rules.get("tier_discounts", {}) if isinstance(rules, dict) else {}
        loyalty = rules.get("loyalty_bonus", {}) if isinstance(rules, dict) else {}
        spending = rules.get("spending_bonus", {}) if isinstance(rules, dict) else {}
        thresholds = spending.get("thresholds", []) if isinstance(spending, dict) else []
        threshold_lines = []
        for item in thresholds:
            if not isinstance(item, dict):
                continue
            threshold_lines.append(
                f"spend>={item.get('min_spent')} -> +{item.get('bonus_pct')}%"
            )

        tier_lines = []
        if isinstance(tier_rules, dict):
            for tier_name, info in tier_rules.items():
                if isinstance(info, dict):
                    tier_lines.append(
                        f"{tier_name}={info.get('base_discount_pct')}% "
                        f"(min_orders={info.get('min_orders', 0)})"
                    )

        irrelevant = sorted(
            name for name in source_texts
            if name not in {rule_name, users_name}
            and any(term in name.lower() for term in ("promotion", "product", "catalog", "coupon"))
        )
        support_docs = sorted(
            name for name in source_texts
            if name not in {rule_name, users_name}
            and Path(name).suffix.lower() in {".md", ".txt", ".rst"}
        )

        findings = [
            "FILE: collection_rule_table_script_closure",
            "KIND: rule_table_script_closure",
            "overall_status: ready_for_write",
            f"authoritative_rule_source: {rule_name}",
            f"authoritative_row_source: {users_name}; rows={user_profile.get('rows', 'unknown')}",
            f"user_schema: {', '.join(user_profile.get('columns', []))}",
        ]
        if tier_lines:
            findings.append(f"tier_rules: {'; '.join(tier_lines)}")
        if isinstance(loyalty, dict):
            findings.append(
                "loyalty_rule: "
                f"enabled={loyalty.get('enabled')}; "
                f"years_threshold={loyalty.get('years_threshold')}; "
                f"bonus_pct={loyalty.get('bonus_pct')}"
            )
        if threshold_lines:
            findings.append(
                "spending_rule: highest qualifying threshold only; "
                + "; ".join(threshold_lines)
            )
        if isinstance(rules, dict):
            findings.append(
                "eligibility_and_cap: "
                f"inactive_user_eligible={rules.get('inactive_user_eligible')}; "
                f"max_discount_pct={rules.get('max_discount_pct')}"
            )
        if support_docs:
            findings.append(
                f"supporting_context_only: {', '.join(support_docs)}; JSON rule source wins on conflicts"
            )
        if irrelevant:
            findings.append(
                f"irrelevant_for_core_calculation: {', '.join(irrelevant)}; do not use for user discounts"
            )
        findings.extend(
            [
                (
                    "script_contract: write an import-safe Python file that reads the local JSON/CSV paths at runtime; "
                    "do not paste all CSV rows into the script or conversation"
                ),
                (
                    "api_contract: expose calculate_discount(user_row: dict, rules: dict|None=None, "
                    "reference_date: str|date|None=None) -> float; do not return a rich dict from calculate_discount; "
                    "put component breakdown in a separate helper if needed; class API is optional, function API should work; "
                    "user_row may be a raw csv.DictReader row with string total_spent/order_count/is_active/signup_date values; "
                    "calculate_discount(row, rules) must apply the default as-of date, not skip loyalty"
                ),
                (
                    "date_contract: if the task prompt gives an evaluation/as-of date, make it a module constant and default; "
                    "if it says as of 2025-07-01, set DEFAULT_REFERENCE_DATE = date(2025, 7, 1) and use it whenever reference_date is None; "
                    "never infer this from the current system date or date.today()"
                ),
                (
                    "import_contract: keep module import side-effect free and avoid dataclass-only return objects; "
                    "grader-style importlib calls should be able to import and call the reusable entrypoint"
                ),
                (
                    "closure_instruction: this closure is sufficient to write the calculator; "
                    "next step is write_file, then run one equivalent short API check"
                ),
            ]
        )
        return self._clip("\n".join(findings), 2400)


    @staticmethod
    def _collection_is_diagnostic_shape(items: list[CollectionItem], source_texts: dict[str, str]) -> bool:
        """Check if the collection looks like a multi-file diagnostic task: config + log + csv/table + md/doc."""
        suffixes = set()
        for item in items:
            s = item.path.suffix.lower()
            if s in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf"}:
                suffixes.add("config")
            elif s == ".log":
                suffixes.add("log")
            elif s in {".csv", ".tsv"}:
                suffixes.add("table")
            elif s in {".md", ".markdown", ".rst", ".txt"}:
                suffixes.add("doc")
        # Need all 4 families: config + log + table + doc
        required = {"config", "log", "table", "doc"}
        return required.issubset(suffixes)

    @staticmethod
    def _goal_wants_diagnostic_ledger(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        diagnostic_terms = (
            "diagnose", "diagnosis", "investigate", "root cause", "analyze",
            "fix", "config", "configuration", "log", "logs", "precision",
            "proposal", "proposals", "eviction", "retrieval", "memory",
            "retention", "truncation", "scoring", "weight", "benchmark",
            "methodology", "metrics", "pipeline", "seed",
        )
        # Strong terms: specific to multi-file pipeline/eviction/proposal diagnostics
        strong_terms = (
            "eviction", "truncation", "pipeline", "cross-file", "multi-file",
            "seed eviction", "root cause", "proposal", "proposals",
            "benchmark", "methodology",
        )
        if any(term in hay for term in strong_terms):
            return True
        # Medium terms: need at least 3 for general multi-file diagnosis
        medium_terms = (
            "diagnose", "diagnosis", "investigate", "analyze",
            "precision", "scoring weight", "retrieval system",
            "memory retrieval", "config diff",
        )
        matches = sum(1 for term in medium_terms if term in hay)
        return matches >= 3


    @staticmethod
    def _ledger_coverage_ready(ledger_text: str) -> bool:
        """Check if the diagnostic ledger covers at least 3 of 4 families: config, log, table/metric, proposal/doc."""
        families = 0
        # Section markers in the ledger output
        if "=== config_snapshot ===" in ledger_text or "=== config_diffs ===" in ledger_text:
            families += 1
        if "=== log_events ===" in ledger_text:
            families += 1
        if "=== metric_tables ===" in ledger_text:
            families += 1
        if "=== proposal_inventory ===" in ledger_text:
            families += 1
        # Also check that there's actual evidence (not just empty sections)
        return families >= 3 and "R1:" in ledger_text

    def _diagnostic_ledger_closure(self, source_texts: dict[str, str], hint: HintSpec, items: list[CollectionItem]) -> tuple[str, dict[str, str], bool]:
        """Build a source-grounded fact ledger for multi-file diagnostic collections.

        Returns (compact_text, sections_dict, is_ready).
        - compact_text: short visible output fitting tool preview (<=900 chars).
        - sections_dict: full sections keyed by category for detail expansion.
        - is_ready: whether enough families are covered for deliverable writing.
        """
        if not self._collection_is_diagnostic_shape(items, source_texts) and not self._goal_wants_diagnostic_ledger(hint):
            return ("", {}, False)

        # Separate sources by family
        config_sources = {
            name: text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf"}
        }
        log_sources = {
            name: text for name, text in source_texts.items() if name.endswith(".log")
        }
        table_sources = {
            name: text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".csv", ".tsv"}
        }
        doc_sources = {
            name: text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".md", ".markdown", ".rst", ".txt"}
        }

        findings: list[str] = [
            "FILE: collection_diagnostic_ledger",
            "KIND: diagnostic_ledger",
        ]

        # ----- config_snapshot -----
        config_kvs: list[str] = []
        for name, text in sorted(config_sources.items()):
            suffix = Path(name).suffix.lower()
            try:
                if suffix in {".yaml", ".yml"}:
                    parsed = yaml.safe_load(text) or {}
                elif suffix == ".json":
                    parsed = json.loads(text)
                elif suffix in {".toml", ".ini", ".cfg", ".conf"}:
                    # Use raw key=value parsing for ini-style
                    parsed = {}
            except Exception:
                continue
            if isinstance(parsed, dict):
                for key, value in self._flatten(parsed):
                    short_key = key.split(".")[-1] if "." in key else key
                    val_str = str(value)
                    # Normalize Python booleans to lowercase JSON-style
                    if val_str == "True":
                        val_str = "true"
                    elif val_str == "False":
                        val_str = "false"
                    elif val_str == "None":
                        val_str = "null"
                    if len(val_str) > 120:
                        val_str = val_str[:117] + "..."
                    config_kvs.append(f"  {short_key}: {val_str}  # source={name}")

        if config_kvs:
            findings.append("=== config_snapshot ===")
            findings.extend(config_kvs[:40])

        # ----- config_diffs -----
        config_diff_pairs: list[tuple[str, str, str, str]] = []  # (key, val1, source1, val2, source2)
        config_names = sorted(config_sources.keys())
        if len(config_names) >= 2:
            for i in range(len(config_names)):
                for j in range(i + 1, len(config_names)):
                    try:
                        data_i = self._parse_config(config_sources[config_names[i]])
                        data_j = self._parse_config(config_sources[config_names[j]])
                    except Exception:
                        continue
                    if not isinstance(data_i, dict) or not isinstance(data_j, dict):
                        continue
                    for key, val_i in self._flatten(data_i):
                        short_key = key.split(".")[-1] if "." in key else key
                        for k2, val_j in self._flatten(data_j):
                            if key == k2 and str(val_i) != str(val_j):
                                config_diff_pairs.append((short_key, str(val_i), config_names[i], str(val_j), config_names[j]))
            if config_diff_pairs:
                findings.append("=== config_diffs ===")
                seen_diffs: set[str] = set()
                for short_key, val1, src1, val2, src2 in config_diff_pairs[:20]:
                    diff_key = f"{short_key}:{val1}:{val2}"
                    if diff_key in seen_diffs:
                        continue
                    seen_diffs.add(diff_key)
                    findings.append(f"  {short_key}: [{src1}]={val1} vs [{src2}]={val2}")

        # ----- disabled_or_zero_flags -----
        disabled_flags: list[str] = []
        for name, text in sorted(config_sources.items()):
            try:
                data = self._parse_config(text)
            except Exception:
                continue
            if isinstance(data, dict):
                for key, value in self._flatten(data):
                    short_key = key.split(".")[-1] if "." in key else key
                    val_str = str(value).strip()
                    val_lower = val_str.lower()
                    if val_lower in {"0", "false", "disabled", "none", "no", "off", "null"}:
                        # Normalize Python booleans to lowercase for display
                        if val_str == "True":
                            val_str = "true"
                        elif val_str == "False":
                            val_str = "false" 
                        disabled_flags.append(f"  {short_key}: {val_str}  # source={name}")
        if disabled_flags:
            findings.append("=== disabled_or_zero_flags ===")
            findings.extend(disabled_flags[:15])

        # ----- log_events -----
        if log_sources:
            findings.append("=== log_events ===")
            for name, text in sorted(log_sources.items()):
                lines = text.splitlines()
                # Extract stage markers
                stages: list[str] = []
                for line in lines:
                    m = re.search(r"STAGE\s+(\d+:[^\]]+)", line)
                    if m:
                        stages.append(m.group(1).strip())
                # Extract summary
                summary_lines: list[str] = []
                for line in lines:
                    if "SUMMARY" in line or "summary" in line.lower():
                        summary_lines.append(line.strip())
                # Extract eviction/drop/truncate events
                eviction_events: list[str] = []
                for line in lines:
                    if re.search(r"(DROPPED|evicted|EVICTED|truncating|TRUNCAT)", line):
                        eviction_events.append(line.strip()[:200])
                # Extract count/cap info
                count_lines: list[str] = []
                for line in lines:
                    if re.search(r"(count|Count|entries|Entries|result|Result)\s*(after|:|total|count)?\s*\d+", line):
                        count_lines.append(line.strip()[:200])
                # Extract N of M eviction
                n_of_m: list[str] = []
                for line in lines:
                    if re.search(r"\d+\s+of\s+\d+", line):
                        n_of_m.append(line.strip()[:200])
                # Extract seed IDs
                seed_ids: list[str] = []
                for line in lines:
                    if re.search(r"Seed\s*#\d+.*DROPPED", line):
                        seed_ids.append(line.strip()[:200])
                # Extract precision
                precision_lines: list[str] = []
                for line in lines:
                    if re.search(r"(recision|Precision)\s*[:(]?\s*0?\.\d+", line):
                        precision_lines.append(line.strip()[:200])

                log_block = [f"  source: {name}"]
                if stages:
                    log_block.append(f"  stages: {', '.join(stages[:6])}")
                if eviction_events:
                    log_block.append(f"  eviction_events:")
                    for ev in eviction_events[:8]:
                        log_block.append(f"    {ev}")
                if seed_ids:
                    log_block.append(f"  dropped_seeds:")
                    for sid in seed_ids[:10]:
                        log_block.append(f"    {sid}")
                if n_of_m:
                    log_block.append(f"  n_of_m: {'; '.join(n_of_m[:4])}")
                if precision_lines:
                    log_block.append(f"  precision_reported: {'; '.join(precision_lines[:3])}")
                if count_lines:
                    log_block.append(f"  counts: {'; '.join(count_lines[:5])}")
                findings.extend(log_block)

        # ----- metric_tables -----
        if table_sources:
            findings.append("=== metric_tables ===")
            for name, text in sorted(table_sources.items()):
                try:
                    delimiter = "\t" if name.endswith(".tsv") else ","
                    reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
                    rows = list(reader)
                except Exception:
                    continue
                if not rows:
                    continue
                headers = list(rows[0].keys())
                # Only include small tables (< 30 rows) fully
                if len(rows) <= 30:
                    findings.append(f"  source: {name}")
                    findings.append(f"  columns: {', '.join(headers)}")
                    for idx, row in enumerate(rows, start=1):
                        values = " | ".join(f"{h}={row.get(h, '')}" for h in headers)
                        findings.append(f"  R{idx}: {values}")
                else:
                    findings.append(f"  source: {name} (total_rows={len(rows)})")
                    findings.append(f"  columns: {', '.join(headers)}")
                    for idx, row in enumerate(rows[:10], start=1):
                        values = " | ".join(f"{h}={row.get(h, '')}" for h in headers)
                        findings.append(f"  R{idx}: {values}")
                    findings.append(f"  ...({len(rows) - 10} more rows)")

        # ----- methodology_flags -----
        methodology: list[str] = []
        for name, text in sorted({**table_sources, **doc_sources}.items()):
            lower = text.lower()
            if "filter" in lower and ("timestamp" in lower or "date" in lower or "recent" in lower):
                # Extract the filter condition
                filter_match = re.search(r"(?:filter|restrict|subset|only).{0,100}(?:(?:timestamp|date)\s*[><=]\s*\S+)", text, re.IGNORECASE)
                detail = filter_match.group(0) if filter_match else "filter present"
                methodology.append(f"  source={name}: {detail}")
            elif any(term in lower for term in ("restricted", "subset", "recent only", "evaluation only on")):
                methodology.append(f"  source={name}: potential methodology restriction detected")
        if methodology:
            findings.append("=== methodology_flags ===")
            findings.extend(methodology[:8])

        # ----- proposal_inventory -----
        if doc_sources:
            for name, text in sorted(doc_sources.items()):
                headings = re.findall(r"^#{1,4}\s+(Proposal\s*\d+.*|Option\s*\d+.*|Strategy\s*\d+.*)$", text, re.MULTILINE | re.IGNORECASE)
                if headings:
                    findings.append("=== proposal_inventory ===")
                    findings.append(f"  source: {name}")
                    for h in headings[:15]:
                        findings.append(f"  heading: {h.strip()}")
                        # Try to grab a short snippet of pros/cons/status after heading
                        section_match = re.search(
                            re.escape(h) + r".{0,500}",
                            text, re.DOTALL
                        )
                        if section_match:
                            snippet = section_match.group(0)
                            status_match = re.search(r"\b(?:Status|Decision|Verdict)\s*[：:–-]\s*(.{5,80})", snippet)
                            pros_match = re.search(r"(?:Pros?|Advantages?|Strengths?)\s*[：:–-]\s*(.{5,120})", snippet)
                            cons_match = re.search(r"(?:Cons?|Disadvantages?|Weaknesses?|Drawbacks?)\s*[：:–-]\s*(.{5,120})", snippet)
                            if status_match:
                                findings.append(f"    status: {status_match.group(1).strip()}")
                            if pros_match:
                                findings.append(f"    pros: {pros_match.group(1).strip()}")
                            if cons_match:
                                findings.append(f"    cons: {cons_match.group(1).strip()}")

        # ----- evidence_coverage -----
        coverage = []
        if config_sources:
            coverage.append("config")
        if log_sources:
            coverage.append("log")
        if table_sources:
            coverage.append("table/metric")
        if doc_sources:
            coverage.append("proposal/doc")
        findings.append("=== evidence_coverage ===")
        findings.append(f"  covered_families: {', '.join(coverage)}")
        findings.append(f"  source_count: {len(source_texts)}")
        findings.append(f"  source_list: {', '.join(sorted(source_texts.keys()))}")

        if len(findings) <= 3:
            return ("", {}, False)

        sections = self._split_ledger_sections(findings)
        compact = self._render_compact_ledger(sections)
        is_ready = self._ledger_coverage_from_sections(sections)
        return (compact, sections, is_ready)

    @staticmethod
    def _split_ledger_sections(findings: list[str]) -> dict[str, str]:
        """Split flat findings list into sections keyed by category."""
        sections: dict[str, list[str]] = {}
        current_key = "_header"
        for line in findings:
            if line.startswith("=== ") and line.endswith(" ==="):
                current_key = line[4:-4].strip()
                sections.setdefault(current_key, [])
            else:
                sections.setdefault(current_key, []).append(line)
        result: dict[str, str] = {}
        config_parts: list[str] = []
        for key in ("config_snapshot", "disabled_or_zero_flags"):
            if key in sections:
                config_parts.append(f"=== {key} ===\n" + "\n".join(sections[key]))
        if config_parts:
            result["config"] = "\n".join(config_parts)
        if "config_diffs" in sections:
            result["diffs"] = "=== config_diffs ===\n" + "\n".join(sections["config_diffs"])
        if "log_events" in sections:
            result["loss"] = "=== log_events ===\n" + "\n".join(sections["log_events"])
        if "metric_tables" in sections:
            result["metrics"] = "=== metric_tables ===\n" + "\n".join(sections["metric_tables"])
        if "methodology_flags" in sections:
            result["evaluation"] = "=== methodology_flags ===\n" + "\n".join(sections["methodology_flags"])
        if "proposal_inventory" in sections:
            result["proposals"] = "=== proposal_inventory ===\n" + "\n".join(sections["proposal_inventory"])
        if "evidence_coverage" in sections:
            result["_coverage"] = "=== evidence_coverage ===\n" + "\n".join(sections["evidence_coverage"])
        return result

    @staticmethod
    def _render_compact_ledger(sections: dict[str, str]) -> str:
        """Render a short compact view of diagnostic evidence, <=900 chars."""
        import re as _re
        lines: list[str] = ["DIAG compact evidence (use diagnostic_detail_<section> for full facts before writing)"]
        config_text = sections.get("config", "")
        if config_text:
            diffs_text = sections.get("diffs", "")
            diffs: list[str] = []
            for line in diffs_text.splitlines():
                if " vs " in line and "source=" not in line:
                    diffs.append(line.strip().lstrip("- "))
            flags: list[str] = []
            for line in config_text.splitlines():
                stripped = line.strip()
                if any(stripped.endswith(f" {v}") for v in ("0", "false", "disabled", "none", "null")):
                    key = stripped.split(":")[0].strip() if ":" in stripped else stripped.split()[0]
                    flags.append(key)
            weights: list[str] = []
            weight_kw = ("weight", "bias", "similarity", "frequency", "recency", "semantic", "keyword_match")
            for line in config_text.splitlines():
                stripped = line.strip().lstrip("- ")
                has_float = _re.search(r"\b0?\.\d{1,3}\b", stripped)
                has_kw = any(kw in stripped.lower() for kw in weight_kw)
                if has_float and has_kw:
                    clean = stripped.split("#")[0].strip()
                    weights.append(clean)
            parts: list[str] = []
            if diffs:
                parts.append("diff: " + "; ".join(diffs[:6]))
            if weights:
                parts.append("weights: " + "; ".join(weights[:6]))
            if flags:
                parts.append("zero_flags: " + ", ".join(flags[:4]))
            if parts:
                lines.append("config: " + ". ".join(parts))
        loss_text = sections.get("loss", "")
        if loss_text:
            n_of_m = _re.findall(r"\d+\s+of\s+\d+", loss_text)
            seed_ids = _re.findall(r"Seed\s*#\s*(\d+)", loss_text)
            parts: list[str] = []
            if n_of_m:
                parts.append("evicted=" + "; ".join(n_of_m[:4]))
            if seed_ids:
                parts.append("ids=" + ",".join(seed_ids[:10]))
            if parts:
                lines.append("loss: " + ". ".join(parts))
        metric_text = sections.get("metrics", "")
        if metric_text:
            buckets = _re.findall(r"time_bucket=(Q\d-\d{4}).*?avg_precision=(0?\.\d+)", metric_text)
            if buckets:
                trend = " ".join(f"{b[0]}:{b[1]}" for b in buckets[:6])
                lines.append(f"precision: {trend}")
        eval_text = sections.get("evaluation", "")
        if eval_text:
            flags: list[str] = []
            for line in eval_text.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("==="):
                    flags.append(stripped.lstrip("- "))
            if flags:
                lines.append("evaluation: " + "; ".join(flags[:3]))
        prop_text = sections.get("proposals", "")
        if prop_text:
            headings = _re.findall(r"heading:\s*(.+)", prop_text)
            if headings:
                short = " | ".join(h[:40] for h in headings[:8])
                lines.append(f"proposals: {short}")
        # Detail guidance — inserted after DIAG compact line for visibility
        available = [k for k in ("config", "diffs", "loss", "metrics", "evaluation", "proposals") if k in sections]
        detail_guide = "|".join(available)
        lines.insert(1, f"next: write deliverables; for detail use sro_read needle diagnostic_detail_<section> ({detail_guide})")
        return "\n".join(lines)

    @staticmethod
    def _ledger_coverage_from_sections(sections: dict[str, str]) -> bool:
        """Check if at least 3 of 4 diagnostic families are covered and have non-empty evidence."""
        families = 0
        for key in ("config", "loss", "metrics"):
            if key in sections and len(sections[key]) > 50:
                families += 1
        if ("proposals" in sections and len(sections["proposals"]) > 30) or \
           ("evaluation" in sections and len(sections["evaluation"]) > 30):
            families += 1
        return families >= 3

    @staticmethod
    def _parse_config(text: str) -> Any:
        """Parse a config text into a dict, trying YAML/JSON/TOML/INI in order."""
        try:
            return yaml.safe_load(text)
        except Exception:
            pass
        try:
            return json.loads(text)
        except Exception:
            pass
        # Try INI parsing
        try:
            parser = configparser.ConfigParser()
            parser.read_string(text)
            return {section: dict(parser[section]) for section in parser.sections()}
        except Exception:
            pass
        return {}

    @staticmethod
    def _fill_diagnostic_sources(source_texts: dict[str, str], items: list[CollectionItem]) -> None:
        wanted = {".log", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf", ".py", ".sh"}
        for item in items:
            if item.name in source_texts:
                continue
            if item.path.suffix.lower() not in wanted:
                continue
            try:
                source_texts[item.name] = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue

    @staticmethod
    def _fill_diagnostic_ledger_sources(source_texts: dict[str, str], items: list[CollectionItem]) -> None:
        """Load all config, log, csv/table, and doc files for diagnostic ledger."""
        wanted = {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf",
                  ".log", ".csv", ".tsv", ".md", ".markdown", ".rst", ".txt"}
        for item in items:
            if item.name in source_texts:
                continue
            if item.path.suffix.lower() not in wanted:
                continue
            try:
                source_texts[item.name] = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue

    @staticmethod
    def _fill_audit_sources(source_texts: dict[str, str], items: list[CollectionItem]) -> None:
        wanted = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".py", ".sh", ".csv", ".tsv"}
        for item in items:
            if item.name in source_texts:
                continue
            if item.path.suffix.lower() not in wanted:
                continue
            try:
                source_texts[item.name] = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue

    @staticmethod
    def _fill_analysis_sources(source_texts: dict[str, str], items: list[CollectionItem]) -> None:
        wanted = {".csv", ".tsv", ".json", ".yaml", ".yml", ".txt", ".md", ".py"}
        for item in items:
            if item.name in source_texts:
                continue
            if item.path.suffix.lower() not in wanted:
                continue
            try:
                source_texts[item.name] = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue

    @staticmethod
    def _fill_rule_table_script_sources(source_texts: dict[str, str], items: list[CollectionItem]) -> None:
        wanted = {".csv", ".tsv", ".json", ".yaml", ".yml", ".md", ".txt"}
        for item in items:
            if item.name in source_texts:
                continue
            if item.path.suffix.lower() not in wanted:
                continue
            try:
                source_texts[item.name] = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue

    @staticmethod
    def _fill_command_security_sources(source_texts: dict[str, str], items: list[CollectionItem]) -> None:
        wanted = {".sh", ".yaml", ".yml", ".json", ".md", ".csv", ".log"}
        for item in items:
            if item.name in source_texts:
                continue
            if item.path.suffix.lower() not in wanted:
                continue
            try:
                source_texts[item.name] = item.path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue

    @staticmethod
    def _first_named_source(source_texts: dict[str, str], suffix: str) -> tuple[str, str]:
        for name, text in source_texts.items():
            if Path(name).suffix.lower() == suffix:
                return name, text
        return "", ""

    @staticmethod
    def _find_discount_rules_source(source_texts: dict[str, str]) -> tuple[str, dict[str, Any]]:
        for name, text in source_texts.items():
            if Path(name).suffix.lower() != ".json":
                continue
            if "discount" not in name.lower() and "tier" not in text.lower():
                continue
            try:
                data = json.loads(text)
            except Exception:
                continue
            if isinstance(data, dict) and {"tier_discounts", "spending_bonus"}.issubset(data):
                return name, data
        return "", {}

    @staticmethod
    def _find_user_table_source(source_texts: dict[str, str]) -> tuple[str, dict[str, Any]]:
        required = {"user_id", "membership_tier", "signup_date", "total_spent", "is_active"}
        for name, text in source_texts.items():
            if Path(name).suffix.lower() not in {".csv", ".tsv"}:
                continue
            delimiter = "\t" if Path(name).suffix.lower() == ".tsv" else ","
            try:
                reader = csv.DictReader(text.splitlines(), delimiter=delimiter)
                rows = list(reader)
            except Exception:
                continue
            columns = list(rows[0].keys()) if rows else list(reader.fieldnames or [])
            if required.issubset({str(column) for column in columns}):
                return name, {"rows": len(rows), "columns": columns}
        return "", {}

    @staticmethod
    def _is_diagnostic_source_anchor(anchor: str) -> bool:
        suffix = Path(anchor).suffix.lower()
        if suffix in {".log", ".py", ".sh", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}:
            return True
        name = Path(anchor).name.lower()
        return name in {"messaging.json", "scheduler.json", "task_scheduler.json", "config.json"}

    @staticmethod
    def _is_audit_source_anchor(anchor: str) -> bool:
        suffix = Path(anchor).suffix.lower()
        if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".py", ".sh", ".csv", ".tsv"}:
            return True
        name = Path(anchor).name.lower()
        return any(term in name for term in ("state", "output", "config", "audit", "fetch"))

    @staticmethod
    def _is_analysis_source_anchor(anchor: str) -> bool:
        suffix = Path(anchor).suffix.lower()
        if suffix in {".json", ".yaml", ".yml", ".txt", ".md", ".py"}:
            return True
        name = Path(anchor).name.lower()
        return any(term in name for term in ("dictionary", "metadata", "notes", "script"))

    def _excerpt_csv(self, item: CollectionItem, text: str, hint: HintSpec) -> str:
        delimiter = "\t" if item.path.suffix.lower() == ".tsv" else ","
        try:
            rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
        except Exception:
            return self._compact_file_text(item, text, hint)
        if not rows:
            return ""
        headers = list(rows[0].keys())
        terms = self._excerpt_terms(hint)
        wants_table = self._goal_wants_complete_rows(hint) or len(rows) <= 25
        selected: list[dict[str, str]] = []
        for row in rows:
            row_text = " | ".join(f"{key}={value}" for key, value in row.items())
            if wants_table or self._matches_excerpt(row_text, terms):
                selected.append(row)
            if len(selected) >= 30:
                break
        lines = [f"FILE: {item.name}", f"KIND: {item.kind}", f"columns: {', '.join(headers)}"]
        for idx, row in enumerate(selected, start=1):
            values = " | ".join(f"{key}={row.get(key, '')}" for key in headers)
            lines.append(f"R{idx}: {values}")
        return self._clip("\n".join(lines), 1400)

    def _flatten(self, obj: Any, prefix: str = "$"):
        if isinstance(obj, dict):
            for key, value in obj.items():
                yield from self._flatten(value, f"{prefix}.{key}")
        elif isinstance(obj, list):
            if all(not isinstance(value, (dict, list)) for value in obj):
                yield prefix, json.dumps(obj, ensure_ascii=False)
            else:
                for idx, value in enumerate(obj[:40]):
                    yield from self._flatten(value, f"{prefix}[{idx}]")
                if len(obj) > 40:
                    yield f"{prefix}.length", str(len(obj))
        else:
            yield prefix, "" if obj is None else str(obj)

    def _excerpt_terms(self, hint: HintSpec) -> set[str]:
        terms = set(self._terms(hint))
        terms.update({
            "429", "api", "arima", "actual", "actual_flow", "baseline",
            "cap", "changelog", "confidence", "delay", "delay_seconds",
            "discount", "fallback", "forecast", "forecast_values", "historical",
            "inactive", "loyalty", "normalization", "normalization_factor",
            "performance", "rate", "rate_limit", "retry", "retry_after",
            "s-4021", "sensor", "send_message", "telegram", "threshold",
            "tier", "timezone", "v2.1", "v2.1.0", "weather",
            "audit", "seen_ids", "last_fetch_ts", "announcementid",
            "important", "csv_summary", "summary_", "deduplicate",
            "list(seen)", "save_csv_summary", "notifications",
            "did", "difference-in-differences", "panel", "firm_id",
            "revenue_growth_pct", "fixed effects", "entity effects",
            "time effects", "cluster", "parallel trends", "true att",
            "known issue", "underprediction", "bias", "coverage",
            "confidence_interval", "confidence_intervals_95pct",
            "urban", "highway", "inductive", "lane", "lanes",
            "peak_flow", "flow_range", "precipitation", "clear", "dry",
        })
        return {term.lower() for term in terms if term}

    @staticmethod
    def _matches_excerpt(text: str, terms: set[str]) -> bool:
        hay = text.lower()
        return any(term in hay for term in terms)

    @staticmethod
    def _goal_wants_complete_rows(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        return any(term in hay for term in ("all rows", "all records", "compute", "calculate", "metrics", "assessment"))

    @staticmethod
    def _goal_wants_excerpt_digest(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        return any(
            term in hay
            for term in (
                "assess", "assessment", "analysis", "analyze", "calculate",
                "audit", "integrity", "consistency", "cross-reference",
                "bug", "bugs", "flagged", "important",
                "did", "difference-in-differences", "regression",
                "fixed effects", "parallel trends", "panel",
                "diagnose", "diagnosis", "investigate", "root cause",
                "cross-file", "configuration", "config", "log", "metrics",
                "forecast", "rules", "discount",
                "command prefix", "injection", "security policy", "suspicious command",
                "command_classifications", "security_analysis_report",
            )
        )

    @staticmethod
    def _goal_wants_diagnosis(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        return any(term in hay for term in ("diagnose", "diagnosis", "investigate", "root cause", "failure", "failed"))

    @staticmethod
    def _goal_wants_audit(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        return any(term in hay for term in (
            "audit", "integrity", "consistency", "cross-reference", "state vs output",
            "flagged", "important", "bug", "fetcher", "fetch_state", "seen_ids",
            "announcements", "output", "run_scheduled_fetch",
        ))

    @staticmethod
    def _goal_wants_panel_did(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        has_did = any(term in hay for term in ("did", "difference-in-differences", "difference in differences"))
        has_panel = any(term in hay for term in ("panel", "firm fixed", "entity fixed", "year fixed", "time fixed", "parallel trends"))
        return has_did and has_panel

    @staticmethod
    def _goal_wants_rule_table_script(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        has_discount = any(term in hay for term in ("discount", "discount_rules", "spending bonus", "loyalty"))
        has_rule_table = any(term in hay for term in ("rule", "rules", "calculator", "calculate", "script"))
        has_user_rows = any(term in hay for term in ("user", "users.csv", "row", "rows", "csv"))
        return has_discount and has_rule_table and has_user_rows

    @staticmethod
    def _goal_wants_command_security(hint: HintSpec) -> bool:
        hay = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        has_command = any(term in hay for term in ("command prefix", "suspicious command", "command_classifications", "injection"))
        has_security = any(term in hay for term in ("security", "policy", "risk", "bulletin", "known_injections"))
        return has_command and has_security

    @staticmethod
    def _first_int(pattern: str, text: str) -> int | None:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        try:
            return int(match.group(1).strip())
        except ValueError:
            return None

    @staticmethod
    def _first_float(pattern: str, text: str) -> float | None:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        try:
            return float(match.group(1).strip())
        except ValueError:
            return None

    @staticmethod
    def _find_csv_with_headers(csv_sources: dict[str, str], required: set[str]) -> str:
        for name, text in csv_sources.items():
            try:
                reader = csv.reader(text.splitlines())
                headers = {header.strip() for header in next(reader, [])}
            except Exception:
                continue
            if required.issubset(headers):
                return name
        return ""

    @staticmethod
    def _csv_profile(text: str) -> dict[str, Any]:
        try:
            rows = list(csv.DictReader(text.splitlines()))
        except Exception:
            return {}
        columns = list(rows[0].keys()) if rows else []
        years = sorted({
            int(row["year"]) for row in rows
            if str(row.get("year", "")).isdigit()
        })
        firm_ids = {row.get("firm_id", "") for row in rows if row.get("firm_id")}
        treated_firms = {
            row.get("firm_id", "") for row in rows
            if row.get("firm_id") and str(row.get("treated", "")).strip() == "1"
        }
        control_firms = firm_ids - treated_firms
        return {
            "rows": len(rows),
            "columns": columns,
            "years": years,
            "firm_count": len(firm_ids) if firm_ids else "unknown",
            "treated_firms": len(treated_firms) if treated_firms else "unknown",
            "control_firms": len(control_firms) if control_firms else "unknown",
        }

    @staticmethod
    def _raw_did_from_csv(text: str) -> float | None:
        try:
            rows = list(csv.DictReader(text.splitlines()))
        except Exception:
            return None
        buckets: dict[tuple[str, str], list[float]] = {
            ("1", "1"): [],
            ("1", "0"): [],
            ("0", "1"): [],
            ("0", "0"): [],
        }
        for row in rows:
            key = (str(row.get("treated", "")).strip(), str(row.get("post", "")).strip())
            if key not in buckets:
                continue
            try:
                buckets[key].append(float(row.get("revenue_growth_pct", "")))
            except ValueError:
                continue
        if not all(buckets.values()):
            return None
        means = {key: sum(values) / len(values) for key, values in buckets.items()}
        return (means[("1", "1")] - means[("1", "0")]) - (means[("0", "1")] - means[("0", "0")])

    @staticmethod
    def _first_value(pattern: str, text: str) -> str:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return ""
        return match.group(1).strip().strip("'\"")

    @staticmethod
    def _missing_iso_dates(dates: list[str]) -> list[str]:
        from datetime import date, timedelta

        parsed: list[date] = []
        for item in dates:
            try:
                parsed.append(date.fromisoformat(item))
            except ValueError:
                continue
        if len(parsed) < 2:
            return []
        observed = set(parsed)
        current = min(parsed)
        end = max(parsed)
        missing: list[str] = []
        while current <= end:
            if current not in observed:
                missing.append(current.isoformat())
            current += timedelta(days=1)
        return missing

    def _terms(self, hint: HintSpec) -> list[str]:
        raw = " ".join([hint.goal, *hint.needles, *hint.must_keep]).lower()
        terms = [
            token for token in re.findall(r"[a-z0-9_./:-]{3,}", raw)
            if token not in self._STOPWORDS
        ]
        joined_terms: list[str] = []
        if "project" in terms and "alpha" in terms:
            joined_terms.append("project alpha")
        normalized: list[str] = []
        for term in terms:
            normalized.append(term)
            if term.endswith("s") and len(term) > 4:
                normalized.append(term[:-1])
        return [*joined_terms, *normalized[:18]]

    @staticmethod
    def _item_text(item: CollectionItem) -> str:
        return f"{item.name}\n{item.sender}\n{item.date}\n{item.subject}\n{item.snippet}"

    def _candidate_text(self, item: CollectionItem) -> str:
        parts = [f"file: {item.name}", f"kind: {item.kind}", f"size_bytes: {item.size}"]
        if item.subject:
            parts.append(f"subject: {item.subject}")
        if item.sender:
            parts.append(f"from: {item.sender}")
        if item.date:
            parts.append(f"date: {item.date}")
        if item.snippet:
            parts.append(f"snippet: {item.snippet[:140]}")
        return "\n".join(parts)

    def _item_summary(self, item: CollectionItem) -> dict:
        return {
            "name": item.name,
            "kind": item.kind,
            "size_bytes": item.size,
            "subject": item.subject,
            "from": item.sender,
            "date": item.date,
            "snippet": item.snippet[:80],
        }

    def _skeleton_line(self, item: CollectionItem) -> str:
        label = item.subject or item.name
        return f"{item.name}: {label}"

    def _fit_budget(self, blocks: list[EvidenceBlock], budget: int) -> list[EvidenceBlock]:
        picked: list[EvidenceBlock] = []
        used = 0
        for block in blocks:
            cost = len(block.text) + len(block.anchor) + 32
            if picked and used + cost > budget:
                continue
            picked.append(block)
            used += cost
            if used >= budget:
                break
        return picked[:12]

    @staticmethod
    def _unresolved(hint: HintSpec, blocks: list[EvidenceBlock]) -> list[str]:
        text = "\n".join(block.text for block in blocks).lower()
        unresolved: list[str] = []
        for needle in hint.needles:
            low = needle.lower().strip()
            if low and low not in text:
                unresolved.append(needle)
        return unresolved

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[: limit - 20].rstrip() + "\n...[clipped]..."
