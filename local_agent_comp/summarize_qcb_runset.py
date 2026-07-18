#!/usr/bin/env python3
"""Summarize paired baseline/gate results from a QwenClawBench runset."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys


FIELDS = [
    "model",
    "task_id",
    "baseline_score",
    "sro_score",
    "baseline_tokens",
    "sro_tokens",
    "baseline_req",
    "sro_req",
    "baseline_seconds",
    "sro_seconds",
    "baseline_status",
    "sro_status",
]


def read_result(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text())
    task = payload["tasks"][0]
    return {
        "model": payload["model"],
        "task_id": task["task_id"],
        "score": task["grading"]["mean"],
        "tokens": task["usage"]["total_tokens"],
        "req": task["usage"]["request_count"],
        "seconds": round(task["execution_time"], 3),
        "status": task["status"],
    }


def collect(runset: Path, baseline_mode: str, sro_mode: str) -> list[dict[str, object]]:
    baseline = {
        path.parent.name: read_result(path)
        for path in (runset / baseline_mode).glob("*/result.json")
    }
    sro = {
        path.parent.name: read_result(path)
        for path in (runset / sro_mode).glob("*/result.json")
    }
    missing = sorted(set(baseline) ^ set(sro))
    if missing:
        raise ValueError(f"unpaired tasks: {', '.join(missing)}")
    rows = []
    for task_dir in sorted(baseline):
        base = baseline[task_dir]
        candidate = sro[task_dir]
        if base["model"] != candidate["model"]:
            raise ValueError(f"model mismatch for {task_dir}")
        rows.append(
            {
                "model": base["model"],
                "task_id": base["task_id"],
                "baseline_score": base["score"],
                "sro_score": candidate["score"],
                "baseline_tokens": base["tokens"],
                "sro_tokens": candidate["tokens"],
                "baseline_req": base["req"],
                "sro_req": candidate["req"],
                "baseline_seconds": base["seconds"],
                "sro_seconds": candidate["seconds"],
                "baseline_status": base["status"],
                "sro_status": candidate["status"],
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("runset", type=Path)
    parser.add_argument("--baseline-mode", default="baseline")
    parser.add_argument("--sro-mode", default="gate")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = collect(args.runset, args.baseline_mode, args.sro_mode)
    stream = args.output.open("w", newline="") if args.output else sys.stdout
    try:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if args.output:
            stream.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
