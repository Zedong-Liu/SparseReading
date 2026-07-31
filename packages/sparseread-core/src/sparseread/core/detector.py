"""Large-object detection for Sparse Reading Orchestrator."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

STRUCTURED_EXTS = {".csv", ".tsv", ".xlsx", ".json", ".yaml", ".yml", ".xml"}
TEXT_EXTS = {
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".py",
    ".sh",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
}
SUPPORTED_EXTS = STRUCTURED_EXTS | TEXT_EXTS
COLLECTION_EXTS = SUPPORTED_EXTS | {".eml"}
DEFAULT_LARGE_BYTES = int(os.environ.get("SRO_LARGE_BYTES", "4096"))
DEFAULT_STRUCTURED_LARGE_BYTES = int(os.environ.get("SRO_STRUCTURED_LARGE_BYTES", "1024"))
DEFAULT_COLLECTION_FILES = int(os.environ.get("SRO_COLLECTION_FILES", "3"))
SKIP_DIRS = {
    ".git", ".nanobot", ".openclaw", ".opencode", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", "memory", "sessions", "bootstrap", "skills",
}
SKIP_FILES = {"AGENTS.md", "BOOTSTRAP.md", "HEARTBEAT.md", "IDENTITY.md", "SOUL.md", "TOOLS.md", "USER.md"}


@dataclass(slots=True)
class FileInfo:
    path: Path
    type: str
    size_bytes: int
    structured: bool
    supported: bool
    large: bool


def file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".yml":
        return "yaml"
    if suffix in {".md", ".markdown", ".rst"}:
        return "text"
    if suffix in {".log", ".py", ".sh", ".toml", ".ini", ".cfg", ".conf"}:
        return "text"
    if suffix == ".tsv":
        return "csv"
    if suffix.startswith("."):
        return suffix[1:]
    if path.name.lower() == "readme":
        return "text"
    return "text"


def inspect_file(path: str | Path, *, large_bytes: int | None = None) -> FileInfo:
    p = Path(path).expanduser().resolve()
    if p.is_dir():
        files = [
            entry for entry in p.rglob("*")
            if entry.is_file()
            and entry.name not in SKIP_FILES
            and not any(part in SKIP_DIRS for part in entry.relative_to(p).parts[:-1])
            and (entry.suffix.lower() in COLLECTION_EXTS or entry.name.lower().startswith("readme"))
        ]
        size = sum(entry.stat().st_size for entry in files if entry.exists())
        return FileInfo(
            path=p,
            type="collection",
            size_bytes=size,
            structured=False,
            supported=bool(files),
            large=len(files) >= DEFAULT_COLLECTION_FILES or size >= DEFAULT_LARGE_BYTES,
        )
    suffix = p.suffix.lower()
    kind = file_type(p)
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    supported = suffix in SUPPORTED_EXTS or p.name.lower().startswith("readme")
    structured = suffix in STRUCTURED_EXTS
    if large_bytes is not None:
        threshold = large_bytes
    else:
        threshold = DEFAULT_STRUCTURED_LARGE_BYTES if structured else DEFAULT_LARGE_BYTES
    return FileInfo(
        path=p,
        type=kind,
        size_bytes=size,
        structured=structured,
        supported=supported,
        large=size >= threshold,
    )
