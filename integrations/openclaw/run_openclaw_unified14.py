#!/usr/bin/env python3
"""Run the README unified 14-task OpenClaw SparseRead validation.

This runner is framework-adapter evaluation only.  Benchmark hints are kept
short and generic; product routing remains in the OpenClaw adapter gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from run_openclaw_validation import (
    CORE_DIR,
    DEFAULT_MODEL,
    DEFAULT_PROFILE,
    PLUGIN_DIR,
    REPO,
    checked,
    estimate_transcript_tokens,
    extract_final_prompt_tokens,
    iter_session_events,
    parse_agent_json,
    session_file_from_result,
    session_metrics,
    set_plugin_enabled,
    set_workspace,
)


BASELINE = REPO / "SRO_test" / "qwenclawbench" / "baseline"
REFERENCE_CSV = REPO / "figures" / "sro_experiment_data.csv"

UNIFIED14_TASK_IDS = [
    "task_loogle_shortdep_fall_of_outremer",
    "task_loogle_shortdep_fall_of_outremer_5q",
    "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
    "task_21_openclaw_comprehension",
    "task_00036_find_largest_file_in_downloads_directory",
    "task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix",
    "task_00058_did_regression_on_simulated_panel_data",
    "task_00059_user_discount_calculator",
    "task_00067_write_sparql_query_for_product_reviews_containing_iphone",
    "task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis",
    "task_00086_command_prefix_security_analysis",
    "task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site",
    "task_00098_diagnose_scheduled_book_recommendation_failure",
]

REFERENCE_ALIASES = {
    "task_21_openclaw_comprehension": "task_21",
}

BENCH_COLLECT_HINTS: dict[str, dict[str, Any]] = {
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check": {
        "target": "a_stock_announcements",
        "mode": "collect",
        "want": "fact",
        "type_hint": "collection",
        "slots": [
            {"id": "state_vs_output", "question": "35 seen_ids vs 11 output records and the 24 orphaned IDs"},
            {"id": "missing_csv", "question": "csv_summary true but summary_2026-02-09.csv is absent"},
            {"id": "dedup_bug", "question": "deduplicate list(seen)[-5000:] ordering bug and sorted(seen, key=int)[-5000:] fix"},
            {"id": "important_announcements", "question": "exactly five important announcements with IDs and company names"},
            {"id": "config_cross_check", "question": "max_pages, fetch_sse, request_delay, category, notifications"},
        ],
    },
    "task_00086_command_prefix_security_analysis": {
        "target": ".",
        "mode": "collect",
        "want": "fact",
        "type_hint": "collection",
        "slots": [
            {"id": "pipeline_commands", "question": "three non-trivial commands in scripts/run_pipeline.sh and their raw command strings"},
            {"id": "classifications", "question": "curl pipe bash injection, python3 safe prefix, claude safe prefix with high-risk flag"},
            {"id": "test_summary", "question": "data/test_commands.csv total commands, injection count, safe count"},
            {"id": "policy_conflicts", "question": "KI-007/KI-008, LEGACY-R003, SAB-2025-001 conflicts and why security_policy.yaml v3.2.0 wins"},
            {"id": "deliverables", "question": "security_analysis_report.md template needs and command_classifications.json required schema"},
        ],
        "trajectory": "one_collect_then_write",
    },
}


@dataclass(frozen=True)
class LoadedTask:
    task_id: str
    root: Path
    metadata: dict[str, Any]
    prompt: str
    automated_checks: str

    @property
    def assets_dir(self) -> Path:
        return self.root / "runtime" / "assets"

    @property
    def task_md(self) -> Path:
        return self.root / "runtime" / "tasks" / f"{self.task_id}.md"

    @property
    def grading_type(self) -> str:
        return str(self.metadata.get("grading_type") or "automated")

    @property
    def timeout_seconds(self) -> int:
        return int(self.metadata.get("timeout_seconds") or 600)

    @property
    def workspace_files(self) -> list[dict[str, Any]]:
        value = self.metadata.get("workspace_files") or []
        return value if isinstance(value, list) else []


def run(cmd: list[str], *, input_text: str | None = None, timeout: int = 120, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        cmd,
        cwd=REPO,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=merged_env,
    )


def ensure_plugin_runtime() -> None:
    def npm(cmd: list[str], timeout: int) -> None:
        proc = subprocess.run(cmd, cwd=PLUGIN_DIR, text=True, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )

    if not (PLUGIN_DIR / "node_modules" / "typebox").exists():
        npm(["npm", "install", "--ignore-scripts"], timeout=180)
    npm(["npm", "run", "build"], timeout=120)



def load_task(task_id: str) -> LoadedTask:
    root = BASELINE / task_id
    task_md = root / "runtime" / "tasks" / f"{task_id}.md"
    text = task_md.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(?P<yaml>.*?)\n---\s*\n(?P<body>.*)$", text, re.S)
    if not match:
        raise ValueError(f"missing frontmatter: {task_md}")
    metadata = yaml.safe_load(match.group("yaml")) or {}
    sections = parse_sections(match.group("body"))
    return LoadedTask(
        task_id=task_id,
        root=root,
        metadata=metadata,
        prompt=sections.get("Prompt", "").strip(),
        automated_checks=sections.get("Automated Checks", ""),
    )


def parse_sections(body: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in body.splitlines():
        header = re.match(r"^##\s+(.+)$", line)
        if header:
            current = header.group(1).strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {key: "\n".join(value).strip() for key, value in sections.items()}


def materialize_assets(task: LoadedTask, workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    files = task.workspace_files
    if files:
        for spec in files:
            if not isinstance(spec, dict):
                continue
            if "content" in spec:
                dest = workspace / str(spec.get("path") or spec.get("dest") or "input.txt")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(str(spec["content"]), encoding="utf-8")
                continue
            source = task.assets_dir / str(spec.get("source"))
            dest = workspace / str(spec.get("dest") or spec.get("source"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        return

    for source in task.assets_dir.rglob("*"):
        if source.is_dir():
            continue
        dest = workspace / source.relative_to(task.assets_dir)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def target_hint(task: LoadedTask) -> str:
    task_text = (task.prompt + "\n" + task.automated_checks).lower()
    if "downloads/" in task_text and "largest" in task_text and "text-like" in task_text:
        return "downloads"
    if "scheduled" in task_text and "notification" in task_text and "diagnosis_report.md" in task_text:
        return "."
    if task.task_id in BENCH_COLLECT_HINTS:
        return str(BENCH_COLLECT_HINTS[task.task_id]["target"])
    if task.task_id.startswith("task_loogle"):
        return "document.txt"
    if task.task_id == "task_21_openclaw_comprehension":
        return "openclaw_report.pdf"
    if task.task_id.startswith("task_00012"):
        return "a_stock_announcements"
    for spec in task.workspace_files:
        if not isinstance(spec, dict):
            continue
        dest = str(spec.get("dest") or spec.get("source") or "")
        if dest.endswith((".pdf", ".txt", ".md")):
            return dest
    return "."


def numbered_question_slots(prompt: str) -> list[dict[str, str]]:
    slots: list[dict[str, str]] = []
    for match in re.finditer(r"(?m)^\s*(\d+)\.\s+(.+?)\s*$", prompt):
        question = match.group(2).strip()
        if not question or len(question) > 240:
            continue
        slots.append({"id": f"q{match.group(1)}", "question": question})
    return slots


def fixed_reference_dates(prompt: str) -> list[str]:
    dates: list[str] = []
    for pattern in [
        r"\bas of\s+(\d{4}-\d{2}-\d{2})",
        r"\bcomputed as of\s+(\d{4}-\d{2}-\d{2})",
        r"\bevaluat(?:e|ed|ions?)\s+(?:from .*?\s+)?as of\s+(\d{4}-\d{2}-\d{2})",
        r"\brelative to\s+(\d{4}-\d{2}-\d{2})",
    ]:
        for match in re.finditer(pattern, prompt, re.I):
            if match.group(1) not in dates:
                dates.append(match.group(1))
    return dates[:3]


def sro_collect_call_example(hint: dict[str, Any]) -> dict[str, Any]:
    return {
        "target": {"artifact_id": "<artifact_id from sro_preview>"},
        "mode": str(hint.get("mode") or "collect"),
        "hint": {
            "goal": str(hint.get("goal") or "answer the task from the target artifact"),
            "want": str(hint.get("want") or "fact"),
            "scope": str(hint.get("scope") or "new"),
            "type_hint": str(hint.get("type_hint") or "auto"),
            "slots": hint.get("slots") or [],
        },
    }


def feature_guidance(task: LoadedTask, task_spec_text: str) -> str:
    text = task_spec_text.lower()
    guidance = ""
    if "literature retrieval" in text and ("cron_config" in text or "verified_rss_sources" in text):
        guidance += (
            "- This is a native diagnosis/code-fix task. Use native reads/grep/Python over "
            "`cron_config.json`, `cron_logs/`, `literature_results/`, and `scripts/`; do not "
            "start with SparseRead unless a native result is actually truncated.\n"
        )
    if "downloads/" in text and "largest" in text and "text-like" in text:
        guidance += (
            "- This is a native filesystem/stat task, not a SparseRead task. Use shell/Python "
            "inspection such as `find`, `stat`, `file`, or `pathlib` on `downloads/`; exclude "
            "PDF/ZIP/PNG from the considered list, and write only the text-like candidates in "
            "`downloads/text_file_size_report.txt`.\n"
        )
    if "scheduled" in text and "notification" in text and "diagnosis_report.md" in text:
        guidance += (
            "- For scheduled notification diagnosis bundles, use native reads/grep/Python over the "
            "small log/config/script/template files. Do not call sro_preview/sro_read unless a native "
            "tool result is actually truncated; this task should normally be one native evidence "
            "pass followed by writing `diagnosis_report.md` and `book_recommendation.sh`. The skill "
            "deliverable is optional; skip `skill_workshop` when it would add another exploration loop.\n"
            "- In `diagnosis_report.md`, explicitly cover: "
            "`retry_after=3600` versus configured `delay_seconds=300`, non-daily execution gaps "
            "or missing log dates, at least one secondary config/script issue such as timezone "
            "ambiguity, `rate_limit` not enforced by the script, or placeholder/no real API send, "
            "and concrete remediation such as honoring `retry_after` or activating the configured "
            "Discord fallback.\n"
        )
    if "discount" in text and "discount_calculator.py" in text:
        guidance += (
            "- This is a native calculation/code task over small rule and user files. Use native "
            "reads and local Python tests; do not use SparseRead unless a native read is truncated.\n"
        )
    if ("p&l" in text or "p/l" in text or "pnl" in text) and "transactions" in text:
        guidance += (
            "- This is a native full-table analysis task. Use local Python/pandas or csv/json parsing "
            "over the complete transaction files; do not use SparseRead for row-level computation.\n"
        )
    if "exam monitoring system" in text and "monitoring-status.md" in text:
        guidance += (
            "- For monitoring status audits, use a small Python/JSON command to compute the site "
            "inventory from `config/sites.json` before writing: total sites, verified+enabled count, "
            "verified+enabled names, and unverified/disabled count. Then cross-check `cron_schedule.conf`, "
            "`pta_monitor.py`, and `config/feishu.json`; explicitly mention Hubei 502 and Henan 404 "
            "if present in site notes.\n"
        )
    return guidance


def validation_prompt(task: LoadedTask, mode: str, *, diagnostic_hints: bool = False) -> str:
    if mode == "native":
        return task.prompt
    slots = numbered_question_slots(task.prompt)
    slot_hint = ""
    if slots:
        hint = {
            "mode": "collect",
            "goal": "answer the numbered questions from the target artifact",
            "want": "fact",
            "scope": "new",
            "type_hint": "auto",
            "slots": slots,
        }
        slot_hint = (
            "For numbered question-answer tasks, build the first targeted read exactly like this after sro_preview; "
            "replace the artifact_id placeholder with the value returned by preview:\n"
            + json.dumps(sro_collect_call_example(hint), ensure_ascii=False)
            + "\n"
        )
    if not diagnostic_hints:
        task_spec_text = task.task_md.read_text(encoding="utf-8", errors="replace")
        feature_hint = feature_guidance(task, task_spec_text)
        if feature_hint:
            return (
                task.prompt
                + "\n\nSparseRead is available, but this workspace shape is native-fit. "
                + "Follow the native guidance below and use sro_preview/sro_read only if a native result is actually truncated.\n"
                + feature_hint
            )
        return (
            task.prompt
            + "\n\nSparseRead is available for long documents, PDFs, and multi-file evidence bundles. "
            + "Use native reads for small files, config edits, scripts, calculations, and full-table work. "
            + "For long documents or compact evidence collections, call sro_preview first, then sro_read(mode=\"collect\") "
            + "with the returned artifact_id only when targeted evidence is needed. In HintSpec, `want` must be fact/list/count/schema/table/verbatim, "
            + "`scope` must be new/narrow/expand/verify, and `slots` must be objects with id and question, never strings. "
            + slot_hint
            + "When evidence_pack.slot_digest.overall_status is ready, write the deliverable immediately. "
            + "If the task asks for one answer per line, write raw answers only: no numbering, bullets, labels, or prefixes."
        )
    task_spec_text = task.task_md.read_text(encoding="utf-8", errors="replace")
    code_hint = ""
    if re.search(r"\bpython\b|\.py\b|python script", task.prompt, re.I):
        dates = fixed_reference_dates(task_spec_text)
        date_hint = ""
        if dates:
            date_hint = f" The task gives fixed reference date(s) {', '.join(dates)}; use the relevant one as the default in reusable code."
        code_hint = (
            "- When creating Python files, make them compatible with the workspace `python3` runtime "
            "(Python 3.9 on this machine): avoid Python 3.10+ union annotation syntax such as `str | Path`, "
            "avoid dataclass annotation/importlib traps, and verify the file is importable.\n"
            "- If the task states an evaluation/reference/as-of date, use that date as the default in reusable code; "
            "do not default business-rule calculations to today's date when the task gives a fixed date."
            + date_hint
            + "\n"
        )
        if re.search(r"\bdiscount\b", task.prompt, re.I):
            code_hint += (
                "- For discount/calculation modules, expose a simple reusable entry point named "
                "`calculate_discount`, `get_discount`, `compute_discount`, `calculate_user_discount`, "
                "or a `DiscountCalculator` method with one of those names. It should accept a user row dict "
                "and rules/rules_path where appropriate, and return a numeric discount or a dict containing "
                "`discount_pct`/`final_discount_pct`.\n"
            )
    explicit_hint = BENCH_COLLECT_HINTS.get(task.task_id)
    feature_hint = feature_guidance(task, task_spec_text)
    if explicit_hint:
        prefix = (
            "SparseRead validation protocol (benchmark only, not a product gate):\n"
            f"- First action: call sro_preview(path='{explicit_hint['target']}') before native reads, directory listing, grep, or exec inspection.\n"
            "- Then call exactly one sro_read with the returned artifact_id and this shape if targeted evidence is needed; keep `want` inside `hint` and do not put `want` inside each slot:\n"
            + json.dumps(sro_collect_call_example(explicit_hint), ensure_ascii=False)
            + "\n"
            "- When the collect result is ready or allowed_next includes write_file/write, write the requested deliverable immediately. Do not verify/refine resolved slots.\n\n"
        )
        return prefix + task.prompt
    return (
        task.prompt
        + "\n\nSparseRead validation hint (benchmark only, not a product gate):\n"
        + f"- Candidate evidence target: `{target_hint(task)}`.\n"
        + "- Use native reads for small files, config edits, scripts, calculations, and full-table work.\n"
        + "- For long documents, PDFs, reports, or compact evidence closures, call sro_preview first. If the OpenClaw gate recommends SparseRead and preview is insufficient, call exactly one sro_read(mode=collect), then write the requested deliverable immediately when ready.\n"
        + slot_hint
        + feature_hint
        + code_hint
        + "- Do not call sro_read with list-of-string slots. Do not call verify/refine after ready evidence unless a named required slot is unresolved.\n"
    )


def transcript_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return events


def grade_automated(task: LoadedTask, workspace: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    checks = task.automated_checks
    match = re.search(r"```python\s*(.*?)\s*```", checks, re.S)
    if not match:
        return {"score": 0.0, "max_score": 1.0, "scores": {}, "notes": "no automated checks"}
    namespace: dict[str, Any] = {}
    try:
        exec(match.group(1), namespace)
        grade_func = namespace.get("grade")
        if not callable(grade_func):
            return {"score": 0.0, "max_score": 1.0, "scores": {}, "notes": "grade function missing"}
        scores = grade_func(transcript_from_events(events), str(workspace))
    except Exception as exc:
        return {"score": 0.0, "max_score": 1.0, "scores": {}, "notes": f"automated grade error: {exc}"}
    if not isinstance(scores, dict):
        scores = {}
    values = [float(value) for value in scores.values() if isinstance(value, (int, float))]
    return {
        "score": sum(values) / len(values) if values else 0.0,
        "max_score": 1.0,
        "scores": {key: float(value) for key, value in scores.items() if isinstance(value, (int, float))},
        "notes": "automated-only grade",
        "source_grading_type": task.grading_type,
    }


def run_case(task: LoadedTask, mode: str, run_root: Path, profile: str, model: str, timeout_multiplier: float, *, diagnostic_hints: bool = False) -> dict[str, Any]:
    case_root = run_root / task.task_id / mode
    workspace = case_root / "workspace"
    case_root.mkdir(parents=True, exist_ok=True)
    materialize_assets(task, workspace)
    set_workspace(profile, workspace)
    set_plugin_enabled(profile, mode != "native", policy="auto", workspace=workspace)

    prompt = validation_prompt(task, mode, diagnostic_hints=diagnostic_hints)
    (case_root / "prompt.txt").write_text(prompt, encoding="utf-8")
    session_key = f"srou14-{datetime.now().strftime('%Y%m%d%H%M%S')}-{task.task_id}-{mode}"
    env = {"PYTHONPATH": str(CORE_DIR), "SPARSEREAD_PROJECT_ROOT": str(REPO)}
    timeout = max(60, int(task.timeout_seconds * timeout_multiplier))
    started = time.time()
    proc = run(
        [
            "npx",
            "-y",
            "openclaw",
            "--profile",
            profile,
            "agent",
            "--local",
            "--model",
            model,
            "--session-key",
            session_key,
            "--message",
            prompt,
            "--json",
            "--timeout",
            str(timeout),
        ],
        timeout=timeout + 60,
        env=env,
    )
    elapsed = time.time() - started
    (case_root / "stdout.json").write_text(proc.stdout, encoding="utf-8")
    (case_root / "stderr.txt").write_text(proc.stderr, encoding="utf-8")

    result = parse_agent_json(proc.stdout)
    session_file = session_file_from_result(result)
    events = iter_session_events(session_file)
    if session_file and session_file.exists():
        shutil.copy2(session_file, case_root / "session.jsonl")
    else:
        (case_root / "session.jsonl").write_text("", encoding="utf-8")

    metrics = session_metrics(events)
    metrics.update(estimate_transcript_tokens(events, result))
    metrics.update(
        {
            "returncode": proc.returncode,
            "elapsed_seconds": round(elapsed, 2),
            "session_file": str(session_file) if session_file else "",
            "estimated_final_prompt_tokens": extract_final_prompt_tokens(result),
            "stdout_chars": len(proc.stdout),
            "stderr_chars": len(proc.stderr),
        }
    )
    grade = grade_automated(task, workspace, events)
    case_result = {
        "task_id": task.task_id,
        "mode": mode,
        "case_root": str(case_root),
        "workspace": str(workspace),
        "target_hint": target_hint(task) if diagnostic_hints else "",
        "diagnostic_hints": diagnostic_hints,
        "metrics": metrics,
        "grade": grade,
    }
    (case_root / "case_result.json").write_text(json.dumps(case_result, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_result


def load_case(run_root: Path, task: LoadedTask, mode: str) -> dict[str, Any]:
    case_root = run_root / task.task_id / mode
    workspace = case_root / "workspace"
    result = parse_agent_json((case_root / "stdout.json").read_text(encoding="utf-8", errors="replace")) if (case_root / "stdout.json").exists() else {}
    events = iter_session_events(case_root / "session.jsonl")
    metrics = session_metrics(events)
    metrics.update(estimate_transcript_tokens(events, result))
    metrics.update(
        {
            "returncode": 0 if events else 1,
            "session_file": "",
            "estimated_final_prompt_tokens": extract_final_prompt_tokens(result),
            "stdout_chars": (case_root / "stdout.json").stat().st_size if (case_root / "stdout.json").exists() else 0,
            "stderr_chars": (case_root / "stderr.txt").stat().st_size if (case_root / "stderr.txt").exists() else 0,
        }
    )
    case_result = {
        "task_id": task.task_id,
        "mode": mode,
        "case_root": str(case_root),
        "workspace": str(workspace),
        "target_hint": target_hint(task),
        "metrics": metrics,
        "grade": grade_automated(task, workspace, events),
    }
    (case_root / "case_result.json").write_text(json.dumps(case_result, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_result


def load_references() -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    with REFERENCE_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("model") != "DeepSeek-V4-Flash":
                continue
            task_id = row.get("task_id") or ""
            current = refs.get(task_id)
            sro_tokens = int(float(row.get("sro_tokens") or 0))
            if current and current.get("sro_tokens", 10**18) <= sro_tokens:
                continue
            refs[task_id] = {
                "short_name": row.get("short_name", ""),
                "group": row.get("group", ""),
                "benchmark": row.get("benchmark", ""),
                "sro_score": float(row.get("sro_score") or 0.0),
                "sro_tokens": sro_tokens,
                "sro_req": int(float(row.get("sro_req") or 0)),
                "baseline_score": float(row.get("baseline_score") or 0.0),
                "baseline_tokens": int(float(row.get("baseline_tokens") or 0)),
                "baseline_req": int(float(row.get("baseline_req") or 0)),
                "note": row.get("note", ""),
            }
    return refs


def reference_for(task_id: str, refs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if task_id in refs:
        return refs[task_id]
    alias = REFERENCE_ALIASES.get(task_id)
    if alias and alias in refs:
        return refs[alias]
    short = "_".join(task_id.split("_")[:2])
    return refs.get(short) or {}


def compare_to_reference(result: dict[str, Any], ref: dict[str, Any]) -> dict[str, Any]:
    grade = result.get("grade", {})
    metrics = result.get("metrics", {})
    score = float(grade.get("score") or 0.0)
    tokens = int(metrics.get("estimated_total_tokens") or 0)
    tools = int(metrics.get("tool_calls") or 0)
    ref_score = float(ref.get("sro_score") or 0.0)
    ref_tokens = int(ref.get("sro_tokens") or 0)
    ref_req = int(ref.get("sro_req") or 0)
    score_close = score + 0.05 >= ref_score
    token_close = bool(ref_tokens) and tokens <= int(ref_tokens * 1.25)
    request_close = bool(ref_req) and int(metrics.get("assistant_messages") or 0) <= ref_req + 2
    return {
        "score_delta_vs_nanobot": round(score - ref_score, 4),
        "token_ratio_vs_nanobot": round(tokens / ref_tokens, 3) if ref_tokens else None,
        "tool_calls": tools,
        "score_close": score_close,
        "token_close_25pct": token_close,
        "request_close_plus2": request_close,
        "close_or_better": score_close and (token_close or request_close),
    }


def write_report(run_root: Path, results: list[dict[str, Any]]) -> None:
    refs = load_references()
    by_task: dict[str, dict[str, Any]] = {}
    for item in results:
        by_task.setdefault(item["task_id"], {})[item["mode"]] = item

    comparisons: dict[str, Any] = {}
    for task_id, modes in by_task.items():
        sr = modes.get("sr")
        if not sr:
            continue
        comparisons[task_id] = compare_to_reference(sr, reference_for(task_id, refs))

    summary = {"run_root": str(run_root), "results": results, "comparisons": comparisons}
    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# OpenClaw SparseRead Unified14 Validation",
        "",
        f"- Run root: `{run_root}`",
        "- Reference: `figures/sro_experiment_data.csv` DeepSeek-V4-Flash nanobot-SR rows",
        "- Grade: automated checks only; hybrid LLM judge is intentionally not run in this runner.",
        "- Token totals: deterministic OpenClaw transcript estimates; compare as directional, not exact billing tokens.",
    ]
    has_any_diag = any(r.get("diagnostic_hints") for r in results)
    if has_any_diag:
        lines.append("- **Diagnostic hints: ENABLED** — this run used task-specific protocol assistance and is NOT a fair product comparison.")
    else:
        lines.append("- **Diagnostic hints: disabled** — fair product evaluation path.")
    lines += [
        "",
        "| # | task | mode | score | est tokens | assistant req | tools | SR preview/card/read | nanobot SR score/tokens/req | close? | notes |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for idx, task_id in enumerate(UNIFIED14_TASK_IDS, 1):
        modes = by_task.get(task_id, {})
        ref = reference_for(task_id, refs)
        for mode in ("native", "sr"):
            item = modes.get(mode)
            if not item:
                continue
            metrics = item.get("metrics", {})
            grade = item.get("grade", {})
            comp = comparisons.get(task_id, {}) if mode == "sr" else {}
            ref_cell = (
                f"{ref.get('sro_score', '')}/{ref.get('sro_tokens', '')}/{ref.get('sro_req', '')}"
                if ref
                else ""
            )
            lines.append(
                "| {idx} | {task} | {mode} | {score:.3f} | {tokens} | {req} | {tools} | {preview}/{card}/{read} | {ref} | {close} | {notes} |".format(
                    idx=idx,
                    task=task_id,
                    mode=mode,
                    score=float(grade.get("score") or 0.0),
                    tokens=metrics.get("estimated_total_tokens", 0),
                    req=metrics.get("assistant_messages", 0),
                    tools=metrics.get("tool_calls", 0),
                    preview=metrics.get("sro_preview_calls", 0),
                    card=metrics.get("sro_card_calls", 0),
                    read=metrics.get("sro_read_calls", 0),
                    ref=ref_cell,
                    close=("yes" if comp.get("close_or_better") else "no") if mode == "sr" else "",
                    notes=str(grade.get("notes") or ""),
                )
            )
    lines += [
        "",
        "Close means automated score is within 0.05 of the nanobot-SR reference and either estimated tokens are within +25% or assistant requests are within +2.",
    ]
    (run_root / "openclaw_unified14_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_modes(raw: str) -> list[str]:
    out = [item.strip() for item in raw.split(",") if item.strip()]
    bad = [item for item in out if item not in {"native", "sr"}]
    if bad:
        raise ValueError(f"unsupported modes: {bad}")
    return out or ["sr"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout-multiplier", type=float, default=1.0)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--modes", default="sr", help="Comma-separated: native,sr")
    parser.add_argument("--tasks", default="", help="Comma-separated task ids; default README unified14")
    parser.add_argument("--task-limit", type=int, default=0)
    parser.add_argument("--recompute", action="store_true")
    parser.add_argument(
        "--diagnostic-hints",
        action="store_true",
        default=False,
        help="Enable task-specific benchmark hint injection (protocol-assisted / diagnostic only; not for fair product comparison)",
    )
    args = parser.parse_args()

    task_ids = [item.strip() for item in args.tasks.split(",") if item.strip()] or list(UNIFIED14_TASK_IDS)
    if args.task_limit:
        task_ids = task_ids[: args.task_limit]
    modes = parse_modes(args.modes)
    tasks = [load_task(task_id) for task_id in task_ids]
    run_root = args.run_root or (REPO / "SRO_test" / "qwenclawbench" / f"openclaw_unified14_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)

    print("[openclaw-unified14] preflight: plugin runtime", flush=True)
    ensure_plugin_runtime()
    print("[openclaw-unified14] preflight: plugin inspect", flush=True)
    checked(["npx", "-y", "openclaw", "--profile", args.profile, "plugins", "inspect", "sparseread-openclaw", "--json"], timeout=120)
    print("[openclaw-unified14] preflight: ok", flush=True)

    results: list[dict[str, Any]] = []
    for task in tasks:
        for mode in modes:
            print(f"[openclaw-unified14] {task.task_id} mode={mode}", flush=True)
            try:
                case_result_path = run_root / task.task_id / mode / "case_result.json"
                if case_result_path.exists() and not args.recompute:
                    result = load_case(run_root, task, mode)
                else:
                    result = run_case(task, mode, run_root, args.profile, args.model, args.timeout_multiplier, diagnostic_hints=args.diagnostic_hints)
            except Exception as exc:
                result = {
                    "task_id": task.task_id,
                    "mode": mode,
                    "error": str(exc),
                    "metrics": {"returncode": 1},
                    "grade": {"score": 0.0, "scores": {}, "notes": str(exc)},
                }
            results.append(result)
            print(json.dumps({"task_id": task.task_id, "mode": mode, "score": result.get("grade", {}).get("score"), "metrics": result.get("metrics")}, ensure_ascii=False), flush=True)
            write_report(run_root, results)

    print(f"[openclaw-unified14] report: {run_root / 'openclaw_unified14_report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
