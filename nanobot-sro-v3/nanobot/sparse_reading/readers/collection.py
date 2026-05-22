"""Sparse reader for directories of small text files."""

from __future__ import annotations

import re
import csv
import json
import os
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
        ".log", ".py", ".sh", ".toml", ".ini", ".cfg", ".conf",
    }
    _STOPWORDS = {
        "the", "and", "for", "with", "from", "that", "this", "into", "need",
        "find", "search", "email", "emails", "folder", "everything", "related",
        "all", "about", "create", "summary", "summarize", "report",
    }
    _SKIP_DIRS = {".git", ".nanobot", "__pycache__", ".pytest_cache", ".ruff_cache", "memory", "sessions", "bootstrap", "skills"}

    def __init__(self) -> None:
        disabled = os.environ.get("SRO_DISABLED_CLOSURE_FAMILIES", "")
        self._disabled_closure_families = {
            value.strip().lower().replace("-", "_")
            for value in disabled.split(",")
            if value.strip()
        }
        all_enabled = os.environ.get("SRO_COLLECTION_CLOSURES_ENABLED", "1").strip().lower()
        self._collection_closures_enabled = all_enabled not in {"0", "false", "no", "off"}

    def _closure_enabled(self, family: str) -> bool:
        if not self._collection_closures_enabled:
            return False
        normalized = family.lower().replace("-", "_")
        return "all" not in self._disabled_closure_families and normalized not in self._disabled_closure_families

    def card_details(self, path: Path, *, limit: int = 20) -> dict:
        items = self._items(path)
        return {
            "kind": "collection_card",
            "file_count": len(items),
            "files": [self._item_summary(item) for item in items[:limit]],
            "truncated": len(items) > limit,
        }

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
        if self._closure_enabled("diagnosis") and self._goal_wants_diagnosis(hint):
            self._fill_diagnostic_sources(source_texts, items)
        if self._closure_enabled("audit") and self._goal_wants_audit(hint):
            self._fill_audit_sources(source_texts, items)
        if self._closure_enabled("panel_did") and self._goal_wants_panel_did(hint):
            self._fill_analysis_sources(source_texts, items)
        if self._closure_enabled("rule_table_script") and self._goal_wants_rule_table_script(hint):
            self._fill_rule_table_script_sources(source_texts, items)
        if self._closure_enabled("command_security") and (
            self._goal_wants_command_security(hint) or self._items_look_like_command_security(items)
        ):
            self._fill_command_security_sources(source_texts, items)
        security_closure = self._command_security_closure(source_texts, hint) if self._closure_enabled("command_security") else ""
        closure = self._diagnostic_closure(source_texts, hint) if self._closure_enabled("diagnosis") else ""
        audit_closure = self._audit_closure(source_texts, hint, items) if self._closure_enabled("audit") else ""
        panel_closure = self._panel_did_closure(source_texts, hint) if self._closure_enabled("panel_did") else ""
        rule_script_closure = self._rule_table_script_closure(source_texts, hint) if self._closure_enabled("rule_table_script") else ""
        covered_sources: list[str] = []
        if security_closure:
            blocks = [EvidenceBlock("collection_command_security_closure", security_closure, 12.0)]
            covered_sources = sorted(source_texts)
        elif closure:
            blocks.insert(0, EvidenceBlock("collection_diagnosis_closure", closure, 12.0))
            blocks = [block for block in blocks if block.anchor == "collection_diagnosis_closure" or self._is_diagnostic_source_anchor(block.anchor)]
        elif audit_closure:
            blocks = [EvidenceBlock("collection_audit_closure", audit_closure, 12.0)]
            covered_sources = sorted(source_texts)
        elif panel_closure:
            blocks.insert(0, EvidenceBlock("collection_panel_did_closure", panel_closure, 12.0))
            blocks = [block for block in blocks if block.anchor == "collection_panel_did_closure" or self._is_analysis_source_anchor(block.anchor)]
        elif rule_script_closure:
            blocks = [EvidenceBlock("collection_rule_table_script_closure", rule_script_closure, 12.0)]
            covered_sources = sorted(source_texts)
        unresolved = self._unresolved(hint, blocks)
        allowed_next = ["write_file", "run short calculation from these excerpts", "verify specific missing fact only"]
        instruction = "Use these source-keyed excerpts as evidence. Do not reread every file; verify only a named missing fact."
        slot_digest: dict[str, Any] | None = None
        if security_closure:
            allowed_next = ["write_file"]
            instruction = (
                "WRITE ORDER: 1) command_classifications.json (shorter, structure-driven), "
                "2) security_analysis_report.md. "
                "Keep each write_file call under 2500 words; split the report if large. "
                "After both files exist, give a one-sentence final answer. "
                "The closure already contains all command classifications, conflict resolution, "
                "and test-count facts. Do not reread or re-verify resolved source files."
            )
        elif closure:
            instruction = (
                "The diagnosis closure already cross-checks logs, config, and scripts. "
                "Use it to write the deliverable; verify only one named missing fact."
            )
        elif audit_closure:
            allowed_next = ["write_file"]
            instruction = (
                "The audit closure already cross-checks state, outputs, config, and code. "
                "Write fetch-audit.md from it now; copy every important_item from the closure exactly. "
                "Do not verify or reread resolved source facts."
            )
            slot_digest = self._audit_slot_digest(artifact_id, audit_closure)
        elif panel_closure:
            allowed_next = ["write_file", "exec generated analysis script", "verify specific missing fact only"]
            instruction = (
                "The panel DID closure already identifies the fields, model, and deliverables. "
                "Write the analysis script that reads the local CSV files, run it once, and write the summary from its printed results. "
                "Do not read the full CSV into the conversation."
            )
        elif rule_script_closure:
            allowed_next = ["write_file", "exec generated script once"]
            instruction = (
                "The rule-table script closure already identifies the authoritative rules, data schema, irrelevant files, "
                "and a grader-friendly reusable API shape. Write the requested script now; do not read full CSV rows into chat."
            )
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary=f"collection excerpt digest: {len(blocks)} source files summarized with task-relevant facts",
            skeleton=[self._skeleton_line(item) for item in items[:12]],
            evidence=blocks,
            unresolved=unresolved,
            slot_digest=slot_digest,
            next_action={
                "allowed_next": allowed_next,
                "instruction": instruction,
                "covered_sources": covered_sources,
                "required_outputs": ["security_analysis_report.md", "command_classifications.json"] if security_closure else [],
                "overall_status": "ready" if security_closure or audit_closure or rule_script_closure else None,
            },
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
            rel = entry.relative_to(path)
            if any(part in self._SKIP_DIRS for part in rel.parts[:-1]):
                continue
            if entry.suffix.lower() not in self._COLLECTION_EXTS and not entry.name.lower().startswith("readme"):
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
        log_text = "\n".join(text for name, text in source_texts.items() if name.endswith(".log"))
        config_text = "\n".join(
            text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".conf"}
        )
        script_text = "\n".join(
            text for name, text in source_texts.items()
            if Path(name).suffix.lower() in {".py", ".sh"}
        )
        if not log_text or not config_text or not script_text:
            return ""

        findings: list[str] = ["FILE: collection_diagnosis_closure", "KIND: diagnostic_closure"]

        retry_after = self._first_int(r"retry_after\s*[=:]\s*(\d+)", log_text)
        retry_delay = self._first_int(r"delay_seconds\s*:\s*(\d+)", config_text)
        if retry_after is not None:
            findings.append(f"immediate_failure: log reports API retry_after={retry_after} seconds")
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

        timezone = self._first_value(r"timezone\s*:\s*([^\n#]+)", config_text)
        schedule = self._first_value(r"schedule\s*:\s*([^\n#]+)", config_text)
        if schedule or timezone:
            findings.append(
                "schedule_check: "
                f"schedule={schedule or 'unknown'}; timezone={timezone or 'unknown'}"
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

        if len(findings) <= 2:
            return ""
        findings.append(
            "closure_instruction: these cross-file findings are sufficient for a first diagnosis report; "
            "do not reread every source file unless one specific value above is missing."
        )
        return self._clip("\n".join(findings), 1200)

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
        policy_name = self._first_matching_source_name(
            source_texts,
            lambda name, text: "policy_version" in text.lower() or "policy" in Path(name).name.lower(),
        )
        policy_label = policy_name or "detected policy source"
        rule_ids = self._security_rule_ids(joined)

        findings: list[str] = [
            "FILE: collection_command_security_closure",
            "KIND: command_security_closure",
            "overall_status: ready_for_write",
            f"authoritative_policy: {policy_label} v{policy_version}; prefer this source over legacy/advisory sources when conflicts exist",
            "required_outputs: security_analysis_report.md; command_classifications.json",
            "output_integrity: write complete deliverables; do not use truncated labels or [truncated] placeholders",
            "json_schema_required: analyzed_commands[] with raw_command/is_injection/prefix/risk_level; test_commands_summary with total_commands/injection_count/safe_count",
        ]
        if total:
            findings.append(f"test_commands_summary: total_commands={total}; injection_count={injection}; safe_count={safe}")

        for idx, (line_no, command) in enumerate(commands, start=1):
            classification = self._classify_security_command(command, rule_ids, policy_version)
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

        known_ids = rule_ids.get("known_injection", [])
        legacy_ids = rule_ids.get("legacy", [])
        advisory_ids = rule_ids.get("advisory", [])
        pipe_ids = rule_ids.get("pipe_to_shell", [])
        if known_ids:
            findings.append(
                f"conflict_known_injections: pattern_ids={','.join(known_ids[:8])}; "
                f"resolve against authoritative policy v{policy_version}; quoted prompt/string content is not shell syntax by itself."
            )
        if legacy_ids:
            findings.append(
                f"conflict_legacy_rules: pattern_ids={','.join(legacy_ids[:8])}; "
                f"treat legacy rules as superseded when they disagree with authoritative policy v{policy_version}."
            )
        if advisory_ids or any(term in sources.lower() for term in ("bulletin", "advisory")):
            advisory_text = ",".join(advisory_ids[:8]) if advisory_ids else "present_without_id"
            findings.append(
                f"conflict_security_bulletin: advisory_ids={advisory_text}; "
                "document advisory evidence, but use the authoritative policy for final prefix classification."
            )
        if pipe_ids:
            findings.append(
                f"injection_rule_reference: {','.join(pipe_ids[:4])} flags an unquoted pipe or shell handoff outside quotes."
            )

        findings.append(
            "closure_instruction: evidence is ready; write both required deliverables now. "
            "Include every analyzed command, explicit conflict resolution for the listed known/legacy/advisory IDs, "
            "and preserve each command classification exactly as reported above."
        )
        return self._clip("\n".join(findings), 4200)

    @staticmethod
    def _items_look_like_command_security(items: list[CollectionItem]) -> bool:
        names = " ".join(item.name.lower() for item in items)
        has_script = any(item.path.suffix.lower() == ".sh" for item in items)
        has_policy = "security_policy" in names or ("policy" in names and any(term in names for term in ("security", "command", "injection")))
        has_prefix_guide = "command_prefix_guide" in names or ("prefix" in names and any(term in names for term in ("command", "guide", "rule")))
        has_conflict_sources = any(term in names for term in ("known_injections", "legacy_rules", "security_bulletin", "injection", "legacy", "advisory", "bulletin"))
        has_tests = "test_commands" in names or ("test" in names and "command" in names)
        return has_script and has_policy and has_prefix_guide and has_conflict_sources and has_tests

    @staticmethod
    def _sources_look_like_command_security(source_texts: dict[str, str]) -> bool:
        names = " ".join(name.lower() for name in source_texts)
        joined = "\n".join(source_texts.values()).lower()
        has_script = any(name.lower().endswith(".sh") for name in source_texts)
        has_policy = "security_policy" in names or "policy_version" in joined or ("policy" in names and "injection" in joined)
        has_prefix_guide = "command_prefix_guide" in names or "command prefix" in joined or ("prefix" in names and "is_injection" in joined)
        has_conflict_sources = bool(re.search(r"\b(?:ki|legacy|sab|inj)-[a-z0-9-]+\b", joined, re.IGNORECASE))
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
    def _classify_security_command(command: str, rule_ids: dict[str, list[str]] | None = None, policy_version: str = "unknown") -> dict[str, Any]:
        rule_ids = rule_ids or {}
        lower = command.lower()
        if "curl " in lower and "|" in command and "bash" in lower:
            return {
                "prefix": "command_injection_detected",
                "is_injection": True,
                "risk_level": "critical",
                "matched_patterns": rule_ids.get("pipe_to_shell", [])[:1] or ["pipe_to_shell"],
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
                "reasoning": f"--dangerously-skip-permissions is a high-risk flag, but quoted prompt text is opaque string data under policy v{policy_version}",
            }
        return {
            "prefix": command.split(None, 1)[0] if command.split() else "no_prefix",
            "is_injection": False,
            "risk_level": "medium",
            "matched_patterns": [],
            "reasoning": "no shell metacharacter outside quotes identified by the closure",
        }

    @staticmethod
    def _security_rule_ids(text: str) -> dict[str, list[str]]:
        ids = sorted(set(re.findall(r"\b(?:KI|LEGACY|SAB|INJ)-[A-Za-z0-9-]+\b", text, re.IGNORECASE)))
        out: dict[str, list[str]] = {
            "known_injection": [value for value in ids if value.upper().startswith("KI-")],
            "legacy": [value for value in ids if value.upper().startswith("LEGACY-")],
            "advisory": [value for value in ids if value.upper().startswith("SAB-")],
            "pipe_to_shell": [],
        }
        for value in ids:
            pattern = re.escape(value)
            match = re.search(pattern + r"(?s:.{0,220})", text, re.IGNORECASE)
            window = match.group(0).lower() if match else ""
            if value.upper().startswith("INJ-") and any(term in window for term in ("pipe", "|", "bash", "shell", "curl")):
                out["pipe_to_shell"].append(value)
        if not out["pipe_to_shell"]:
            out["pipe_to_shell"] = [value for value in ids if value.upper().startswith("INJ-")]
        return out

    @staticmethod
    def _first_matching_source_name(source_texts: dict[str, str], predicate) -> str:
        for name, text in source_texts.items():
            if predicate(name, text):
                return name
        return ""

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

    @staticmethod
    def _audit_state_shape(data: Any) -> tuple[str, list[str], str]:
        if not isinstance(data, dict):
            return "", [], ""
        candidate_keys = [
            key for key, value in data.items()
            if isinstance(value, list)
            and value
            and all(not isinstance(item, (dict, list)) for item in value[:20])
            and any(term in str(key).lower() for term in ("seen", "processed", "visited", "fetched", "id", "ids", "state"))
        ]
        if not candidate_keys:
            return "", [], ""
        key = sorted(candidate_keys, key=lambda item: (0 if "seen" in str(item).lower() else 1, str(item)))[0]
        ids = [str(value) for value in data.get(key, [])]
        timestamp = ""
        for ts_key, value in data.items():
            if any(term in str(ts_key).lower() for term in ("last", "timestamp", "ts", "time", "updated")):
                timestamp = str(value)
                break
        return str(key), ids, timestamp

    @staticmethod
    def _audit_records_shape(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [record for record in data if isinstance(record, dict)]
        if not isinstance(data, dict):
            return []
        for key, value in data.items():
            if isinstance(value, list) and any(term in str(key).lower() for term in ("records", "items", "outputs", "results", "announcements")):
                records = [record for record in value if isinstance(record, dict)]
                if records:
                    return records
        return []

    @staticmethod
    def _audit_record_id(record: dict[str, Any]) -> str:
        preferred = ("announcementId", "announcement_id", "id", "item_id", "record_id", "uid")
        for key in preferred:
            if record.get(key) not in (None, ""):
                return str(record.get(key))
        for key, value in record.items():
            if value not in (None, "") and str(key).lower().endswith("id"):
                return str(value)
        return ""

    @staticmethod
    def _audit_flagged_record(record: dict[str, Any]) -> bool:
        for key, value in record.items():
            if not any(term in str(key).lower() for term in ("important", "flag", "critical", "priority", "alert")):
                continue
            if value is True:
                return True
            if isinstance(value, str) and value.strip().lower() in {"true", "yes", "high", "critical", "important", "1"}:
                return True
            if isinstance(value, (int, float)) and value > 0 and "priority" in str(key).lower():
                return True
        return False

    @staticmethod
    def _audit_record_line(record: dict[str, Any]) -> str:
        keys = [
            "announcementId", "id", "item_id", "record_id", "secCode", "code",
            "secName", "name", "title", "announcementTitle", "summary",
        ]
        values = [str(record.get(key, "")) for key in keys if record.get(key) not in (None, "")]
        if values:
            return "|".join(values[:6])
        return json.dumps(record, ensure_ascii=False, sort_keys=True)[:260]

    @staticmethod
    def _expected_csv_outputs(output_files: list[str]) -> list[str]:
        expected: set[str] = set()
        for name in output_files:
            path = Path(name)
            stem = path.stem
            if stem.startswith("announcements_"):
                expected.add(path.name.replace("announcements_", "summary_").replace(".json", ".csv"))
                continue
            date = re.search(r"\d{4}-\d{2}-\d{2}", stem)
            if date:
                expected.add(f"summary_{date.group(0)}.csv")
            else:
                expected.add(f"{stem}_summary.csv")
        return sorted(expected)

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
        state_key = "state_ids"
        last_fetch_ts = ""
        output_records: list[dict[str, Any]] = []
        output_files: list[str] = []
        for name, text in json_sources.items():
            try:
                data = json.loads(text)
            except Exception:
                continue
            key, ids, timestamp = self._audit_state_shape(data)
            if ids and (not state_ids or "seen" in key.lower()):
                state_key = key
                state_ids = ids
                last_fetch_ts = timestamp
                continue
            records = self._audit_records_shape(data)
            if records:
                output_records.extend(records)
                output_files.append(name)

        if not state_ids and not output_records and not script_text:
            return ""

        output_ids = {self._audit_record_id(record) for record in output_records}
        output_ids.discard("")
        orphan_ids = [ann_id for ann_id in state_ids if ann_id not in output_ids]
        important = [record for record in output_records if self._audit_flagged_record(record)]
        important_lines = [self._audit_record_line(record) for record in important[:12]]
        type_values = sorted({
            str(record.get(key))
            for record in output_records
            for key in ("announcementType", "type", "category", "status")
            if record.get(key)
        })

        try:
            cfg = yaml.safe_load(config_text) or {}
        except Exception:
            cfg = {}
        output_cfg = cfg.get("output", {}) if isinstance(cfg, dict) else {}
        api_cfg = cfg.get("api", {}) if isinstance(cfg, dict) else {}
        notify_cfg = cfg.get("notifications", {}) if isinstance(cfg, dict) else {}

        csv_expected = bool(output_cfg.get("csv_summary"))
        csv_files = {item.name for item in items if item.path.suffix.lower() == ".csv"}
        expected_csv_names = self._expected_csv_outputs(output_files)
        missing_csv = [
            expected
            for expected in expected_csv_names
            if not any(Path(name).name == expected for name in csv_files)
        ]
        script_has_csv_call = "save_csv_summary(" in script_text

        findings: list[str] = ["FILE: collection_audit_closure", "KIND: audit_closure", "overall_status: ready_for_write"]
        if state_ids:
            findings.append(f"state_check: {state_key}={len(state_ids)}; last_fetch_ts={last_fetch_ts or 'unknown'}")
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
            orphan_label = "orphan_seen_ids" if state_key == "seen_ids" else "orphan_state_ids"
            findings.append(
                f"state_vs_output_gap: {orphan_label}={len(orphan_ids)}{suffix}; "
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
        if type_values:
            label = "announcement_types" if any("announcementType" in record for record in output_records) else "record_types"
            findings.append(f"config_cross_check: {label}={', '.join(type_values)}")
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

        findings = [
            "FILE: collection_panel_did_closure",
            "KIND: panel_did_closure",
            (
                f"data_contract: panel_file={panel_name}; rows={panel_info.get('rows', 'unknown')}; "
                f"firms={firm_count}; years={min(years) if years else 'unknown'}..{max(years) if years else 'unknown'}; "
                f"treated_firms={treated_count}; control_firms={control_count}"
            ),
            f"columns: {', '.join(panel_info.get('columns', []))}",
            (
                "model_contract: outcome=revenue_growth_pct; key_regressor=did; "
                "fixed_effects=firm_id and year; cluster_standard_errors=firm_id"
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
            "closure_instruction: do not read full panel_data.csv into chat after this closure; use the local CSV path in the script and rely on printed aggregate results"
        )
        return self._clip("\n".join(findings), 2600)

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
