"""Sparse reader for embedded LAMMPS script template libraries."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from nanobot.sparse_reading.models import EvidenceBlock, EvidencePack, HintSpec


LAMMPS_BLOCK_RE = re.compile(
    r"(?:lammps_input|lammps_script|IN_LAMMPS_CONTENT)\s*=\s*(?:[rubfRUBF]*)([\"']{3})(.*?)\1",
    re.DOTALL,
)
TRIPLE_STRING_RE = re.compile(r"(?:[rubfRUBF]*)([\"']{3})(.*?)\1", re.DOTALL)
PAIR_STYLE_RE = re.compile(r"(?im)^\s*pair_style\s+([^\s#]+)")
COMMENT_META_RE = re.compile(r"(?im)^\s*#\s*([A-Za-z_ -]+)\s*[:=]\s*([^#\n]+)")


@dataclass(slots=True)
class ScriptEntry:
    filename: str
    task_family: str
    material: str
    pair_style: str
    lammps_block: str
    char_count: int


class ScriptLibraryReader:
    """Select one or a few validated LAMMPS templates from a Python wrapper library."""

    _COMPARISON_FAMILIES = {"nano_melting"}
    _MANYBODY_PAIR_STYLES = {"eam", "eam/alloy", "eam/fs", "sw", "tersoff"}
    _TASK_ALIASES = {
        "tensile_test": ("tensile_test", "tensile", "uniaxial", "strain", "stress"),
        "bulk_equilibration": ("bulk_equilibration", "bulk", "equilibration", "npt", "relax"),
        "rdf_analysis": ("rdf_analysis", "rdf", "radial_distribution", "coordination"),
        "melting_point": ("melting_point", "melting", "melt", "heat"),
        "thermal_conductivity": ("thermal_conductivity", "thermal", "conductivity", "heat_flux"),
        "defect_diffusion": ("defect_diffusion", "diffusion", "msd", "vacancy"),
        "defect_formation": ("defect_formation", "defect", "vacancy", "interstitial"),
        "surface_energy": ("surface_energy", "surface", "slab"),
        "diffusion": ("diffusion", "diffuse", "msd"),
        "restart_workflow": ("restart_workflow", "restart", "write_restart", "read_restart"),
        "amorphous_quench": ("amorphous_quench", "amorphous", "quench", "cooling"),
        "npt_cooling": ("npt_cooling", "cooling", "npt"),
        "nano_melting": ("nano_melting", "nanoparticle", "melting", "melt"),
        "surface_relaxation": ("surface_relaxation", "surface", "relaxation", "minimize"),
    }
    _MATERIAL_ALIASES = (
        ("LJ fluid", ("lj_fluid", "lj fluid", "lj/cut", "lennard")),
        ("Al-Mg", ("al-mg", "almg", "al_mg")),
        ("Cu-Ni", ("cu-ni", "cuni", "cu_ni")),
    )
    _ELEMENTS = {
        "Al", "Ar", "Au", "C", "Co", "Cu", "Fe", "Ge", "Li", "Mg", "Mo", "Na",
        "Ni", "O", "Pb", "Pt", "Si", "Ti", "W", "Zr",
    }

    def card_details(self, path: Path) -> dict:
        entries = self._scan_library(path)
        families: dict[str, set[str]] = defaultdict(set)
        for entry in entries:
            families[entry.task_family or "unknown"].add(entry.material or "unknown")
        return {
            "kind": "script_library_card",
            "entry_count": len(entries),
            "families": [
                {"task_family": family, "materials": sorted(materials), "count": sum(1 for item in entries if (item.task_family or "unknown") == family)}
                for family, materials in sorted(families.items())
            ],
        }

    def preview_details(self, path: Path, budget: int) -> dict:
        details = self.card_details(path)
        details["default_read"] = "template index only; use sro_read focus with task_family/material needles to retrieve one LAMMPS block"
        return self._fit_details(details, budget)

    def read(self, path: Path, artifact_id: str, mode: str, hint: HintSpec, budget: int) -> EvidencePack:
        entries = self._scan_library(path)
        if not entries:
            return EvidencePack(
                artifact_id=artifact_id,
                mode=mode,
                type="script_library",
                summary="empty LAMMPS script library",
                error=f"No embedded LAMMPS scripts found in {path}",
                unresolved=list(hint.needles),
            )

        if mode == "scout":
            return EvidencePack(
                artifact_id=artifact_id,
                mode=mode,
                type="script_library",
                summary=f"LAMMPS script library with {len(entries)} templates",
                skeleton=self._skeleton(entries),
                evidence=[],
                unresolved=list(hint.needles),
                next_hint=self._next_hint(artifact_id, hint, "focus"),
            )

        if mode == "verify":
            return self._verify(artifact_id, mode, hint, entries)

        if mode == "refine":
            return self._refine(artifact_id, mode, hint, entries, budget)

        if mode == "collect" and hint.slots:
            return self._collect(artifact_id, mode, hint, entries, budget)

        return self._focus(artifact_id, mode, hint, entries, budget)

    def _scan_library(self, path: Path) -> list[ScriptEntry]:
        entries: list[ScriptEntry] = []
        for file_path in sorted(path.rglob("*.py")):
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            block = self._extract_lammps_block(text)
            if not block:
                continue
            entries.append(
                ScriptEntry(
                    filename=str(file_path.relative_to(path)),
                    task_family=self._infer_task_family(file_path, text, block),
                    material=self._infer_material(file_path, text, block),
                    pair_style=self._infer_pair_style(block),
                    lammps_block=block,
                    char_count=len(block),
                )
            )
        return entries

    def _focus(
        self,
        artifact_id: str,
        mode: str,
        hint: HintSpec,
        entries: list[ScriptEntry],
        budget: int,
    ) -> EvidencePack:
        ranked = self._rank_entries(entries, hint)
        if not ranked or ranked[0][1] < 1.0:
            return self._no_match_pack(artifact_id, mode, hint, len(entries))
        entry, score = ranked[0]
        block = EvidenceBlock(
            anchor=self._anchor(entry),
            text=self._clip(entry.lammps_block, min(3000, budget)),
            score=score,
        )
        evidence = [block]
        comparison = self._comparison_evidence(entry, ranked, budget)
        if comparison is not None:
            evidence.append(comparison)
        runtime_guard = self._runtime_guard_evidence(entry, budget)
        if runtime_guard is not None:
            evidence.append(runtime_guard)
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="script_library",
            summary=(
                f"Matched {entry.task_family or 'unknown'}/{entry.material or 'unknown'} template "
                f"(score={score:.1f}). LAMMPS block: {len(entry.lammps_block.splitlines())} lines."
                + (" Added short same-family syntax comparison." if comparison else "")
                + (" Added runtime invariant guard." if runtime_guard else "")
            ),
            skeleton=self._skeleton(entries),
            evidence=evidence,
            unresolved=self._unresolved(hint, evidence),
            next_action={
                "allowed_next": ["write_file"],
                "instruction": (
                    "Adapt the primary validated LAMMPS template for the requested material and constraints. "
                    "If comparison or runtime-invariant blocks are present, use them only to cross-check risky LAMMPS syntax and executable preconditions."
                ),
            },
        )

    def _collect(
        self,
        artifact_id: str,
        mode: str,
        hint: HintSpec,
        entries: list[ScriptEntry],
        budget: int,
    ) -> EvidencePack:
        blocks: list[EvidenceBlock] = []
        used = 0
        seen_families: set[str] = set()
        slot_hints = [
            HintSpec(
                goal=slot.question,
                needles=[slot.id, *slot.aliases],
                want=hint.want,
                scope=hint.scope,
                artifact=artifact_id,
                type_hint="script_library",
            )
            for slot in hint.slots
        ]
        for slot_hint in slot_hints:
            ranked = self._rank_entries(entries, slot_hint)
            if not ranked or ranked[0][1] < 1.0:
                continue
            entry, score = ranked[0]
            family_key = entry.task_family or entry.filename
            if family_key in seen_families:
                continue
            text = self._clip(entry.lammps_block, min(3000, max(800, budget - used)))
            cost = len(text) + len(entry.filename) + 64
            if blocks and used + cost > budget:
                continue
            blocks.append(EvidenceBlock(self._anchor(entry), text, score))
            used += cost
            seen_families.add(family_key)
        if not blocks:
            return self._focus(artifact_id, mode, hint, entries, budget)
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="script_library",
            summary=f"selected {len(blocks)} LAMMPS templates for requested families",
            skeleton=self._skeleton(entries),
            evidence=blocks,
            unresolved=self._unresolved(hint, blocks),
            next_action={
                "allowed_next": ["write_file"],
                "instruction": "Use each returned template as the closest validated starting point; do not read the full library.",
            },
        )

    def _refine(
        self,
        artifact_id: str,
        mode: str,
        hint: HintSpec,
        entries: list[ScriptEntry],
        budget: int,
    ) -> EvidencePack:
        ranked = self._rank_entries(entries, hint)
        if not ranked or ranked[0][1] < 1.0:
            return self._no_match_pack(artifact_id, mode, hint, len(entries))
        entry, score = ranked[0]
        terms = self._terms(hint)
        lines = [
            line for line in entry.lammps_block.splitlines()
            if any(term in line.lower() for term in terms)
        ]
        if not lines:
            lines = entry.lammps_block.splitlines()[:20]
        text = self._clip("\n".join(lines), min(2000, budget))
        block = EvidenceBlock(self._anchor(entry), text, score)
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="script_library",
            summary=f"refined parameter lines from {entry.filename}",
            evidence=[block],
            unresolved=self._unresolved(hint, [block]),
            next_action={"allowed_next": ["write_file", "focus if more template context is needed"]},
        )

    def _verify(
        self,
        artifact_id: str,
        mode: str,
        hint: HintSpec,
        entries: list[ScriptEntry],
    ) -> EvidencePack:
        ranked = self._rank_entries(entries, hint)
        entry = ranked[0][0] if ranked else entries[0]
        hay = f"{entry.task_family} {entry.material} {entry.pair_style}\n{entry.lammps_block}".lower()
        present = [item for item in hint.must_keep if item.lower() in hay]
        missing = [item for item in hint.must_keep if item.lower() not in hay]
        text = "present: " + ", ".join(present or ["none"]) + "\nmissing: " + ", ".join(missing or ["none"])
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="script_library",
            summary=f"verified required sections against {entry.filename}",
            evidence=[EvidenceBlock(self._anchor(entry), text, ranked[0][1] if ranked else 0.0)],
            unresolved=missing,
            next_action={"allowed_next": ["write_file"] if not missing else ["focus", "native fallback"]},
        )

    def _score_entry(self, entry: ScriptEntry, hint: HintSpec) -> float:
        terms = self._terms(hint)
        query = " ".join(terms)
        score = 0.0
        family = entry.task_family.lower()
        if family and (family in terms or family.replace("_", " ") in query):
            score += 5.0
        material = entry.material.lower()
        if material and (material in terms or material in query):
            score += 3.0
        pair_style = entry.pair_style.lower()
        if pair_style and (pair_style in terms or pair_style in query):
            score += 2.0
        metadata = f"{entry.filename} {entry.task_family} {entry.material} {entry.pair_style}".lower()
        for term in terms:
            if len(term) > 2 and term in metadata:
                score += 1.0
        lammps_terms = {word for word in re.findall(r"[a-z0-9_./-]+", entry.lammps_block.lower()) if len(word) > 4}
        score += len(lammps_terms & set(terms)) * 0.5
        return score

    def _rank_entries(self, entries: list[ScriptEntry], hint: HintSpec) -> list[tuple[ScriptEntry, float]]:
        ranked = [(entry, self._score_entry(entry, hint)) for entry in entries]
        ranked.sort(key=lambda item: (item[1], -item[0].char_count), reverse=True)
        return ranked

    def _comparison_evidence(
        self,
        primary: ScriptEntry,
        ranked: list[tuple[ScriptEntry, float]],
        budget: int,
    ) -> EvidenceBlock | None:
        if primary.task_family not in self._COMPARISON_FAMILIES:
            return None
        if budget < 3600:
            return None
        for candidate, score in ranked[1:]:
            if candidate.filename == primary.filename:
                continue
            if candidate.task_family != primary.task_family:
                continue
            text = self._comparison_excerpt(candidate)
            if not text:
                continue
            return EvidenceBlock(
                anchor=f"comparison_only {self._anchor(candidate)}",
                text=text,
                score=max(0.1, score - 0.25),
            )
        guard = self._risk_guard_excerpt(primary)
        if not guard:
            return None
        return EvidenceBlock(
            anchor=f"comparison_only syntax_guard task_family={primary.task_family}",
            text=guard,
            score=0.5,
        )

    def _comparison_excerpt(self, entry: ScriptEntry) -> str:
        important = (
            "variable", "label", "jump", "next", "if ", "fix ", "unfix", "fix_modify",
            "region", "create_atoms", "thermo_style", "run", "print",
        )
        lines: list[str] = []
        for raw_line in entry.lammps_block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lower = line.lower()
            if line.startswith("#") and len(lines) < 3:
                lines.append(raw_line)
            elif any(lower.startswith(prefix) for prefix in important):
                lines.append(raw_line)
            if len(lines) >= 26:
                break
        if not lines:
            return ""
        header = (
            "COMPARISON_TEMPLATE_DO_NOT_COPY_BLINDLY\n"
            "Use these same-family lines only to cross-check risky loop/fix/variable syntax.\n"
        )
        return self._clip(header + "\n".join(lines), 1200)

    def _runtime_guard_evidence(self, entry: ScriptEntry, budget: int) -> EvidenceBlock | None:
        if budget < 4200:
            return None
        text = self._runtime_guard_excerpt(entry)
        if not text:
            return None
        return EvidenceBlock(
            anchor=f"runtime_invariants task_family={entry.task_family} material={entry.material} pair_style={entry.pair_style or 'unknown'}",
            text=text,
            score=0.45,
        )

    def _runtime_guard_excerpt(self, entry: ScriptEntry) -> str:
        lines = [
            "RUNTIME_INVARIANTS_DO_NOT_COPY_BLINDLY",
            "Preserve these executable preconditions when adapting the selected template.",
        ]
        pair_style = entry.pair_style.lower()
        if pair_style in self._MANYBODY_PAIR_STYLES:
            potential = self._potential_filename(entry.lammps_block)
            elements = self._element_mapping(entry.material)
            if elements:
                file_hint = potential or "<potential-file>"
                lines.extend([
                    f"- For {entry.pair_style}, pair_coeff needs element mapping after the potential file.",
                    f"  Use the shape: pair_coeff * * {file_hint} {' '.join(elements)}",
                    "  A bare `pair_coeff * * file` is not executable for manybody potentials.",
                ])
        if entry.task_family == "defect_diffusion" or re.search(r"(?im)^\s*read_data\s+", entry.lammps_block):
            lines.extend([
                "- If the final input uses read_data, the Python script must create or copy that exact data file into the run directory before LAMMPS starts.",
                "- For a self-contained no-runtime artifact, prefer lattice/create_box/create_atoms unless the data file is also generated.",
                "- For LJ/atomic systems created from scratch, include `mass 1 1.0` before any `velocity all create` command.",
                "- For simple vacancies, prefer `region ...`, `group ... region ...`, then `delete_atoms group <group>`; avoid short invalid forms such as `delete_atoms random 0.01 seed`.",
            ])
        if entry.task_family == "restart_workflow" or "read_restart" in entry.lammps_block:
            lines.extend([
                "- Restart workflows should keep stage boundaries clear: stage 1 creates the box and writes restart; stage 2 starts from read_restart.",
                "- If both stages are in one input file, use `clear` between `write_restart` and `read_restart`; do not call read_restart while the stage-1 box still exists.",
                "- Do not place pair_coeff before a simulation box or read_restart exists.",
                "- After read_restart, re-specify manybody pair_style and pair_coeff before the next run when LAMMPS requires it.",
            ])
        if entry.task_family == "amorphous_quench":
            lines.extend([
                "- For binary alloy boxes, create_box must declare all atom types before set type/fraction, e.g. `create_box 2 box`, `create_atoms 1 box`, then `set type 1 type/fraction 2 <fraction> <seed>`.",
                "- Set masses for every atom type before velocity or dynamics.",
            ])
        if entry.task_family == "surface_relaxation":
            lines.extend([
                "- To create a slab with vacuum, make the simulation box taller than the populated slab region.",
                "- Define a slab region first, then use `create_atoms 1 region <region_name>`; do not fill the full box if the task asks for a surface/vacuum slab.",
                "- `create_atoms 1 slab` is not valid LAMMPS syntax.",
            ])
        if entry.task_family == "nano_melting":
            lines.extend([
                "- Nanoparticle melting should keep heating fixes well scoped: define fix, run, then unfix before the next heating segment.",
            ])
        if len(lines) <= 2:
            return ""
        return self._clip("\n".join(lines), 1400)

    @staticmethod
    def _risk_guard_excerpt(entry: ScriptEntry) -> str:
        if entry.task_family != "nano_melting":
            return ""
        return (
            "COMPARISON_SYNTAX_GUARD_DO_NOT_COPY_BLINDLY\n"
            "For nano_melting, prefer simple sequential heating blocks over fragile LAMMPS loops.\n"
            "Avoid Python-format leftovers such as {{T}} in the final in.lammps.\n"
            "Avoid reusing a fix ID without unfixing it first.\n"
            "Safe heating idiom:\n"
            "fix heat all nvt temp 300.0 300.0 0.1\n"
            "run 5000\n"
            "unfix heat\n"
            "fix heat all nvt temp 300.0 600.0 0.1\n"
            "run 5000\n"
            "unfix heat\n"
            "thermo_style custom step temp pe ke etotal\n"
        )

    @staticmethod
    def _potential_filename(block: str) -> str:
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line.lower().startswith("pair_coeff"):
                continue
            tokens = line.split()
            for token in tokens[1:]:
                cleaned = token.strip("\"'")
                if any(cleaned.endswith(suffix) for suffix in (".eam", ".alloy", ".fs", ".sw", ".tersoff")):
                    return cleaned
        return ""

    @staticmethod
    def _element_mapping(material: str) -> list[str]:
        if not material or material.lower() in {"unknown", "lj", "lj fluid"}:
            return []
        candidates = re.findall(r"[A-Z][a-z]?", material)
        return candidates or [material]

    def _no_match_pack(self, artifact_id: str, mode: str, hint: HintSpec, entry_count: int) -> EvidencePack:
        unresolved = list(hint.needles) or [hint.goal or "template match"]
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="script_library",
            summary=f"no confident LAMMPS template match among {entry_count} entries",
            unresolved=unresolved,
            next_action={
                "allowed_next": ["native fallback", "retry_sro_read with task_family/material needles"],
                "instruction": "Do not inject an unrelated template; proceed zero-shot or provide more specific task/material hints.",
            },
        )

    def _skeleton(self, entries: list[ScriptEntry]) -> list[str]:
        grouped: dict[str, list[ScriptEntry]] = defaultdict(list)
        for entry in entries:
            grouped[entry.task_family or "unknown"].append(entry)
        lines: list[str] = []
        for family, items in sorted(grouped.items()):
            materials = sorted({item.material or "unknown" for item in items})
            pair_styles = sorted({item.pair_style for item in items if item.pair_style})
            lines.append(
                f"task_family={family} materials={','.join(materials)} count={len(items)}"
                + (f" pair_styles={','.join(pair_styles[:4])}" if pair_styles else "")
            )
        return lines

    @classmethod
    def _infer_task_family(cls, path: Path, text: str, block: str) -> str:
        meta = cls._metadata(text)
        for key in ("task_family", "task family", "family"):
            if meta.get(key):
                return cls._normalize_family(meta[key])
        hay = f"{path.stem} {block}".lower()
        for family, aliases in cls._TASK_ALIASES.items():
            if any(alias in hay for alias in aliases):
                return family
        return "unknown"

    @classmethod
    def _infer_material(cls, path: Path, text: str, block: str) -> str:
        meta = cls._metadata(text)
        for key in ("material", "element"):
            if meta.get(key):
                return cls._normalize_material(meta[key])
        hay = f"{path.stem} {block}"
        lowered = hay.lower()
        for material, aliases in cls._MATERIAL_ALIASES:
            if any(alias in lowered for alias in aliases):
                return material
        for element in sorted(cls._ELEMENTS, key=len, reverse=True):
            if re.search(rf"(?<![A-Za-z]){re.escape(element)}(?![a-z])", hay):
                return element
        pair_match = re.search(r"(?im)^\s*pair_coeff\b.*?\b([A-Z][a-z]?)\b", block)
        return pair_match.group(1) if pair_match else "unknown"

    @staticmethod
    def _infer_pair_style(block: str) -> str:
        match = PAIR_STYLE_RE.search(block)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _metadata(text: str) -> dict[str, str]:
        head = "\n".join(text.splitlines()[:50])
        return {match.group(1).strip().lower().replace("-", "_"): match.group(2).strip() for match in COMMENT_META_RE.finditer(head)}

    @staticmethod
    def _normalize_family(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_") or "unknown"

    @staticmethod
    def _normalize_material(value: str) -> str:
        return re.split(r"[\s,;/]+", value.strip())[0] or "unknown"

    @staticmethod
    def _extract_lammps_block(text: str) -> str:
        blocks = [ScriptLibraryReader._clean_lammps_block(match.group(2)) for match in LAMMPS_BLOCK_RE.finditer(text)]
        if not blocks:
            blocks = [
                ScriptLibraryReader._clean_lammps_block(match.group(2))
                for match in TRIPLE_STRING_RE.finditer(text)
                if ScriptLibraryReader._looks_like_lammps(match.group(2))
            ]
        blocks = [block for block in blocks if block]
        if not blocks:
            return ""
        if len(blocks) == 1:
            return blocks[0]
        joined: list[str] = []
        for index, block in enumerate(blocks, start=1):
            joined.append(f"# --- LAMMPS block {index} ---\n{block}")
        return "\n\n".join(joined)

    @staticmethod
    def _looks_like_lammps(text: str) -> bool:
        lower = text.lower()
        has_units = bool(re.search(r"(?im)^\s*units\s+\w+", text))
        has_run = bool(re.search(r"(?im)^\s*run\s+", text))
        has_pair_or_data = bool(re.search(r"(?im)^\s*(pair_style|read_data|read_restart|create_box)\b", text))
        return has_units and has_run and has_pair_or_data and "class " not in lower

    @staticmethod
    def _clean_lammps_block(block: str) -> str:
        lines = [line.rstrip() for line in block.replace("\r\n", "\n").splitlines()]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _terms(hint: HintSpec) -> list[str]:
        raw = " ".join([hint.goal, *hint.needles, *hint.must_keep])
        for slot in hint.slots:
            raw += " " + " ".join([slot.id, slot.question, *slot.aliases])
        terms = [term.strip().lower() for term in re.split(r"[\s,;:()]+", raw) if term.strip()]
        phrases = [item.strip().lower() for item in [hint.goal, *hint.needles, *hint.must_keep] if item.strip()]
        return list(dict.fromkeys([*terms, *phrases]))

    @staticmethod
    def _anchor(entry: ScriptEntry) -> str:
        return f"task_family={entry.task_family} material={entry.material} pair_style={entry.pair_style or 'unknown'} source={entry.filename}"

    @staticmethod
    def _unresolved(hint: HintSpec, blocks: list[EvidenceBlock]) -> list[str]:
        text = "\n".join([block.anchor + "\n" + block.text for block in blocks]).lower()
        unresolved: list[str] = []
        for needle in hint.needles:
            low = needle.lower().strip()
            if low and low not in text:
                unresolved.append(needle)
        return unresolved

    @staticmethod
    def _next_hint(artifact_id: str, hint: HintSpec, mode: str) -> dict:
        return {
            "goal": hint.goal or "select a LAMMPS template",
            "needles": hint.needles,
            "want": "verbatim",
            "scope": "new",
            "artifact": artifact_id,
            "type_hint": "script_library",
            "must_keep": hint.must_keep,
            "mode": mode,
        }

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 20)].rstrip() + "\n...[clipped]..."

    @staticmethod
    def _fit_details(details: dict, budget: int) -> dict:
        while len(str(details)) > budget and len(details.get("families", [])) > 4:
            details["families"] = details["families"][: max(4, len(details["families"]) // 2)]
            details["truncated"] = True
        return details
