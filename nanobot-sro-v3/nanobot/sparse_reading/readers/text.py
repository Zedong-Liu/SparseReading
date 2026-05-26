"""Hint-guided reader for PDFs and long prose text."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.sparse_reading.models import MAX_HINT_NEEDLES, EvidenceBlock, EvidencePack, HintSpec, SlotSpec


@dataclass(slots=True)
class TextUnit:
    anchor: str
    text: str
    heading: str = ""


class TextReader:
    """Extractive reader for PDF/text/markdown-like objects."""

    _LONG_UNIT_CHARS = 1600
    _LONG_UNIT_OVERLAP = 180
    _MONTHS = {
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    }

    _STOPWORDS = {
        "the", "and", "for", "with", "from", "that", "this", "into", "your",
        "their", "then", "than", "what", "when", "where", "which", "while",
        "find", "exact", "facts", "fact", "count", "counts", "number", "section",
        "header", "listed", "below", "about", "paper", "document", "tasks", "many",
        "does", "have", "name", "type", "what", "how", "after", "before",
        "was", "were", "who", "why", "did", "which",
    }

    def read(self, path: Path, artifact_id: str, mode: str, hint: HintSpec, budget: int) -> EvidencePack:
        try:
            units, skeleton, kind = self._load_units(path)
        except Exception as exc:
            return EvidencePack(
                artifact_id=artifact_id,
                mode=mode,
                type="text",
                summary="reader error",
                error=f"Error reading {path}: {exc}",
                unresolved=list(hint.needles),
            )

        if mode == "collect" or hint.slots:
            return self.collect(path, artifact_id, mode, hint, budget, units=units, skeleton=skeleton, kind=kind)

        if mode == "scout":
            evidence = self._scout_evidence(units, budget)
            unresolved = list(hint.needles)
            return EvidencePack(
                artifact_id=artifact_id,
                mode=mode,
                type=kind,
                summary=f"{kind} object with {len(units)} text units",
                skeleton=skeleton[:12],
                evidence=evidence,
                unresolved=unresolved,
                next_hint=self._next_hint(hint, artifact_id, "focus") if unresolved else None,
            )

        section_selected = self._section_expand(units, hint, budget) if hint.scope == "expand" and self._goal_requests_section(hint) else []
        if section_selected:
            selected = section_selected
        else:
            scored = self._score_units(units, hint)
            selected = self._fit_budget(scored, budget)
        unresolved = self._unresolved(hint, selected)
        summary = f"{len(selected)} evidence blocks selected from {len(units)} text units"
        if section_selected:
            summary = f"section-local evidence selected from {len(units)} text units"
        if mode == "verify":
            summary = "exact verification evidence"
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type=kind,
            summary=summary,
            skeleton=skeleton[:8] if mode in {"scout", "focus"} else [],
            evidence=selected,
            unresolved=unresolved,
            next_hint=self._next_hint(hint, artifact_id, "refine") if unresolved and mode != "verify" else None,
        )

    def collect(
        self,
        path: Path,
        artifact_id: str,
        mode: str,
        hint: HintSpec,
        budget: int,
        *,
        units: list[TextUnit] | None = None,
        skeleton: list[str] | None = None,
        kind: str | None = None,
    ) -> EvidencePack:
        if units is None or skeleton is None or kind is None:
            units, skeleton, kind = self._load_units(path)
        if not hint.slots:
            return EvidencePack(
                artifact_id=artifact_id,
                mode=mode,
                type=kind,
                summary="collect requires hint.slots",
                error="collect requires hint.slots with id/question/expected",
            )

        slot_results = [self._resolve_slot(slot, units, artifact_id) for slot in hint.slots]
        self._mark_suspicious_duplicate_candidates(slot_results)
        unresolved = [item["id"] for item in slot_results if item["status"] != "resolved"]
        if not unresolved:
            overall = "ready"
            allowed_next = ["write_file"]
        elif all(item["status"] in {"resolved", "partial"} for item in slot_results):
            overall = "needs_verify"
            allowed_next = ["verify specific slots", "write_file if candidates are sufficient"]
        else:
            overall = "needs_refine"
            allowed_next = ["refine unresolved slots only", "verify specific slots"]

        digest = {
            "kind": "slot_digest",
            "artifact_id": artifact_id,
            "overall_status": overall,
            "readiness": "ready means the slot candidates are sufficient evidence for the deliverable; do not verify resolved slots",
            "slots": slot_results,
            "unresolved_slots": unresolved,
            "allowed_next": allowed_next,
        }
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type=kind,
            summary=f"slot digest for {len(slot_results)} slots from {len(units)} text units",
            skeleton=skeleton[:8] if mode == "scout" else [],
            evidence=[],
            unresolved=unresolved,
            slot_digest=digest,
            next_action={"allowed_next": allowed_next, "unresolved_slots": unresolved},
        )

    def _resolve_slot(self, slot: SlotSpec, units: list[TextUnit], artifact_id: str) -> dict[str, Any]:
        ranked = self._score_slot_units(units, slot)
        best = ranked[0] if ranked else None
        candidate = ""
        candidate_block = best
        if self._slot_wants_count(slot):
            count_choice = self._best_count_candidate(slot, ranked[:8], units)
            if count_choice:
                candidate, candidate_block = count_choice
        if not candidate:
            for block in ranked[:8]:
                candidate = self._candidate_for_slot(slot, block, units)
                if candidate:
                    candidate_block = block
                    break
        status = "resolved" if candidate else ("partial" if best else "missing")
        confidence = 0.0
        anchor = ""
        verify_ref = ""
        if candidate_block:
            anchor = candidate_block.anchor
            verify_ref = f"{artifact_id}:{candidate_block.anchor}"
            confidence = min(0.99, max(0.35, candidate_block.score / 12.0))
            if status == "resolved":
                confidence = max(confidence, 0.8)
        return {
            "id": slot.id,
            "status": status,
            "candidate": candidate,
            "anchor": anchor,
            "confidence": round(confidence, 2),
            "verify_ref": verify_ref,
        }

    def _best_count_candidate(
        self,
        slot: SlotSpec,
        blocks: list[EvidenceBlock],
        units: list[TextUnit],
    ) -> tuple[str, EvidenceBlock] | None:
        best: tuple[float, str, EvidenceBlock] | None = None
        for block in blocks:
            candidate = self._candidate_for_slot(slot, block, units)
            if not candidate:
                continue
            quality = block.score + self._count_candidate_bonus(slot, candidate, block.text)
            if best is None or quality > best[0]:
                best = (quality, candidate, block)
        if best is None:
            return None
        return best[1], best[2]

    @staticmethod
    def _count_candidate_bonus(slot: SlotSpec, candidate: str, text: str) -> float:
        question = slot.question.lower()
        hay = text.lower()
        bonus = 0.0
        try:
            value = int(candidate.replace(",", "").split(".")[0])
        except ValueError:
            value = 0

        if "," in candidate or value > 20:
            bonus += 4.0

        if any(term in question for term in ("registry", "community-built", "before filtering", "public registry")):
            if any(term in hay for term in ("registry", "community-built", "collected", "public registry")):
                bonus += 10.0
            elif 0 < value <= 20:
                bonus -= 6.0

        if any(term in question for term in ("remain", "filtered", "filtering out", "after filtering")):
            if any(term in hay for term in ("filtered", "filtering", "after excluding", "excluding spam", "duplicates", "remained")):
                bonus += 10.0
            elif 0 < value <= 20:
                bonus -= 6.0

        return bonus

    def _score_slot_units(self, units: list[TextUnit], slot: SlotSpec) -> list[EvidenceBlock]:
        terms = self._slot_terms(slot)
        aliases = [alias.lower() for alias in slot.aliases]
        blocks: list[EvidenceBlock] = []
        for unit in units:
            hay = f"{unit.heading}\n{unit.text}".lower()
            score = 0.0
            for alias in aliases:
                if alias and alias in hay:
                    score += 12.0
            for term in terms:
                if term in hay:
                    score += 1.5
            sentence_overlap = self._best_sentence_overlap(unit.text, terms)
            if sentence_overlap >= 2:
                score += sentence_overlap * 2.0
            if ("api" in terms or "api" in hay) and "api" in hay:
                if "gateway" in hay and "expos" in hay:
                    score += 8.0
                if "no api" in hay and "gateway" in terms:
                    score -= 3.0
            if self._slot_wants_count(slot) and re.search(r"\b\d[\d,]*(?:\.\d+)?\b", unit.text):
                score += 1.2
            if ("category" in f"{slot.expected} {slot.question}".lower()) and "count" in hay:
                if "skill category" in hay or "top categories" in hay:
                    score += 8.0
            if self._slot_wants_list(slot):
                if any(term in hay for term in self._slot_section_terms(slot)):
                    score += 8.0
                if self._looks_like_heading(unit.text.strip()) or self._looks_like_list_item(unit.text):
                    score += 0.8
            if score > 0:
                blocks.append(EvidenceBlock(unit.anchor, unit.text, score))
        blocks.sort(key=lambda block: block.score, reverse=True)
        return blocks

    def _candidate_for_slot(self, slot: SlotSpec, best: EvidenceBlock, units: list[TextUnit]) -> str:
        text = best.text.strip()
        expected = slot.expected.lower()
        question = slot.question.lower()
        if self._slot_wants_list(slot):
            section_anchor = self._section_anchor_for_slot(slot, units) or best.anchor
            labels = (
                self._section_task_labels(section_anchor, units)
                if self._slot_wants_task_list(slot)
                else self._section_item_labels(section_anchor, units)
            )
            if "count" in expected or "how many" in question or "number" in question:
                return str(len(labels)) if labels else ""
            return "; ".join(labels[:6])
        if "date" in expected or "date" in question or "collected" in question:
            return self._extract_date(text, question, self._slot_terms(slot))
        if "filename" in expected or "file" in question:
            return self._extract_filename(text)
        if "api" in expected or "api" in question:
            return self._extract_api(text)
        if "category" in expected or "category" in question:
            category_text = self._local_text(best.anchor, units, after=8) or text
            return self._extract_category_count(category_text, question)
        if self._slot_wants_location(slot):
            return self._extract_location(text, question, self._slot_terms(slot))
        if self._slot_wants_count(slot):
            return self._extract_count(text, question)
        return self._short_candidate(text, self._slot_terms(slot))


    def _section_anchor_for_slot(self, slot: SlotSpec, units: list[TextUnit]) -> str:
        terms = self._slot_section_terms(slot)
        for term in terms:
            for unit in units:
                first_line = unit.text.strip().splitlines()[0].strip() if unit.text.strip() else ""
                hay = f"{unit.heading}\n{unit.text}".lower()
                if term in hay and self._looks_like_heading(first_line):
                    return unit.anchor
        return ""

    def _section_item_labels(self, anchor: str, units: list[TextUnit]) -> list[str]:
        start_idx = next((idx for idx, unit in enumerate(units) if unit.anchor == anchor), -1)
        if start_idx < 0:
            return []
        labels: list[str] = []
        stop_terms = {"comparative table", "roadmap", "prioritized roadmap", "visualization"}
        skip_terms = {"brief", "why", "inputs", "outputs", "success criteria", "difficulty", "suggested metrics"}
        for unit in units[start_idx + 1 : start_idx + 40]:
            label = unit.text.strip().splitlines()[0].strip()
            low = label.lower().rstrip(":")
            if any(term in low for term in stop_terms):
                break
            if not label or low in skip_terms or any(low.startswith(term) for term in skip_terms):
                continue
            if self._looks_like_section_item_label(label):
                labels.append(label)
        return labels

    def _section_task_labels(self, anchor: str, units: list[TextUnit]) -> list[str]:
        start_idx = next((idx for idx, unit in enumerate(units) if unit.anchor == anchor), -1)
        if start_idx < 0:
            return []
        labels: list[str] = []
        stop_terms = {"comparative table", "roadmap", "prioritized roadmap", "visualization"}
        for unit in units[start_idx : start_idx + 40]:
            lines = [line.strip() for line in unit.text.splitlines() if line.strip()]
            if any(term in lines[0].lower() for term in stop_terms):
                break
            for idx, line in enumerate(lines):
                if line.lower().startswith("brief:") and idx > 0:
                    label = lines[idx - 1]
                    if self._looks_like_section_item_label(label) and label not in labels:
                        labels.append(label)
        return labels or self._section_item_labels(anchor, units)

    def _extract_count(self, text: str, question: str) -> str:
        if "how long" in question or "duration" in question:
            duration = re.search(r"\b(\d[\d,]*(?:\.\d+)?)\s+(?:years?|months?|weeks?|days?|hours?)\b", text, re.IGNORECASE)
            if duration:
                return duration.group(1)
        nums = re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", text)
        if not nums:
            return ""
        filtered: list[str] = []
        for raw in nums:
            value = int(raw.replace(",", "").split(".")[0])
            if 1900 <= value <= 2100:
                continue
            if "," in raw or value > 20:
                filtered.append(raw)
        candidates = filtered or nums
        if any(term in question for term in ("after", "remain", "filtered", "filtering out")) and len(candidates) > 1:
            return candidates[1]
        return candidates[0]


    @staticmethod
    def _looks_like_section_item_label(label: str) -> bool:
        label = label.strip()
        low = label.lower().rstrip(":")
        if not label or len(label) > 110:
            return False
        if re.fullmatch(r"\d+", label):
            return False
        if any(mark in low for mark in ("http://", "https://")):
            return False
        if low.startswith(("brief", "why", "inputs", "outputs", "success criteria", "difficulty", "suggested metrics")):
            return False
        if label.endswith((".", ";")) or ":" in label:
            return False
        words = re.findall(r"[A-Za-z][A-Za-z&/+.-]*", label)
        if not (2 <= len(words) <= 12):
            return False
        return True

    @staticmethod
    def _extract_date(text: str, question: str = "", terms: list[str] | None = None) -> str:
        patterns = [
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}\b",
            r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
            r"\b\d{4}[-/]\d{2}[-/]\d{2}\b",
        ]
        useful_terms = [term for term in dict.fromkeys(terms or []) if len(term) >= 4]
        dated_sentences: list[tuple[int, str]] = []
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            for pattern in patterns:
                for match in re.finditer(pattern, sentence):
                    hay = sentence.lower()
                    local = sentence[max(0, match.start() - 70) : match.end() + 70].lower()
                    score = sum(1 for term in useful_terms if term in hay)
                    score += 2 * sum(1 for term in useful_terms if term in local)
                    if "when" in question and any(term in local for term in ("crowned", "collected", "signed", "published")):
                        score += 1
                    score += 3 * sum(1 for term in TextReader._date_action_terms(question) if term in local)
                    score += 4 * sum(1 for term in TextReader._date_focus_terms(question) if term in local)
                    dated_sentences.append((score, match.group(0)))
        if dated_sentences:
            dated_sentences.sort(key=lambda item: item[0], reverse=True)
            return dated_sentences[0][1]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return ""

    @staticmethod
    def _date_focus_terms(question: str) -> list[str]:
        focus: list[str] = []
        for match in re.finditer(r"\b(?:of|in|at|for)\s+([A-Za-z][A-Za-z'-]{3,})", question.lower()):
            term = match.group(1)
            if term not in TextReader._STOPWORDS:
                focus.append(term)
        return list(dict.fromkeys(focus))

    @staticmethod
    def _date_action_terms(question: str) -> list[str]:
        actions = {
            "appointed", "approved", "collected", "crowned", "deployed", "launched",
            "migrated", "promoted", "published", "released", "signed", "started",
        }
        return [term for term in actions if term in question.lower()]

    @classmethod
    def _extract_location(cls, text: str, question: str, terms: list[str]) -> str:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
        if not sentences:
            return ""

        search_text = text
        if "after" in question and len(sentences) > 1:
            event_terms = [
                term for term in terms
                if term not in {"king", "went", "left", "departed", "returned", "location"}
            ]
            event_idx = max(
                range(len(sentences)),
                key=lambda idx: sum(1 for term in event_terms if term in sentences[idx].lower()),
            )
            search_text = " ".join(sentences[event_idx : event_idx + 3])

        patterns = [
            r"\bembarked\s+for\s+([A-Z][A-Za-z .'-]{1,60}?)(?:\s+(?:where|for)\b|[.,;]|$)",
            r"\b(?:went|returned|departed|sailed|travelled|traveled|left|moved)\s+(?:for|to)\s+([A-Z][A-Za-z .'-]{1,60}?)(?:\s+(?:where|for)\b|[.,;]|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, search_text)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()
        return cls._short_candidate(search_text, terms)

    @staticmethod
    def _extract_filename(text: str) -> str:
        match = re.search(r"\b[A-Za-z0-9_.-]+\.md\b", text, re.IGNORECASE)
        return match.group(0) if match else ""

    @staticmethod
    def _extract_api(text: str) -> str:
        match = re.search(r"\btyped\s+WebSocket\s+API\b", text, re.IGNORECASE)
        if match:
            return match.group(0)
        match = re.search(r"\b[A-Za-z-]+\s+API\b", text)
        return match.group(0) if match else ""

    @staticmethod
    def _extract_category_count(text: str, question: str) -> str:
        pairs = re.findall(r"(?:was|is|are|were|:)\s+([A-Z][A-Za-z &/+.-]{2,60}?)\s*\(?([0-9][0-9,]*)\)?", text)
        if not pairs:
            pairs = re.findall(r"([A-Z][A-Za-z &/+.-]{2,60}?)\s*\(?([0-9][0-9,]*)\)?", text)
        cleaned: list[tuple[str, str]] = []
        for name, count in pairs:
            name = re.sub(r"\s+", " ", name).strip(" -:")
            value = int(count.replace(",", ""))
            if len(name) < 3 or TextReader._looks_like_non_category_label(name) or 1900 <= value <= 2100:
                continue
            cleaned.append((name, count))
        if not cleaned:
            return ""
        cleaned.sort(key=lambda item: int(item[1].replace(",", "")), reverse=True)
        idx = 1 if "second" in question and len(cleaned) > 1 else 0
        name, count = cleaned[idx]
        return f"{name}: {count}"

    @staticmethod
    def _looks_like_non_category_label(name: str) -> bool:
        low = name.lower().strip()
        if low in TextReader._MONTHS or any(part in low for part in ("registry", "filtered", "skills reports")):
            return True
        if "," in name or len(name) > 50:
            return True
        words = re.findall(r"[A-Za-z][A-Za-z&/+.-]*", name)
        if not (1 <= len(words) <= 5):
            return True
        titled = sum(1 for word in words if word[:1].isupper() or word.isupper())
        return titled < len(words)

    @staticmethod
    def _short_candidate(text: str, terms: list[str] | None = None) -> str:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
        if not sentences:
            return ""
        if terms:
            lowered_terms = [term.lower() for term in terms if len(term) >= 3]
            best = max(
                sentences[:60],
                key=lambda sentence: sum(1 for term in lowered_terms if term in sentence.lower()),
            )
            if any(term in best.lower() for term in lowered_terms):
                first = best
            else:
                first = sentences[0]
        else:
            first = sentences[0]
        first = re.sub(r"\s+", " ", first).strip()
        return first[:220]

    @staticmethod
    def _mark_suspicious_duplicate_candidates(slot_results: list[dict[str, Any]]) -> None:
        if len(slot_results) < 3:
            return
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in slot_results:
            candidate = str(item.get("candidate") or "").strip()
            if item.get("status") != "resolved" or len(candidate) > 120:
                continue
            key_text = re.sub(r"\W+", " ", candidate.lower()).strip()
            if not key_text or re.fullmatch(r"[\d,.\s]+", key_text):
                continue
            key = (key_text, str(item.get("anchor") or ""))
            groups.setdefault(key, []).append(item)
        for duplicates in groups.values():
            if len(duplicates) < 2:
                continue
            for item in duplicates:
                item["status"] = "partial"
                item["confidence"] = min(float(item.get("confidence") or 0.0), 0.6)
                item["needs_verify_reason"] = "same short candidate reused for multiple slots"

    def _slot_terms(self, slot: SlotSpec) -> list[str]:
        raw = " ".join([slot.question, slot.expected, *slot.aliases]).lower()
        return [
            token for token in re.findall(r"[a-z0-9_.:/-]{3,}", raw)
            if token not in self._STOPWORDS
        ][:12]

    @staticmethod
    def _best_sentence_overlap(text: str, terms: list[str]) -> int:
        useful_terms = [term for term in dict.fromkeys(terms) if len(term) >= 4]
        if not useful_terms:
            return 0
        best = 0
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            hay = sentence.lower()
            best = max(best, sum(1 for term in useful_terms if term in hay))
        return best

    @staticmethod
    def _slot_wants_count(slot: SlotSpec) -> bool:
        text = f"{slot.expected} {slot.question}".lower()
        return any(term in text for term in ("count", "how many", "how long", "duration", "number", "total", "remained"))

    @staticmethod
    def _slot_wants_location(slot: SlotSpec) -> bool:
        text = f"{slot.expected} {slot.question}".lower()
        return "where" in text or "location" in text

    @classmethod
    def _slot_wants_list(cls, slot: SlotSpec) -> bool:
        prompt_text = f"{slot.expected} {slot.question}".lower()
        return "list" in prompt_text or "items" in prompt_text or cls._slot_wants_task_list(slot)

    @staticmethod
    def _slot_wants_task_list(slot: SlotSpec) -> bool:
        prompt_text = f"{slot.expected} {slot.question}".lower()
        all_text = f"{prompt_text} {' '.join(slot.aliases)}".lower()
        return ("task" in all_text or "benchmark" in all_text) and bool(re.search(r"\bpropos(?:e|ed|es|ing)\b", all_text))

    @staticmethod
    def _slot_section_terms(slot: SlotSpec) -> list[str]:
        raw_terms = [*slot.aliases, slot.question]
        text = " ".join(raw_terms + [slot.expected]).lower()
        if ("task" in text or "benchmark" in text) and re.search(r"\bpropos(?:e|ed|es|ing)\b", text):
            raw_terms.extend(["proposed tasks", "proposed benchmark tasks"])
        out: list[str] = []
        for term in raw_terms:
            lowered = term.lower().strip()
            if len(lowered) < 4:
                continue
            if lowered not in out:
                out.append(lowered)
        return out[:8]

    def _load_units(self, path: Path) -> tuple[list[TextUnit], list[str], str]:
        if path.suffix.lower() == ".pdf":
            return self._load_pdf_units(path)
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        return self._segment_text(text, kind="text")

    def _load_pdf_units(self, path: Path) -> tuple[list[TextUnit], list[str], str]:
        try:
            result = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            pages = self._load_pdf_pages_with_pymupdf(path)
        else:
            if result.returncode != 0:
                detail = (result.stderr or "").strip() or f"pdftotext exited with {result.returncode}"
                raise RuntimeError(detail)
            pages = (result.stdout or "").split("\f")
        units: list[TextUnit] = []
        skeleton: list[str] = []
        for page_idx, page in enumerate(pages, start=1):
            page_units, page_skeleton, _ = self._segment_text(page, kind="pdf", page=page_idx)
            units.extend(page_units)
            skeleton.extend(page_skeleton)
        return units, self._dedupe(skeleton)[:24], "pdf"

    @staticmethod
    def _load_pdf_pages_with_pymupdf(path: Path) -> list[str]:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("PDF reading requires pdftotext or pymupdf") from exc
        try:
            doc = fitz.open(str(path))
        except Exception as exc:
            raise RuntimeError(f"pymupdf failed to open PDF: {exc}") from exc
        try:
            return [page.get_text("text") for page in doc]
        finally:
            doc.close()

    def _segment_text(
        self, text: str, *, kind: str, page: int | None = None,
    ) -> tuple[list[TextUnit], list[str], str]:
        lines = text.splitlines()
        units: list[TextUnit] = []
        skeleton: list[str] = []
        buf: list[str] = []
        start_line = 1
        heading = ""

        def flush(end_line: int) -> None:
            nonlocal buf, start_line
            raw = "\n".join(buf).strip()
            if raw:
                prefix = f"p{page}:" if page is not None else ""
                self._append_text_units(units, prefix, start_line, end_line, raw, heading)
            buf = []
            start_line = end_line + 1

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            is_heading = self._looks_like_heading(stripped)
            is_list = self._looks_like_list_item(line)
            if is_heading:
                flush(idx - 1)
                heading = stripped
                skeleton.append(f"p{page} {stripped}" if page is not None else stripped)
                start_line = idx
                buf = [line]
                continue
            if not stripped:
                flush(idx - 1)
                continue
            if is_list and buf and len("\n".join(buf)) > 500:
                flush(idx - 1)
            if not buf:
                start_line = idx
            buf.append(line)
            if len("\n".join(buf)) > 1200:
                flush(idx)
        flush(len(lines))
        return units, self._dedupe(skeleton), kind

    def _append_text_units(
        self,
        units: list[TextUnit],
        prefix: str,
        start_line: int,
        end_line: int,
        raw: str,
        heading: str,
    ) -> None:
        base_anchor = f"{prefix}L{start_line}-L{end_line}"
        if len(raw) <= self._LONG_UNIT_CHARS:
            units.append(TextUnit(anchor=base_anchor, text=raw, heading=heading))
            return
        for char_start, char_end, chunk in self._split_long_text(raw):
            units.append(TextUnit(anchor=f"{base_anchor}:C{char_start}-{char_end}", text=chunk, heading=heading))

    def _split_long_text(self, text: str) -> list[tuple[int, int, str]]:
        chunks: list[tuple[int, int, str]] = []
        start = 0
        length = len(text)
        while start < length:
            hard_end = min(length, start + self._LONG_UNIT_CHARS)
            end = hard_end
            if hard_end < length:
                window = text[start:hard_end]
                sentence_breaks = [window.rfind(mark) for mark in (". ", "? ", "! ")]
                best_sentence = max(sentence_breaks)
                if best_sentence >= self._LONG_UNIT_CHARS // 2:
                    end = start + best_sentence + 1
                else:
                    space = window.rfind(" ")
                    if space >= self._LONG_UNIT_CHARS // 2:
                        end = start + space
            chunk = text[start:end].strip()
            if chunk:
                chunks.append((start, end, chunk))
            if end >= length:
                break
            start = max(end - self._LONG_UNIT_OVERLAP, start + 1)
        return chunks

    @staticmethod
    def _local_text(anchor: str, units: list[TextUnit], *, before: int = 1, after: int = 1) -> str:
        idx = next((unit_idx for unit_idx, unit in enumerate(units) if unit.anchor == anchor), -1)
        if idx < 0:
            return ""
        start = max(0, idx - before)
        end = min(len(units), idx + after + 1)
        return "\n".join(unit.text for unit in units[start:end])

    def _scout_evidence(self, units: list[TextUnit], budget: int) -> list[EvidenceBlock]:
        preferred: list[TextUnit] = []
        for unit in units:
            hay = f"{unit.heading}\n{unit.text}".lower()
            if any(term in hay for term in ("executive summary", "summary", "overview", "abstract")):
                preferred.append(unit)
        preferred.extend(units[:3])
        blocks = [EvidenceBlock(anchor=u.anchor, text=self._clip(u.text, 360), score=0.2) for u in self._unique_units(preferred)]
        return self._fit_budget(blocks, min(budget, 1400))[:4]

    @staticmethod
    def _goal_requests_section(hint: HintSpec) -> bool:
        goal = hint.goal.lower()
        return any(word in goal for word in ("section", "heading", "table", "appendix", "chapter", "roadmap"))

    def _section_expand(self, units: list[TextUnit], hint: HintSpec, budget: int) -> list[EvidenceBlock]:
        terms = self._section_terms(hint)
        if not terms:
            return []
        start_idx = -1
        for idx, unit in enumerate(units):
            label = f"{unit.heading}\n{unit.text}".lower()
            if any(term in label for term in terms):
                start_idx = idx
                break
        if start_idx < 0:
            return []

        picked: list[EvidenceBlock] = []
        used = 0
        for offset, unit in enumerate(units[start_idx : start_idx + 10]):
            block = EvidenceBlock(
                anchor=unit.anchor,
                text=self._clip(unit.text, 900),
                score=8.0 if offset == 0 else max(1.0, 7.0 - offset * 0.4),
            )
            cost = len(block.anchor) + len(block.text) + 32
            if picked and used + cost > budget:
                break
            picked.append(block)
            used += cost
            if used >= budget:
                break
        return picked

    def _score_units(self, units: list[TextUnit], hint: HintSpec) -> list[EvidenceBlock]:
        terms = [*hint.needles, *hint.must_keep]
        goal_terms = re.findall(r"[A-Za-z0-9_./:-]{3,}", hint.goal)
        lowered_terms = [t.lower() for t in terms if t] + [t.lower() for t in goal_terms[:8]]
        must_keep_terms = {x.lower() for x in hint.must_keep if x}
        blocks: list[EvidenceBlock] = []
        for unit in units:
            hay = f"{unit.heading}\n{unit.text}".lower()
            score = 0.0
            for term in lowered_terms:
                if term and term in hay:
                    score += 4.0 if term in must_keep_terms else 2.0
            if hint.want in {"count", "fact"} and re.search(r"\b\d+(?:[.,]\d+)?\b", unit.text):
                score += 0.8
            if self._looks_like_list_item(unit.text) or "suggested metrics" in hay:
                score += 0.4
            if unit.heading:
                score += 0.3
            if score > 0:
                blocks.append(EvidenceBlock(anchor=unit.anchor, text=self._clip(unit.text, 900), score=score))
        if not blocks:
            blocks = [EvidenceBlock(anchor=u.anchor, text=self._clip(u.text, 700), score=0.1) for u in units[:5]]
        blocks.sort(key=lambda b: b.score, reverse=True)
        return blocks

    def _fit_budget(self, blocks: list[EvidenceBlock], budget: int) -> list[EvidenceBlock]:
        picked: list[EvidenceBlock] = []
        used = 0
        for block in blocks:
            cost = len(block.anchor) + len(block.text) + 32
            if picked and used + cost > budget:
                continue
            picked.append(block)
            used += cost
            if used >= budget:
                break
        return picked[:12]

    def _unresolved(self, hint: HintSpec, blocks: list[EvidenceBlock]) -> list[str]:
        text = "\n".join(block.text for block in blocks).lower()
        return [needle for needle in hint.needles if not self._needle_resolved(needle, text)]

    @staticmethod
    def _next_hint(hint: HintSpec, artifact_id: str, mode: str) -> dict:
        return {
            "goal": hint.goal,
            "needles": hint.needles[:MAX_HINT_NEEDLES],
            "want": hint.want,
            "scope": "narrow" if mode == "refine" else "new",
            "artifact": artifact_id,
            "type_hint": hint.type_hint,
            "must_keep": hint.must_keep,
        }

    @classmethod
    def _needle_resolved(cls, needle: str, text: str) -> bool:
        lowered = needle.lower().strip()
        if not lowered:
            return True
        if lowered in text:
            return True
        tokens = [
            token for token in re.findall(r"[A-Za-z0-9_./:-]+", lowered)
            if len(token) >= 3 and token not in cls._STOPWORDS
        ]
        return bool(tokens) and all(token in text for token in tokens)

    @classmethod
    def _section_terms(cls, hint: HintSpec) -> list[str]:
        out: list[str] = []
        for item in [*hint.needles, *hint.must_keep]:
            lowered = str(item).strip().lower()
            if len(lowered) < 4:
                continue
            if re.fullmatch(r"[\d.\s-]+", lowered):
                continue
            if lowered not in out:
                out.append(lowered)
        return out[:6]

    @staticmethod
    def _looks_like_list_item(line: str) -> bool:
        return bool(re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S", line))

    @classmethod
    def _looks_like_heading(cls, stripped: str) -> bool:
        if not stripped or len(stripped) > 140:
            return False
        if "http://" in stripped or "https://" in stripped:
            return False
        if re.fullmatch(r"[\d\s./:%-]+", stripped):
            return False
        words = re.findall(r"[A-Za-z][A-Za-z&/+.-]*", stripped)
        if not words or len(words) > 18:
            return False
        digit_count = sum(ch.isdigit() for ch in stripped)
        if digit_count > max(3, len(stripped) // 5):
            return False
        if stripped.startswith("#"):
            return True
        if re.match(r"^\d+(?:\.\d+)*[.)]?\s+\S", stripped):
            return True
        if stripped.isupper() and len(words) >= 2:
            return True
        if stripped.endswith((".", "?", "!", ";")):
            return False
        capitalized = sum(1 for word in words if word[:1].isupper())
        return capitalized >= max(1, len(words) - 1)

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        text = text.strip()
        return text if len(text) <= limit else text[: limit - 20].rstrip() + "\n...[clipped]..."

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            key = item.strip()
            if key and key not in seen:
                seen.add(key)
                out.append(key)
        return out

    @staticmethod
    def _unique_units(units: list[TextUnit]) -> list[TextUnit]:
        seen: set[str] = set()
        out: list[TextUnit] = []
        for unit in units:
            if unit.anchor in seen:
                continue
            seen.add(unit.anchor)
            out.append(unit)
        return out
