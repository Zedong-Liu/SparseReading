#!/usr/bin/env python3
"""Offline automated grading and paired aggregation for OpenCode runsets.

This intentionally does not modify runner ``summary.json`` files.  It writes
per-case ``automated_grade.json`` audit sidecars plus one runset-level report.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
QCB_ROOT = ROOT / "SRO_test" / "qwenclawbench"
OPENCLAW_RUNNER_DIR = ROOT / "integrations" / "openclaw"
if str(OPENCLAW_RUNNER_DIR) not in sys.path:
    sys.path.insert(0, str(OPENCLAW_RUNNER_DIR))

from run_openclaw_unified14 import grade_automated, load_task  # noqa: E402


MODES = ("native_truncation", "plugin_auto")
MODEL_TIMEOUT_SECONDS = 1800.0
INFRA_MARKERS = ("database is locked", "429", "rate limit", "connection reset")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def duration_seconds(summary: dict[str, Any]) -> float | None:
    match = re.search(r"duration=([0-9.]+)s", str(summary.get("notes") or ""))
    return float(match.group(1)) if match else None


def case_classification(case_dir: Path, summary: dict[str, Any]) -> str:
    status = str(summary.get("status") or "")
    if status == "ok":
        return "valid"
    details = "\n".join((text(case_dir / "stdout.txt"), text(case_dir / "stderr.txt"))).lower()
    if any(marker in details for marker in INFRA_MARKERS):
        return "infra_missing"
    if "timeout" in status.lower() or "timed out" in details:
        return "model_timeout"
    return "failed"


def trace_events(case_dir: Path) -> list[dict[str, Any]]:
    path = case_dir / "traces" / "opencode_trace_summary.json"
    if not path.exists():
        return []
    events = read_json(path).get("events")
    return events if isinstance(events, list) else []


def audit_case(case_dir: Path) -> dict[str, Any]:
    summary = read_json(case_dir / "summary.json")
    classification = case_classification(case_dir, summary)
    task_id = str(summary["task"])
    duration = duration_seconds(summary)
    if classification == "model_timeout":
        grade: dict[str, Any] = {
            "score": 0.0,
            "max_score": 1.0,
            "scores": {},
            "notes": "valid model timeout scored zero",
        }
        duration = MODEL_TIMEOUT_SECONDS
    elif classification == "valid":
        grade = grade_automated(load_task(task_id), case_dir / "runtime", trace_events(case_dir))
    else:
        grade = {
            "score": None,
            "max_score": 1.0,
            "scores": {},
            "notes": "not graded: infrastructure or runner failure",
        }
    return {
        "task": task_id,
        "mode": summary.get("mode"),
        "case_dir": str(case_dir),
        "classification": classification,
        "tokens": summary.get("tokens"),
        "duration_seconds": duration,
        "automated_grade": grade,
    }


def paired_metrics(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_task: dict[str, dict[str, dict[str, Any]]] = {}
    for item in cases:
        by_task.setdefault(item["task"], {})[str(item["mode"])] = item
    paired: list[dict[str, Any]] = []
    excluded: dict[str, str] = {}
    for task, modes in sorted(by_task.items()):
        native, sparse = modes.get("native_truncation"), modes.get("plugin_auto")
        if native is None or sparse is None:
            excluded[task] = "missing mode"
            continue
        if native["classification"] == "infra_missing" or sparse["classification"] == "infra_missing":
            excluded[task] = "infrastructure missing"
            continue
        if native["classification"] not in {"valid", "model_timeout"} or sparse["classification"] not in {"valid", "model_timeout"}:
            excluded[task] = "non-comparable failure"
            continue
        native_score = native["automated_grade"].get("score")
        sparse_score = sparse["automated_grade"].get("score")
        if not isinstance(native_score, (int, float)) or not isinstance(sparse_score, (int, float)):
            excluded[task] = "missing automated grade"
            continue
        row: dict[str, Any] = {
            "task": task,
            "score_delta": float(sparse_score) - float(native_score),
        }
        native_tokens, sparse_tokens = native.get("tokens"), sparse.get("tokens")
        if isinstance(native_tokens, int) and isinstance(sparse_tokens, int) and native_tokens > 0:
            row["token_reduction"] = (native_tokens - sparse_tokens) / native_tokens
        native_time, sparse_time = native.get("duration_seconds"), sparse.get("duration_seconds")
        if isinstance(native_time, (int, float)) and isinstance(sparse_time, (int, float)) and native_time > 0:
            row["time_save"] = (native_time - sparse_time) / native_time
        paired.append(row)

    def median_metric(key: str) -> float | None:
        values = [float(row[key]) for row in paired if isinstance(row.get(key), (int, float))]
        return statistics.median(values) if values else None

    score_values = [float(row["score_delta"]) for row in paired]
    return {
        "expected_tasks": len(by_task),
        "paired_tasks": len(paired),
        "excluded_tasks": excluded,
        "token_reduction_median": median_metric("token_reduction"),
        "score_delta_mean": statistics.mean(score_values) if score_values else None,
        "time_save_median": median_metric("time_save"),
        "pairs": paired,
    }


def audit_runset(runset: str) -> dict[str, Any]:
    run_root = QCB_ROOT / runset
    cases: list[dict[str, Any]] = []
    for mode in MODES:
        for summary_path in sorted((run_root / mode).glob("*/summary.json")):
            item = audit_case(summary_path.parent)
            (summary_path.parent / "automated_grade.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            cases.append(item)
    result = {"runset": runset, "cases": cases, "aggregate": paired_metrics(cases)}
    (run_root / "opencode_automated_audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runsets", nargs="+", help="QwenClawBench OpenCode runset names")
    args = parser.parse_args()
    for runset in args.runsets:
        result = audit_runset(runset)
        aggregate = result["aggregate"]
        print(
            json.dumps(
                {
                    "runset": runset,
                    "paired_tasks": aggregate["paired_tasks"],
                    "expected_tasks": aggregate["expected_tasks"],
                    "token_reduction_median": aggregate["token_reduction_median"],
                    "score_delta_mean": aggregate["score_delta_mean"],
                    "time_save_median": aggregate["time_save_median"],
                    "excluded_tasks": aggregate["excluded_tasks"],
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
