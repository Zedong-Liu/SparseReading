#!/usr/bin/env python3
"""SparseRead Token Log Analyzer — standalone CLI tool.

Reads the SparseRead token JSONL log file (~/.claude/sro_token_log.jsonl)
and produces a human-readable token consumption report.  No project
dependencies required — pure standard library.

Usage:
  python token_analyzer.py                  # summary of all records
  python token_analyzer.py --tail 20        # last 20 records
  python token_analyzer.py --json           # JSON output for scripting
  python token_analyzer.py --log /path/to/sro_token_log.jsonl

This tool is portable — copy it to any machine with a SparseRead token
log file and run with `python token_analyzer.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_LOG = Path.home() / ".claude" / "sro_token_log.jsonl"


def read_log(log_path: Path) -> list[dict]:
    records = []
    if not log_path.exists():
        return records
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def summarize(records: list[dict]) -> dict:
    if not records:
        return {"records": 0}

    total_ops = len(records)
    total_full = sum(r.get("full_tokens", 0) for r in records)
    total_sr = sum(r.get("sr_tokens", 0) for r in records)
    total_saved = sum(r.get("saved", 0) for r in records)
    ratio = total_saved / total_full if total_full > 0 else 0.0

    by_op = {}
    for r in records:
        op = r.get("op", "unknown")
        by_op.setdefault(op, {"count": 0, "full": 0, "sr": 0, "saved": 0})
        by_op[op]["count"] += 1
        by_op[op]["full"] += r.get("full_tokens", 0)
        by_op[op]["sr"] += r.get("sr_tokens", 0)
        by_op[op]["saved"] += r.get("saved", 0)

    top = sorted(records, key=lambda r: r.get("saved", 0), reverse=True)[:10]

    return {
        "records": total_ops,
        "full_file_tokens": total_full,
        "sr_response_tokens": total_sr,
        "tokens_saved": total_saved,
        "savings_ratio": round(ratio, 4),
        "savings_pct": f"{ratio * 100:.1f}%",
        "by_operation": by_op,
        "top_savings": [
            {"path": r.get("path", ""), "saved": r.get("saved", 0), "ratio": r.get("ratio", 0)}
            for r in top
        ],
    }


def print_summary(stats: dict) -> None:
    if stats.get("records", 0) == 0:
        print("No SparseRead token records found.")
        print(f"(Expected log at: {DEFAULT_LOG})")
        return

    rows = [
        ("", ""),
        ("╔══════════════════════════════════════════════════╗", ""),
        ("║     SparseRead Token Consumption Report          ║", ""),
        ("╚══════════════════════════════════════════════════╝", ""),
        ("", ""),
        (f"  Total SR operations:       {stats['records']:>8d}", ""),
        ("", ""),
        (f"  Full-file tokens (est):    {stats['full_file_tokens']:>8,d}", ""),
        (f"  SR response tokens (est):  {stats['sr_response_tokens']:>8,d}", ""),
        (f"  Tokens SAVED:              {stats['tokens_saved']:>8,d}", ""),
        ("", ""),
        (f"  Savings ratio:             {stats['savings_pct']:>10}", ""),
        ("", ""),
    ]
    for label, _ in rows:
        print(label)

    by_op = stats.get("by_operation", {})
    if by_op:
        print("  By operation type:")
        print(f"    {'Operation':<12} {'Count':>6} {'Full tokens':>14} {'SR tokens':>12} {'Saved':>12}")
        print(f"    {'-'*12} {'-'*6} {'-'*14} {'-'*12} {'-'*12}")
        for op, data in sorted(by_op.items()):
            print(
                f"    {op:<12} {data['count']:>6d} {data['full']:>14,d} "
                f"{data['sr']:>12,d} {data['saved']:>12,d}"
            )

    top = stats.get("top_savings", [])
    if top:
        print()
        print("  Top savings:")
        for i, item in enumerate(top[:5], 1):
            path = item["path"]
            short = path.replace("\\", "/").split("/")[-1] if path else "?"
            ratio = item.get("ratio", 0)
            print(f"    {i}. {short}: {item['saved']:,d} tokens saved ({ratio:.1%})")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SparseRead Token Log Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log", default=str(DEFAULT_LOG), help=f"Path to token log (default: {DEFAULT_LOG})")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--tail", type=int, default=0, help="Show only the last N records")
    args = parser.parse_args(argv)

    records = read_log(Path(args.log))
    if args.tail > 0:
        records = records[-args.tail:]

    stats = summarize(records)

    if args.json:
        json.dump(stats, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print_summary(stats)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
