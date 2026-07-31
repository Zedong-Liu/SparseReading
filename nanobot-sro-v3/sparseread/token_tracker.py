"""SparseRead Token Consumption Tracker.

Provides precise token consumption tracking for SparseRead integrations.
When ANTHROPIC_API_KEY is available, uses the Anthropic Messages API
/v1/messages/count_tokens endpoint for GROUND-TRUTH token counts.
Falls back to character-count heuristics when the API is unavailable.

Metrics computed:
  - full_file_tokens: tokens if the file were read natively
  - sr_response_tokens: tokens in SR tool responses
  - tokens_saved: full_file_tokens - sr_response_tokens
  - savings_ratio: tokens_saved / full_file_tokens
  - context_retained_pct: percentage of context window that was conserved

Token counting modes (auto-selected):
  - API mode (ANTHROPIC_API_KEY set): uses POST /v1/messages/count_tokens
    for ground-truth counts with the real Anthropic tokenizer
  - Heuristic mode (no API key): character-count heuristic
    (4 chars/token for text, 3 for JSON, adjusted for PDF)

The tracker writes a JSONL log file (~/.claude/sro_token_log.jsonl) so
metrics survive across sessions and can be analysed offline.

Usage:
  from sparseread.token_tracker import TokenTracker

  tracker = TokenTracker(log_dir=Path.home() / ".claude")
  tracker.record_preview(file_path, file_size_bytes, preview_json)
  tracker.record_read(file_path, file_size_bytes, read_json, mode="collect")
  print(tracker.session_summary())
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Token estimation constants
# ---------------------------------------------------------------------------

# Heuristic: English / code text averages ~4 characters per token.
# CJK languages average ~2 characters per token.
# Structured data (JSON, CSV) averages ~3 characters per token.
# These are calibration defaults; the Anthropic count_tokens endpoint is
# the ground truth when available.
CHARS_PER_TOKEN_TEXT = 4.0
CHARS_PER_TOKEN_JSON = 3.0
CHARS_PER_TOKEN_CJK = 2.0

# Default context window size for Claude Opus 4.8 / Sonnet 5 (1M).
# Adjust for other models.
DEFAULT_CONTEXT_WINDOW = 200_000


def estimate_tokens(text: str | None, chars_per_token: float = CHARS_PER_TOKEN_TEXT) -> int:
    """Estimate token count for a string using the character heuristic."""
    if not text:
        return 0
    return max(1, int(len(text) / chars_per_token))


def estimate_file_tokens(size_bytes: int, file_extension: str = "") -> int:
    """Estimate how many tokens a file would consume if read natively.

    The estimate accounts for different file types:
      - code / text / markdown: 4 chars/token
      - JSON / structured: 3 chars/token
      - PDF (binary, encoded): ~1.4x overhead (base85/hex encoding)
    """
    ext = file_extension.lower().lstrip(".")
    chars_per_token = CHARS_PER_TOKEN_TEXT

    if ext in {"json", "csv", "yaml", "yml", "toml", "xml", "html"}:
        chars_per_token = CHARS_PER_TOKEN_JSON
    elif ext == "pdf":
        # PDFs are base85-encoded by the API; ~1.4 bytes → 1 char → 0.25 token
        return max(1, int(size_bytes * 1.4 / CHARS_PER_TOKEN_TEXT))

    return max(1, int(size_bytes / chars_per_token))


def estimate_response_tokens(text: str) -> int:
    """Estimate token count for a SR tool response (JSON string)."""
    if not text:
        return 0
    return max(1, int(len(text) / CHARS_PER_TOKEN_JSON))


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TokenRecord:
    """A single token-tracking event."""

    timestamp: float = field(default_factory=time.time)
    operation: str = ""  # preview / read / card / raw / decide / preflight
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
    count_mode: str = "heuristic"  # "api" | "heuristic"

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
    """Cumulative token metrics for a session."""

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


# ---------------------------------------------------------------------------
# API-based precision counting (Anthropic /v1/messages/count_tokens)
# ---------------------------------------------------------------------------


def _get_api_client():
    """Return an Anthropic client if credentials are available, else None."""
    try:
        import anthropic

        # Check for API key or OAuth profile
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return anthropic.Anthropic()
        # Check for ant auth login profile
        config_dir = os.environ.get("ANTHROPIC_CONFIG_DIR") or os.path.join(
            os.path.expanduser("~"), ".config", "anthropic"
        )
        if os.path.isdir(config_dir):
            return anthropic.Anthropic()
    except Exception:
        pass
    return None


def _api_count_tokens(text: str, client, model: str = "claude-opus-4-8") -> int | None:
    """Get ground-truth token count from the Anthropic API. Returns None on failure."""
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


# ---------------------------------------------------------------------------
# TokenTracker
# ---------------------------------------------------------------------------


class TokenTracker:
    """Tracks SR token consumption with per-operation granularity.

    All metrics are calculated from within SR — no dependency on the host
    platform (Claude Code, OpenClaw, etc.) internals.

    The tracker writes a JSONL log for cross-session persistence:

      ~/.claude/sro_token_log.jsonl   (default)

    Each log line is a JSON TokenRecord, suitable for streaming analysis
    with `tail -f`, `jq`, or the companion `token_tracker_cli.py`.
    """

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

        # Resolve log path
        if log_dir is None:
            log_dir = Path.home() / ".claude"
        self._log_path = Path(log_dir) / "sro_token_log.jsonl"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_preview(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
        artifact_id: str = "",
    ) -> TokenRecord:
        """Record a preview operation."""
        return self._record("preview", file_path, file_size_bytes, file_extension, response_json, "", artifact_id)

    def record_read(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
        mode: str = "",
        artifact_id: str = "",
    ) -> TokenRecord:
        """Record a read (scout/focus/collect/refine/verify) operation."""
        return self._record("read", file_path, file_size_bytes, file_extension, response_json, mode, artifact_id)

    def record_card(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
    ) -> TokenRecord:
        """Record a card inspection."""
        return self._record("card", file_path, file_size_bytes, file_extension, response_json, "", "")

    def record_raw(
        self,
        file_path: str,
        file_size_bytes: int,
        file_extension: str = "",
        response_json: str = "",
        artifact_id: str = "",
    ) -> TokenRecord:
        """Record a raw content retrieval."""
        return self._record("raw", file_path, file_size_bytes, file_extension, response_json, "", artifact_id)

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
        # Use API for precision when available, fall back to heuristic
        count_mode = "heuristic"
        full_tokens = 0
        sr_response_tokens = 0

        if self._api_client is not None:
            # Read file content for API counting (most precise)
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

        if count_mode == "heuristic":
            full_tokens = estimate_file_tokens(file_size_bytes, file_extension)
            sr_response_tokens = estimate_response_tokens(response_json)

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

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def session_summary(self) -> SessionSummary:
        """Return cumulative token metrics for this session."""
        records = self.records
        total_ops = len(records)
        total_full = sum(r.full_file_tokens for r in records)
        total_sr = sum(r.sr_response_tokens for r in records)
        total_saved = sum(r.tokens_saved for r in records)
        overall_ratio = total_saved / total_full if total_full > 0 else 0.0
        context_retained = (total_saved / self.context_window * 100) if self.context_window > 0 else 0.0

        by_op: dict[str, dict[str, int]] = {}
        for r in records:
            if r.operation not in by_op:
                by_op[r.operation] = {"count": 0, "full_tokens": 0, "sr_tokens": 0, "saved": 0}
            by_op[r.operation]["count"] += 1
            by_op[r.operation]["full_tokens"] += r.full_file_tokens
            by_op[r.operation]["sr_tokens"] += r.sr_response_tokens
            by_op[r.operation]["saved"] += r.tokens_saved

        top = sorted(records, key=lambda r: r.tokens_saved, reverse=True)

        return SessionSummary(
            total_operations=total_ops,
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
        """Full tracker state as a JSON-serializable dict."""
        return {
            "uptime_seconds": round(time.time() - self._started_at, 3),
            "context_window": self.context_window,
            "log_path": str(self._log_path),
            "record_count": len(self.records),
            "records": [r.to_dict() for r in self.records],
            "session": self.session_summary().to_dict(),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _append_log(self, record: TokenRecord) -> None:
        """Append a single record to the JSONL log file."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError:
            pass  # non-critical; don't crash SR for a log write failure

    @classmethod
    def read_log(cls, log_path: str | Path) -> list[dict[str, Any]]:
        """Read and parse the JSONL token log file."""
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

    @classmethod
    def cumulative_from_log(cls, log_path: str | Path) -> SessionSummary:
        """Compute cumulative metrics from a JSONL log file."""
        raw = cls.read_log(log_path)
        records = []
        for item in raw:
            records.append(
                TokenRecord(
                    timestamp=item.get("ts", 0),
                    operation=item.get("op", ""),
                    file_path=item.get("path", ""),
                    file_size_bytes=item.get("file_bytes", 0),
                    file_extension=item.get("ext", ""),
                    full_file_tokens=item.get("full_tokens", 0),
                    sr_response_chars=item.get("sr_chars", 0),
                    sr_response_tokens=item.get("sr_tokens", 0),
                    tokens_saved=item.get("saved", 0),
                    savings_ratio=item.get("ratio", 0.0),
                    mode=item.get("mode", ""),
                    artifact_id=item.get("artifact", ""),
                )
            )
        tracker = cls(enable_log=False)
        tracker.records = records
        return tracker.session_summary()


# ---------------------------------------------------------------------------
# Optional: Anthropic API ground-truth token counting
# ---------------------------------------------------------------------------


def count_tokens_api(text: str, model: str = "claude-opus-4-8") -> int | None:
    """Get the exact token count from the Anthropic Messages API.

    Requires ANTHROPIC_API_KEY in the environment or an `ant auth login`
    session.  Returns None if authentication is not available.

    This is the ground truth — use it for calibrating heuristics or
    validating savings claims in formal benchmarks.
    """
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
