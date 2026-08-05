"""SparseRead token consumption tracker (Claude adapter observability).

Adapted from the Claude Code integration PR into the framework adapter so the
shared core stays free of host-platform concerns.  Uses the Anthropic
``count_tokens`` API when credentials exist, otherwise character heuristics.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CHARS_PER_TOKEN_TEXT = 4.0
CHARS_PER_TOKEN_JSON = 3.0
CHARS_PER_TOKEN_CJK = 2.0
DEFAULT_CONTEXT_WINDOW = 200_000


def estimate_tokens(text: str | None, chars_per_token: float = CHARS_PER_TOKEN_TEXT) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / chars_per_token))


def estimate_file_tokens(size_bytes: int, file_extension: str = "") -> int:
    ext = file_extension.lower().lstrip(".")
    if ext in {"json", "csv", "yaml", "yml", "toml", "xml", "html"}:
        return max(1, int(size_bytes / CHARS_PER_TOKEN_JSON))
    if ext == "pdf":
        return max(1, int(size_bytes * 1.4 / CHARS_PER_TOKEN_TEXT))
    return max(1, int(size_bytes / CHARS_PER_TOKEN_TEXT))


def estimate_response_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN_JSON))


@dataclass
class TokenRecord:
    timestamp: float = field(default_factory=time.time)
    operation: str = ""
    file_path: str = ""
    file_size_bytes: int = 0
    file_extension: str = ""
    full_file_tokens: int = 0
    sr_response_chars: int = 0
    sr_response_tokens: int = 0
    tokens_saved: int = 0
    savings_ratio: float = 0.0
    mode: str = ""
    artifact_id: str = ""
    count_mode: str = "heuristic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": round(self.timestamp, 3),
            "op": self.operation,
            "path": self.file_path,
            "file_bytes": self.file_size_bytes,
            "ext": self.file_extension,
            "full_tokens": self.full_file_tokens,
            "sr_chars": self.sr_response_chars,
            "sr_tokens": self.sr_response_tokens,
            "saved": self.tokens_saved,
            "ratio": round(self.savings_ratio, 4),
            "mode": self.mode,
            "artifact": self.artifact_id,
            "count_mode": self.count_mode,
        }


@dataclass
class SessionSummary:
    total_operations: int = 0
    total_full_file_tokens: int = 0
    total_sr_response_tokens: int = 0
    total_tokens_saved: int = 0
    overall_savings_ratio: float = 0.0
    context_window: int = DEFAULT_CONTEXT_WINDOW
    context_retained_pct: float = 0.0
    by_operation: dict[str, dict[str, int]] = field(default_factory=dict)
    top_savings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operations": self.total_operations,
            "full_file_tokens": self.total_full_file_tokens,
            "sr_response_tokens": self.total_sr_response_tokens,
            "tokens_saved": self.total_tokens_saved,
            "savings_ratio": round(self.overall_savings_ratio, 4),
            "context_window": self.context_window,
            "context_retained_pct": round(self.context_retained_pct, 2),
            "by_operation": self.by_operation,
            "top_savings": self.top_savings[:10],
        }


def _get_api_client():
    try:
        import anthropic

        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return anthropic.Anthropic()
        config_dir = os.environ.get("ANTHROPIC_CONFIG_DIR") or os.path.join(
            os.path.expanduser("~"), ".config", "anthropic"
        )
        if os.path.isdir(config_dir):
            return anthropic.Anthropic()
    except Exception:
        pass
    return None


def _api_count_tokens(text: str, client: Any, model: str = "claude-opus-4-8") -> int | None:
    if not text or not client:
        return None
    try:
        resp = client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": text}],
        )
        return resp.input_tokens
    except Exception:
        return None


class TokenTracker:
    """Tracks SR token consumption with per-operation granularity."""

    def __init__(
        self,
        *,
        log_dir: str | Path | None = None,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        enable_log: bool = True,
        model: str = "claude-opus-4-8",
    ) -> None:
        self.context_window = context_window
        self.enable_log = enable_log
        self.model = model
        self.records: list[TokenRecord] = []
        self._started_at = time.time()
        self._api_client = _get_api_client()
        if log_dir is None:
            log_dir = Path.home() / ".claude"
        self._log_path = Path(log_dir) / "sro_token_log.jsonl"

    def record_preview(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
        artifact_id: str = "",
    ) -> TokenRecord:
        return self._record(
            "preview", file_path, file_size_bytes, file_extension, response_json, "", artifact_id
        )

    def record_read(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
        mode: str = "",
        artifact_id: str = "",
    ) -> TokenRecord:
        return self._record(
            "read", file_path, file_size_bytes, file_extension, response_json, mode, artifact_id
        )

    def record_card(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
    ) -> TokenRecord:
        return self._record(
            "card", file_path, file_size_bytes, file_extension, response_json, "", ""
        )

    def record_raw(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
        artifact_id: str = "",
    ) -> TokenRecord:
        return self._record(
            "raw", file_path, file_size_bytes, file_extension, response_json, "", artifact_id
        )

    def _record(
        self,
        operation: str,
        file_path: str,
        file_size_bytes: int,
        file_extension: str,
        response_json: str,
        mode: str,
        artifact_id: str,
    ) -> TokenRecord:
        count_mode = "heuristic"
        full_tokens = estimate_file_tokens(file_size_bytes, file_extension)
        sr_response_tokens = estimate_response_tokens(response_json)

        if self._api_client is not None:
            file_text = ""
            try:
                if file_path and Path(file_path).exists():
                    file_text = Path(file_path).read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                pass
            api_full = _api_count_tokens(file_text, self._api_client, self.model) if file_text else None
            api_sr = _api_count_tokens(response_json, self._api_client, self.model) if response_json else None
            if api_full is not None and api_sr is not None:
                full_tokens = api_full
                sr_response_tokens = api_sr
                count_mode = "api"

        saved = max(0, full_tokens - sr_response_tokens)
        ratio = saved / full_tokens if full_tokens > 0 else 0.0
        record = TokenRecord(
            operation=operation,
            file_path=file_path,
            file_size_bytes=file_size_bytes,
            file_extension=file_extension,
            full_file_tokens=full_tokens,
            sr_response_chars=len(response_json),
            sr_response_tokens=sr_response_tokens,
            tokens_saved=saved,
            savings_ratio=ratio,
            mode=mode,
            artifact_id=artifact_id,
            count_mode=count_mode,
        )
        self.records.append(record)
        if self.enable_log:
            self._append_log(record)
        return record

    def session_summary(self) -> SessionSummary:
        records = self.records
        total_full = sum(r.full_file_tokens for r in records)
        total_sr = sum(r.sr_response_tokens for r in records)
        total_saved = sum(r.tokens_saved for r in records)
        overall_ratio = total_saved / total_full if total_full > 0 else 0.0
        context_retained = (total_saved / self.context_window * 100) if self.context_window > 0 else 0.0
        by_op: dict[str, dict[str, int]] = {}
        for r in records:
            bucket = by_op.setdefault(r.operation, {"count": 0, "full_tokens": 0, "sr_tokens": 0, "saved": 0})
            bucket["count"] += 1
            bucket["full_tokens"] += r.full_file_tokens
            bucket["sr_tokens"] += r.sr_response_tokens
            bucket["saved"] += r.tokens_saved
        top = sorted(records, key=lambda r: r.tokens_saved, reverse=True)
        return SessionSummary(
            total_operations=len(records),
            total_full_file_tokens=total_full,
            total_sr_response_tokens=total_sr,
            total_tokens_saved=total_saved,
            overall_savings_ratio=overall_ratio,
            context_window=self.context_window,
            context_retained_pct=context_retained,
            by_operation=by_op,
            top_savings=[r.to_dict() for r in top],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(time.time() - self._started_at, 3),
            "context_window": self.context_window,
            "log_path": str(self._log_path),
            "record_count": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "session": self.session_summary().to_dict(),
        }

    def _append_log(self, record: TokenRecord) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass

    @classmethod
    def read_log(cls, log_path: str | Path) -> list[dict[str, Any]]:
        path = Path(log_path)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records


def count_tokens_api(text: str, model: str = "claude-opus-4-8") -> int | None:
    """Ground-truth Anthropic token count when credentials are available."""
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": text}],
        )
        return response.input_tokens
    except Exception:
        return None
