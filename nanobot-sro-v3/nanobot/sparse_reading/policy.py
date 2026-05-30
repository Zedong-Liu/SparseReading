"""Conservative command policy for SRO-enabled runs."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from nanobot.sparse_reading.detector import inspect_file


class SparseCommandPolicy:
    """Block only high-confidence broad dumps and exact repeated failures."""

    def __init__(self, sro: object | None = None) -> None:
        self._failed: dict[tuple[str, str], int] = {}
        self._sro = sro

    def guard(self, command: str, cwd: str) -> str | None:
        cmd = command.strip()
        if not cmd:
            return None
        key = (str(Path(cwd).resolve()), cmd)
        if self._failed.get(key, 0) >= 1 and not self._is_rerunnable_script_command(cmd):
            return (
                "Error: exact same command already failed in this task. "
                "Use sro_read with mode='refine' and the existing artifact_id to narrow the evidence instead."
            )
        if self._is_raw_pdf_search(cmd, cwd):
            return (
                "Error: raw PDF grep/rg is unstable. Use sro_card/sro_read, or extract a bounded text view first."
            )
        if self._is_unbounded_pdf_dump(cmd):
            return (
                "Error: unbounded pdftotext PDF dump is blocked under SRO. "
                "Use sro_read with a HintSpec, or request bounded pages/needles."
            )
        if self._is_package_install(cmd):
            return (
                "Error: package installation is blocked in SRO benchmark runs. "
                "Use existing local Python libraries or write a pandas/numpy fallback script; do not install dependencies."
            )
        if self._is_ready_collection_source_read(cmd, cwd):
            return (
                "Error: this source is already covered by a ready SRO collection digest. "
                "Use the digest/slot_digest to write the deliverable; do not dump, grep, or re-read resolved source files."
            )
        if self._is_text_slot_digest_source_search(cmd, cwd):
            return (
                "Error: this text source already has an SRO slot_digest. "
                "Use the slot candidates or sro_read verify for specific unresolved slots; do not broad grep/exec the source."
            )
        if self._is_large_dump(cmd, cwd):
            return (
                "Error: broad shell dump of a large supported object is blocked under SRO. "
                "Use sro_card followed by sro_read with scout/focus/refine/verify."
            )
        if self._is_office_package_dump(cmd, cwd):
            return (
                "Error: raw Office package extraction is blocked under SRO. "
                "Use sro_card/sro_read for XLSX/DOCX/PPTX evidence instead of unzip/zipinfo on internal XML parts."
            )
        if self._is_python_large_file_read(cmd, cwd):
            return (
                "Error: direct Python read of a large supported object is blocked under SRO. "
                "Use sro_card/sro_read to gather evidence first, then run a short calculation script only on the narrowed facts."
            )
        return None

    def _is_ready_collection_source_read(self, command: str, cwd: str) -> bool:
        if self._sro is None:
            return False
        lower = command.lower()
        read_intent = (
            bool(re.search(r"\b(?:cat|grep|rg|head|tail|sed|awk)\b", lower))
            or bool(re.search(r"\b(?:cp|rsync)\b", lower))
            or ("python" in lower and ("open(" in lower or "json.load(" in lower or "yaml.safe_load(" in lower))
        )
        if not read_intent:
            return False
        checker = getattr(self._sro, "is_ready_collection_child", None)
        if checker is None:
            return False
        for token in self._candidate_path_tokens(command):
            target = self._resolve(token, cwd)
            if target and checker(target):
                return True
        return False

    def _is_text_slot_digest_source_search(self, command: str, cwd: str) -> bool:
        if self._sro is None:
            return False
        lower = command.lower()
        if not re.search(r"\b(?:grep|rg|perl|awk|sed|cat|head|tail)\b", lower):
            return False
        checker = getattr(self._sro, "has_text_slot_digest", None)
        if checker is None:
            return False
        for token in self._candidate_path_tokens(command):
            target = self._resolve(token, cwd)
            if target and checker(target):
                return True
        return False

    @classmethod
    def _candidate_path_tokens(cls, command: str) -> list[str]:
        tokens: list[str] = []
        tokens.extend(cls._split(command))
        tokens.extend(re.findall(r"\bopen\(\s*['\"]([^'\"]+)['\"]", command))
        tokens.extend(re.findall(r"\b(?:json\.load|yaml\.safe_load)\(\s*open\(\s*['\"]([^'\"]+)['\"]", command))
        out: list[str] = []
        for token in tokens:
            cleaned = token.strip().strip("'\"")
            if not cleaned or cleaned.startswith("-"):
                continue
            if "/" in cleaned or "." in Path(cleaned).name:
                out.append(cleaned)
        return out

    def record_result(self, command: str, cwd: str, result: str) -> None:
        if self._is_rerunnable_script_command(command.strip()):
            return
        if "Exit code: 0" in result:
            return
        if "Exit code:" not in result and not result.startswith("Error:"):
            return
        key = (str(Path(cwd).resolve()), command.strip())
        self._failed[key] = self._failed.get(key, 0) + 1

    def _is_large_dump(self, command: str, cwd: str) -> bool:
        parts = self._split(command)
        if not parts or parts[0] not in {"cat", "head", "tail"}:
            return False
        for token in parts[1:]:
            if token.startswith("-"):
                continue
            target = self._resolve(token, cwd)
            if target and self._is_output_artifact(target):
                return False
            if target and inspect_file(target).supported and inspect_file(target).large:
                if not self._sro_should_handoff(target):
                    return False
                return True
            break
        return False

    def _is_raw_pdf_search(self, command: str, cwd: str) -> bool:
        parts = self._split(command)
        if not parts or parts[0] not in {"grep", "rg"}:
            return False
        return any((target := self._resolve(token, cwd)) and target.suffix.lower() == ".pdf" for token in parts[1:])

    def _is_python_large_file_read(self, command: str, cwd: str) -> bool:
        lower = command.lower()
        if not any(tok in lower for tok in ("python", "ipython")):
            return False
        if not any(tok in lower for tok in (
            "read_csv(",
            "read_excel(",
            "load_workbook(",
            "csv.reader(",
            "csv.dictreader(",
            "json.load(",
            "yaml.safe_load(",
            "et.parse(",
            ".open(",
            "open(",
        )):
            return False
        for target in self._supported_large_files(cwd):
            if self._is_output_artifact(target):
                continue
            if not self._sro_should_handoff(target):
                continue
            name = target.name.lower()
            full = str(target).lower()
            if name in lower or full in lower:
                return True
        return False

    def _is_office_package_dump(self, command: str, cwd: str) -> bool:
        parts = self._split(command)
        if not parts or parts[0] not in {"unzip", "zipinfo", "bsdtar", "tar"}:
            return False
        lower = command.lower()
        if parts[0] == "unzip" and not any(flag in parts[1:] for flag in {"-p", "-l"}):
            return False
        if parts[0] == "zipinfo" and "-1" not in parts[1:] and "-l" not in parts[1:]:
            return False
        if parts[0] in {"bsdtar", "tar"} and not any(flag in lower for flag in ("-xof", "-xOf", "-x -O", "--to-stdout")):
            return False
        for target in self._supported_large_files(cwd):
            if target.suffix.lower() not in {".xlsx", ".docx", ".pptx"}:
                continue
            if not self._sro_should_handoff(target):
                continue
            name = target.name.lower()
            full = str(target).lower()
            if name in lower or full in lower:
                return True
        return False

    def _sro_should_handoff(self, target: Path) -> bool:
        if self._sro is None:
            return True
        checker = getattr(self._sro, "should_handoff_read", None)
        if checker is None:
            return True
        should_handoff = bool(checker(target))
        if should_handoff:
            activator = getattr(self._sro, "request_macro_activation", None)
            if callable(activator):
                activator()
        return should_handoff

    @staticmethod
    def _is_unbounded_pdf_dump(command: str) -> bool:
        lower = command.lower().strip()
        if "pdftotext" not in lower or ".pdf" not in lower:
            return False
        if "|" in lower or ">" in lower:
            return False
        return bool(re.search(r"\bpdftotext\b.*\.pdf(?:\s+|-layout\s+)-\s*$", lower))

    @staticmethod
    def _is_package_install(command: str) -> bool:
        lower = command.lower()
        return bool(
            re.search(r"\b(?:apt|apt-get|yum|dnf|apk|brew)\s+(?:install|update)\b", lower)
            or re.search(r"\b(?:pip|pip3|python\s+-m\s+pip|python3\s+-m\s+pip)\s+install\b", lower)
            or re.search(r"\b(?:conda|mamba|micromamba)\s+install\b", lower)
        )

    @staticmethod
    def _split(command: str) -> list[str]:
        try:
            return shlex.split(command)
        except ValueError:
            return command.split()

    def _is_rerunnable_script_command(self, command: str) -> bool:
        parts = self._split(command)
        if not parts:
            return False
        python_bins = {"python", "python3", "python3.10", "python3.11", "python3.12"}
        for idx, token in enumerate(parts):
            if Path(token).name.lower() not in python_bins:
                continue
            return any(
                not candidate.startswith("-") and candidate.endswith(".py")
                for candidate in parts[idx + 1:]
            )
        return False

    @staticmethod
    def _is_output_artifact(path: str | Path) -> bool:
        try:
            resolved = Path(path).resolve(strict=False)
        except Exception:
            resolved = Path(path)
        generated_names = {
            "answer.txt",
            "command_classifications.json",
            "final_answer.md",
            "diagnosis_report.md",
            "did_results_summary.md",
            "metrics_summary.json",
            "analysis_results.json",
            "security_analysis_report.md",
        }
        if resolved.name.lower() in generated_names:
            return True
        output_dirs = {"reports", "outputs", "results"}
        return any(part.lower() in output_dirs for part in resolved.parts)

    @staticmethod
    def _resolve(token: str, cwd: str) -> Path | None:
        if token.startswith("-") or token in {"|", "&&", ";"}:
            return None
        try:
            p = Path(token).expanduser()
            if not p.is_absolute():
                p = Path(cwd) / p
            return p.resolve(strict=False)
        except Exception:
            return None

    @staticmethod
    def _supported_large_files(cwd: str) -> list[Path]:
        base = Path(cwd).resolve()
        try:
            entries = list(base.iterdir())
        except Exception:
            return []
        out: list[Path] = []
        for entry in entries:
            if not entry.is_file():
                continue
            try:
                info = inspect_file(entry)
            except Exception:
                continue
            if info.supported and info.large:
                out.append(entry.resolve())
        return out
