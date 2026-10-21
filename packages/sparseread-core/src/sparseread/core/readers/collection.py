"""Sparse reader for directories of small text files."""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from sparseread.core.denoise import denoise_text
from sparseread.core.models import EvidenceBlock, EvidencePack, HintSpec


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
        ".txt", ".md", ".markdown", ".rst", ".eml", ".html", ".htm",
        ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml",
        ".log", ".py", ".sh", ".toml", ".ini", ".cfg", ".conf",
    }
    _STOPWORDS = {
        "the", "and", "for", "with", "from", "that", "this", "into", "need",
        "find", "search", "email", "emails", "folder", "everything", "related",
        "all", "about", "create", "summary", "summarize", "report",
    }
    _SKIP_DIRS = {
        ".git", ".nanobot", ".openclaw", ".opencode", "node_modules", "__pycache__", ".pytest_cache",
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
        used = 0
        for name in names:
            item = item_by_name.get(name)
            if item is None:
                continue
            try:
                text = self._read_source_text(item.path)
            except OSError:
                continue
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
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="collection",
            summary=f"collection excerpt digest: {len(blocks)} source files summarized with task-relevant facts",
            skeleton=[self._skeleton_line(item) for item in items[:12]],
            evidence=blocks,
            unresolved=self._unresolved(hint, blocks),
            next_action={
                "allowed_next": ["write_file", "run short calculation from these excerpts", "verify specific missing fact only"],
                "instruction": "Use these source-keyed excerpts as evidence. Do not reread every file; verify only a named missing fact.",
                "covered_sources": [],
                "required_outputs": [],
                "overall_status": None,
            },
        )

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
            try:
                text = self._read_source_text(entry)
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
        if suffix in {".html", ".htm"}:
            return "html"
        if suffix.startswith("."):
            return suffix[1:]
        return "text"

    @staticmethod
    def _read_source_text(path: Path) -> str:
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        suffix = path.suffix.lower()
        if suffix in {".html", ".htm"}:
            return denoise_text(text, source_kind="html").text
        if suffix == ".log":
            return denoise_text(text, source_kind="log").text
        if suffix in {".txt", ".md", ".markdown", ".rst", ".eml"}:
            return denoise_text(text, source_kind="text").text
        return text

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
                text = self._read_source_text(item.path).strip()
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
            "blocked", "critical", "error", "failed", "failure", "retry",
            "timeout", "limit", "risk", "status", "version", "warning",
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
            "error", "exception", "failed", "failure", "fallback", "retry",
            "timeout", "threshold", "deprecated", "changelog", "known issue",
            "warning", "todo", "fixme",
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
                "bug", "bugs", "regression",
                "diagnose", "diagnosis", "investigate", "root cause",
                "cross-file", "configuration", "config", "log", "metrics", "rules",
            )
        )

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
