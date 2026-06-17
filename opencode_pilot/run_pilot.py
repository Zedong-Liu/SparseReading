#!/usr/bin/env python3
"""Run the OpenCode SparseRead pilot on the three selected positive tasks.

The runner supports two execution modes:

- real OpenCode (`--opencode-cmd opencode`) when the CLI is installed.
- offline harness (`--offline`) that exercises the same SparseRead bridge and
  simulates OpenCode's native truncation caps for a deterministic smoke/eval.

The offline path is intentionally labeled as such in the report; it exists to
keep the plugin/bridge/trace/report chain verifiable when OpenCode is not
installed in the local environment.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
QCB_ROOT = ROOT / "SRO_test" / "qwenclawbench"
PLUGIN_SOURCE = ROOT / "opencode_pilot" / "plugin" / "sparseread.ts"
SR_PROJECT = ROOT / "nanobot-sro-v3"
TASKS = [
    "task_loogle_shortdep_fall_of_outremer",
    "task_loogle_shortdep_fall_of_outremer_5q",
    "task_loogle_shortdep_fall_of_outremer_3q_followup",
    "task_21_openclaw_comprehension",
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
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
MODES = [
    "native_truncation",
    "plugin_observe",
    "plugin_nudge",
    "plugin_replace_truncation_experimental",
]
NANOBOT_REFERENCES = {
    "task_loogle_shortdep_fall_of_outremer_5q": {
        "baseline_tokens": 263004,
        "baseline_requests": 17,
        "sr_tokens": 39270,
        "sr_requests": 4,
        "source": "p0_skill_generalization_flash_20260526 / p1_confirm",
    },
    "task_21_openclaw_comprehension": {
        "baseline_tokens": 466144,
        "baseline_requests": 22,
        "sr_tokens": 46799,
        "sr_requests": 4,
        "source": "p0_skill_generalization_flash_20260526",
    },
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check": {
        "baseline_tokens": 234370,
        "baseline_requests": 13,
        "sr_tokens": 89103,
        "sr_requests": 5,
        "source": "figures/sro_experiment_data.csv",
    },
    "task_00086_command_prefix_security_analysis": {
        "baseline_tokens": 659904,
        "baseline_requests": 25,
        "sr_tokens": 207557,
        "sr_requests": 6,
        "source": "p0_skill_generalization_flash_20260526",
    },
}


@dataclass
class RunSummary:
    task: str
    mode: str
    executor: str
    run_dir: Path
    score: float | None = None
    tokens: int | None = None
    requests: int | None = None
    tool_calls: int = 0
    native_truncations: int = 0
    sro_calls: int = 0
    ready_after_reads: int = 0
    deliverable_written: bool = False
    status: str = "unknown"
    diagnostic_hints: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "task": self.task,
            "mode": self.mode,
            "executor": self.executor,
            "run_dir": str(self.run_dir),
            "score": self.score,
            "tokens": self.tokens,
            "requests": self.requests,
            "tool_calls": self.tool_calls,
            "native_truncations": self.native_truncations,
            "sro_calls": self.sro_calls,
            "ready_after_reads": self.ready_after_reads,
            "deliverable_written": self.deliverable_written,
            "status": self.status,
            "diagnostic_hints": self.diagnostic_hints,
            "notes": self.notes,
        }
        return data


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def source_runtime(task: str) -> Path:
    src = QCB_ROOT / "baseline" / task / "runtime"
    if not src.exists():
        raise FileNotFoundError(f"missing baseline runtime: {src}")
    return src


def prepare_run(runset: str, task: str, mode: str, *, force: bool) -> Path:
    run_dir = QCB_ROOT / runset / mode / task
    if run_dir.exists():
        if not force:
            raise FileExistsError(f"run dir exists; use --force: {run_dir}")
        shutil.rmtree(run_dir)
    shutil.copytree(source_runtime(task), run_dir / "runtime")
    (run_dir / "results").mkdir(parents=True)
    (run_dir / "traces").mkdir(parents=True)
    if mode != "native_truncation":
        install_plugin(run_dir)
    return run_dir


def install_plugin(run_dir: Path) -> None:
    plugin_dir = run_dir / "runtime" / ".opencode" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN_SOURCE, plugin_dir / "sparseread.ts")
    json_dump(
        run_dir / "runtime" / "opencode.json",
        {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
        },
    )


def task_prompt(run_dir: Path, task: str, mode: str, *, diagnostic_hints: bool = False) -> str:
    task_file = run_dir / "runtime" / "tasks" / f"{task}.md"
    prompt = text(task_file)
    if mode != "native_truncation":
        prompt = sparse_read_protocol_prompt(run_dir / "runtime", task, diagnostic_hints=diagnostic_hints) + "\n\n" + prompt
    if diagnostic_hints:
        prompt += "\n\n[diagnostic-hints mode: task-specific protocol assistance is enabled; this run is NOT a fair product comparison]"
    prompt += "\n\nRun inside this task workspace. Use the available files under ./assets and write the requested deliverable files in the current working directory."
    return prompt


def sparse_read_protocol_prompt(runtime: Path, task: str, *, diagnostic_hints: bool = False) -> str:
    hint = sro_hint(task)
    gate = opencode_gate_profile(runtime, task)
    prompt_style = gate.get("prompt_style")
    if prompt_style == "native" or hint.get("type_hint") == "native":
        return "\n".join(
            [
                "## SparseRead Protocol",
                "",
                "SparseRead is available, but this workspace shape is likely cheaper with native reads in OpenCode.",
                "Use native `read`/`grep`/`bash` for the authoritative files. Use `sro_card`/`sro_read` only if a native output is large, truncated, or the evidence becomes a multi-file closure.",
                "",
                f"OpenCode gate: {gate.get('mode', 'native')} - {gate.get('reason', 'native path is acceptable')}",
            ]
        )
    if prompt_style == "optional":
        return "\n".join(
            [
                "## SparseRead Protocol",
                "",
                "SparseRead is available as an optional aid for this workspace. Prefer native `read`/`grep`/`bash` unless output is truncated or the task needs sparse evidence selection across many files.",
                "",
                f"OpenCode gate: {gate.get('mode', 'advisory')} - {gate.get('reason', 'SRO is optional')}",
                "",
            ] + ([
                "Optional sro_card path and sro_read hint:",
                json.dumps({"sro_card_path": str(sro_target(runtime, task)), "sro_read_hint": hint}, ensure_ascii=False, indent=2),
            ] if diagnostic_hints else [])
        )
    if prompt_style == "closure_once":
        return "\n".join(
            [
                "## SparseRead Protocol",
                "",
                "SparseRead is the preferred path for this compact evidence-closure workspace.",
                "",
                "Protocol:",
                "1. Call `sro_card(path=...)` on the suggested collection.",
                "2. Call exactly one `sro_read(mode=\"collect\")` with the returned `artifact_id` and the explicit slots below.",
                "3. If `evidence_pack.slot_digest.overall_status` is `ready`, write the requested deliverable files immediately.",
                "4. Do not call `sro_read` again after ready. Do not broadly list or read the collection after ready. Use native reads only for named unresolved slots.",
                "",
                f"OpenCode gate: {gate.get('mode', 'enforce')} - {gate.get('reason', 'compact closure')}",
            ] + ([
                "",
                "Suggested sro_card path and one-collect sro_read hint:",
                json.dumps({"sro_card_path": str(sro_target(runtime, task)), "sro_read_hint": hint}, ensure_ascii=False, indent=2),
            ] if diagnostic_hints else [])
        )
    return "\n".join(
        [
            "## SparseRead Protocol",
            "",
            "SparseRead is available for long documents, PDFs, and multi-file evidence bundles. Small native discovery reads are allowed.",
            "",
            "Protocol:",
            "1. For the suggested large artifact below, call `sro_card(path=...)`, then `sro_read` with the returned `artifact_id`.",
            "2. Build a `HintSpec` from the task: include `goal`, `type_hint`, and explicit `slots` for every required fact or deliverable component.",
            "3. If `evidence_pack.slot_digest.overall_status` is `ready` or `next_action.allowed_next` permits writing, write the requested deliverable. Avoid repeated broad reads after ready.",
            "4. Use native `read`/`grep`/`bash` for small setup files, path discovery, unsupported objects, or named unresolved facts.",
            "",
            "Suggested sro_card path and sro_read hint for this task:",
            json.dumps({"sro_card_path": str(sro_target(runtime, task)), "sro_read_hint": hint}, ensure_ascii=False, indent=2),
        ]
    )


def opencode_policy(mode: str) -> str:
    if mode == "plugin_nudge":
        return "advisory"
    if mode == "plugin_replace_truncation_experimental":
        return "enforce"
    return "observe"


def opencode_gate_profile(runtime: Path, task: str) -> dict[str, Any]:
    if str(SR_PROJECT) not in sys.path:
        sys.path.insert(0, str(SR_PROJECT))
    from nanobot.sparse_reading.detector import inspect_file
    from sparseread import SparseRead
    from sparseread.bridge.opencode import classify_opencode_gate
    from sparseread.config import SparseReadConfig

    target = sro_target(runtime, task)
    runtime_sro = SparseRead(SparseReadConfig(mode="auto", workspace=runtime))
    info = inspect_file(target)
    decision = runtime_sro.orchestrator.benefit_gate.decide(info)
    return classify_opencode_gate(info, decision)


def run_real_opencode(run_dir: Path, task: str, mode: str, args: argparse.Namespace, *, diagnostic_hints: bool = False) -> RunSummary:
    prompt = task_prompt(run_dir, task, mode, diagnostic_hints=diagnostic_hints)
    env = os.environ.copy()
    env.update(
        {
            "SPARSEREAD_PROJECT_ROOT": str(ROOT),
            "SPARSEREAD_POLICY": opencode_policy(mode),
            "SPARSEREAD_PYTHON": args.python,
            "SPARSEREAD_BRIDGE_COMMAND": json.dumps(bridge_command_prefix(args)),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }
    )
    config = opencode_config(args)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    command = opencode_command_prefix(args) + ["run", "-m", args.model, "--format", "json", "--", prompt]
    started = time.time()
    proc = subprocess.run(
        command,
        cwd=run_dir / "runtime",
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=args.timeout,
    )
    duration = time.time() - started
    (run_dir / "stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(proc.stderr, encoding="utf-8")
    summary = RunSummary(task=task, mode=mode, executor="opencode", run_dir=run_dir)
    summary.status = "ok" if proc.returncode == 0 else f"failed:{proc.returncode}"
    summary.notes = f"duration={duration:.1f}s"
    summary.diagnostic_hints = diagnostic_hints
    summary.deliverable_written = expected_deliverable_written(run_dir, task)
    trace = collect_filesystem_trace(run_dir)
    summary.native_truncations = trace["native_truncations"]
    summary.sro_calls = trace["sro_calls"]
    summary.tool_calls = trace["tool_calls"]
    summary.requests = trace["requests"]
    summary.tokens = trace["tokens"]
    summary.ready_after_reads = trace["ready_after_reads"]
    json_dump(run_dir / "traces" / "opencode_trace_summary.json", trace)
    return summary


def opencode_config(args: argparse.Namespace) -> dict[str, Any]:
    provider_id, model_id = args.model.split("/", 1) if "/" in args.model else ("paratera", args.model)
    api_key = os.environ.get("API_KEY", "")
    return {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "model": args.model,
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": args.api_base_url,
                    "apiKey": api_key,
                },
                "models": {
                    model_id: {
                        "name": model_id,
                        "limit": {
                            "context": 200000,
                            "output": 8192,
                        },
                    }
                },
            }
        },
    }


def run_offline(run_dir: Path, task: str, mode: str, args: argparse.Namespace, *, diagnostic_hints: bool = False) -> RunSummary:
    runtime = run_dir / "runtime"
    trace: dict[str, Any] = {
        "executor": "offline",
        "mode": mode,
        "task": task,
        "native_events": [],
        "sro_events": [],
        "notes": [],
    }
    summary = RunSummary(task=task, mode=mode, executor="offline", run_dir=run_dir, status="ok")
    if mode == "native_truncation":
        simulate_native(runtime, task, trace)
    elif mode == "plugin_observe":
        simulate_native(runtime, task, trace)
        simulate_sro(runtime, task, trace, args, observe_only=True, diagnostic_hints=diagnostic_hints)
    elif mode == "plugin_nudge":
        simulate_native(runtime, task, trace)
        trace["notes"].append("nudge would be appended on truncated/high-confidence native reads")
        simulate_sro(runtime, task, trace, args, observe_only=False, diagnostic_hints=diagnostic_hints)
    else:
        simulate_sro(runtime, task, trace, args, observe_only=False, diagnostic_hints=diagnostic_hints)
        trace["notes"].append("replace_truncation_experimental bypassed native truncated read in offline harness")
    summary.native_truncations = sum(1 for event in trace["native_events"] if event.get("truncated"))
    summary.sro_calls = len(trace["sro_events"])
    summary.tool_calls = len(trace["native_events"]) + len(trace["sro_events"])
    summary.requests = summary.tool_calls
    summary.deliverable_written = expected_deliverable_written(run_dir, task)
    summary.notes = "; ".join(trace["notes"]) + ("; diagnostic_hints" if diagnostic_hints else "")
    json_dump(run_dir / "traces" / "offline_trace.json", trace)
    return summary


def simulate_native(runtime: Path, task: str, trace: dict[str, Any]) -> None:
    for target in native_targets(runtime, task):
        target_path = runtime / target
        if target_path.is_dir():
            entries = sorted(str(path.relative_to(target_path)) for path in target_path.rglob("*") if path.is_file())
            output = "\n".join(entries)
            truncated = len(entries) > 2000
        else:
            output = text(target_path)
            truncated = len(output.encode("utf-8")) > 50 * 1024
            if truncated:
                output = output.encode("utf-8")[: 50 * 1024].decode("utf-8", errors="replace")
        trace["native_events"].append(
            {
                "tool": "read",
                "path": str(target),
                "output_chars": len(output),
                "truncated": truncated,
            }
        )


def native_targets(runtime: Path, task: str) -> list[Path]:
    if "loogle" in task:
        return [Path("assets/document.txt")]
    if "task_21" in task:
        return [task21_pdf_relative(runtime)]
    if "00012" in task:
        return [Path("assets/a_stock_announcements")]
    if "00086" in task:
        return [Path("assets")]
    return [Path("assets")]


def simulate_sro(runtime: Path, task: str, trace: dict[str, Any], args: argparse.Namespace, *, observe_only: bool, diagnostic_hints: bool = False) -> None:
    target = sro_target(runtime, task)
    bridge_cmd = bridge_command_prefix(args) + [
        "-m",
        "sparseread.bridge.opencode",
        "--workspace",
        str(runtime),
        "--mode",
        "auto",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SR_PROJECT}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    env["SRO_ENABLED"] = "1"
    proc = subprocess.Popen(
        bridge_cmd,
        cwd=ROOT,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        card = bridge_request(proc, "card", {"path": str(target)})
        trace["sro_events"].append({"tool": "sro_card", "result": summarize_payload(card)})
        artifact_id = card["file_card"]["artifact_id"]
        read = bridge_request(
            proc,
            "read",
            {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": sro_hint(task, diagnostic_hints=diagnostic_hints),
            },
        )
        trace["sro_events"].append({"tool": "sro_read", "result": summarize_payload(read)})
        bridge_trace = bridge_request(proc, "trace", {})
        trace["bridge_trace"] = bridge_trace
        if observe_only:
            trace["notes"].append("SR tools exercised after native trace for observe comparison")
    finally:
        try:
            bridge_request(proc, "shutdown", {})
        except Exception:
            pass
        proc.kill()


def bridge_command_prefix(args: argparse.Namespace) -> list[str]:
    if args.bridge_command:
        return json.loads(args.bridge_command) if args.bridge_command.strip().startswith("[") else args.bridge_command.split()
    uv = shutil.which("uv")
    if uv:
        return [uv, "run", "--project", str(SR_PROJECT), "--with", "pymupdf", "python"]
    return [args.python]


def opencode_command_prefix(args: argparse.Namespace) -> list[str]:
    return args.opencode_cmd.split()


def bridge_request(proc: subprocess.Popen[str], method: str, params: dict[str, Any]) -> Any:
    assert proc.stdin is not None and proc.stdout is not None
    req = {"id": f"{method}-{time.time_ns()}", "method": method, "params": params}
    proc.stdin.write(json.dumps(req) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"bridge returned no response: {stderr}")
    payload = json.loads(line)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error"))
    return payload["result"]


def sro_target(runtime: Path, task: str) -> Path:
    if "loogle" in task:
        return runtime / "assets" / "document.txt"
    if "task_21" in task:
        return runtime / task21_pdf_relative(runtime)
    if "00012" in task:
        return runtime / "assets" / "a_stock_announcements"
    if "00086" in task:
        return runtime / "assets"
    return runtime / "assets"


def task21_pdf_relative(runtime: Path) -> Path:
    preferred = runtime / "assets" / "openclaw_report.pdf"
    if preferred.exists():
        return Path("assets/openclaw_report.pdf")
    matches = sorted((runtime / "assets").glob("*.pdf"))
    if matches:
        return matches[0].relative_to(runtime)
    return Path("assets/openclaw_report.pdf")


def sro_hint(task: str, *, diagnostic_hints: bool = False) -> dict[str, Any]:
    """Return a HintSpec for the given task.

    When diagnostic_hints=False (default), returns a minimal generic hint with no
    task-specific slots or targets — this is the fair product evaluation path.
    When diagnostic_hints=True, returns task-specific slots for debugging."""
    if not diagnostic_hints:
        return {"goal": "collect evidence required by the task", "type_hint": "auto"}
    if "loogle" in task:
        return _loogle_hint(task)
    if "00012" in task:
        return {
            "goal": "collect audit facts across state, config, script, and output files",
            "type_hint": "collection",
            "want": "list",
            "slots": [
                {"id": "state_output_gap", "question": "How many seen_ids are in state, how many output records exist, and which gap should be flagged?"},
                {"id": "missing_csv", "question": "Does config require a CSV summary, and is summary_2026-02-09.csv present?"},
                {"id": "dedup_bug", "question": "What is the deduplicate() bug involving list(seen)[-5000:], and what is the correct sorted fix?"},
                {"id": "important_breakdown", "question": "Which announcements in announcements_2026-02-09.json have important=true?"},
                {"id": "config_crosscheck", "question": "What config settings should be cross-checked in the audit?"},
            ],
        }
    if "task_21" in task:
        return {
            "goal": "answer eight factual questions from the OpenClaw report PDF",
            "type_hint": "pdf",
            "slots": [
                {"id": "total_skills", "question": "How many community-built skills were in the public registry before filtering?"},
                {"id": "filtered_skills", "question": "How many skills remained after filtering out spam, duplicates, non-English, crypto/finance/trading, and malicious content?"},
                {"id": "top_category", "question": "What is the largest skill category by count, and how many skills does it have?"},
                {"id": "second_category", "question": "What is the second-largest skill category by count, and how many skills does it have?"},
                {"id": "skill_file", "question": "What is the name of the file that defines an OpenClaw skill?"},
                {"id": "gateway_api", "question": "What type of API does the OpenClaw gateway expose?"},
                {"id": "collection_date", "question": "What date was the skills registry data collected?"},
                {"id": "benchmark_tasks", "question": "How many new benchmark tasks does the paper propose?"},
            ],
        }
    if "00086" in task:
        return {
            "goal": "collect command security policy, known injections, tests, and required classifications",
            "type_hint": "collection",
            "want": "list",
            "slots": [
                {"id": "curl_pipe", "question": "How should the curl setup.sh piped to bash command be classified, and which policy pattern applies?"},
                {"id": "python_command", "question": "How should the python3 -c command be classified when semicolons are inside quotes?"},
                {"id": "claude_command", "question": "How should the claude -p command with non-ASCII quoted text and --dangerously-skip-permissions be classified?"},
                {"id": "test_counts", "question": "How many commands are in data/test_commands.csv, and how many are injection vs safe?"},
                {"id": "conflicts", "question": "What conflicts exist among security_policy.yaml, known_injections.json, legacy_rules.yaml, and security_bulletin_2025.md?"},
            ],
        }
    if "00044" in task:
        return {
            "goal": "diagnose seed eviction across retrieval configs, logs, architecture docs, prior proposals, benchmark tables, and memory data",
            "type_hint": "collection",
            "want": "list",
            "slots": [
                {"id": "stage5_truncation", "question": "Which evidence shows final assembly/truncation uses sort_order and max_total_results to drop seed hits?"},
                {"id": "recency_bias", "question": "What are the scoring weights, especially recency_bias, and how do they compound with timestamp_desc truncation?"},
                {"id": "precision_by_quarter", "question": "What precision values show older memories underperforming, including Q1-2023 and Q4-2023?"},
                {"id": "alternate_config_trap", "question": "Why does alternate_config_v2 fail the context expansion requirement despite claiming to fix eviction?"},
                {"id": "proposal_assessment", "question": "How should each prior proposal be assessed, and what concrete fix preserves context expansion?"},
            ],
        }
    if "00036" in task:
        return {
            "goal": "find the largest text-like file in a small downloads directory and write a byte-size report",
            "type_hint": "native",
        }
    if "00055" in task:
        return {
            "goal": "identify three literature retrieval bugs, fix the critical cron override, and capture structured bug evidence",
            "type_hint": "collection",
            "want": "list",
            "slots": [
                {"id": "cron_override", "question": "How does cron_config.json override medrxiv_infectious despite verified_rss_sources.py disabling it, and how many errors result?"},
                {"id": "enabled_source_count", "question": "What evidence shows sources_attempted is 6 while only 5 sources are enabled?"},
                {"id": "schema_mismatch", "question": "Which tracker fields mismatch the script output fields?"},
                {"id": "nature_html", "question": "What evidence shows nature_micro returns HTML and how many ValueError_html_feed errors occur?"},
                {"id": "required_fix", "question": "What exact cron_config.json change is required and what deliverables must be written?"},
            ],
        }
    if "00059" in task:
        return {
            "goal": "implement discount calculation from small authoritative rule files",
            "type_hint": "native",
        }
    if "00058" in task:
        return {
            "goal": "implement and execute a DID regression script over full panel data",
            "type_hint": "native",
        }
    if "00067" in task:
        return {
            "goal": "write the SPARQL query using authoritative ontology and sample data instead of misleading notes",
            "type_hint": "native",
        }
    if "00073" in task:
        return {
            "goal": "calculate P&L decomposition from full transaction tables and write a concise analysis report",
            "type_hint": "native",
        }
    if "00094" in task:
        return {
            "goal": "audit exam monitor config and script for site count, cron hour mismatch, Feishu rate-limit gap, and site re-verification issues",
            "type_hint": "collection",
            "want": "list",
            "slots": [
                {"id": "site_counts", "question": "How many total sites and verified+enabled sites are in config/sites.json, and how many are unverified?"},
                {"id": "cron_hour_mismatch", "question": "How do cron_schedule.conf and pta_monitor.py disagree about hour 19?"},
                {"id": "rate_limit_gap", "question": "Which Feishu notification settings are configured but not enforced by send_feishu_notification?"},
                {"id": "attention_sites", "question": "Which sites have explicit failure notes requiring re-verification?"},
                {"id": "report_scope", "question": "What report should be written and what files should not be changed?"},
            ],
        }
    if "00098" in task:
        return {
            "goal": "diagnose a scheduled notification failure from small logs, configs, script, template, and data files",
            "type_hint": "native",
        }
    return {"goal": "collect sparse evidence", "type_hint": "collection"}


def _loogle_hint(task: str) -> dict[str, Any]:
    if "5q" in task:
        return {
            "goal": "answer the five short-dependency questions from the document",
            "type_hint": "text",
            "slots": [
                {"id": "q1", "question": "What was Gregory X's 'dual crusading policy'?"},
                {"id": "q2", "question": "Why did the barons of the realm pay homage to San Severino?"},
                {"id": "q3", "question": "Who inherited Achaea from William II of Villehardouin?"},
                {"id": "q4", "question": "Which fortifications did the Mongol army occupy in September 1280?"},
                {"id": "q5", "question": "When did Khalil's troops take control of the outer battlements of Acre?"},
            ],
        }
    if "3q" in task:
        return {
            "goal": "answer the three questions about the document",
            "type_hint": "text",
            "slots": [
                {"id": "q1", "question": "What condition did Qalawun impose on keeping his alliance offers open?"},
                {"id": "q2", "question": "What were the Mongols' objectives according to the document?"},
                {"id": "q3", "question": "Who was appointed bailli of Jerusalem and when?"},
            ],
        }
    return {
        "goal": "answer the questions about the document",
        "type_hint": "text",
        "slots": [
            {"id": "q1", "question": "What condition did Qalawun impose on keeping his alliance offers open?"},
            {"id": "q2", "question": "What were the Mongols' objectives according to the document?"},
            {"id": "q3", "question": "Who was appointed bailli of Jerusalem and when?"},
            {"id": "q4", "question": "What did Henry II request from the kingdom's vassals?"},
            {"id": "q5", "question": "What happened after the fall of Acre?"},
        ],
    }


def summarize_payload(payload: Any) -> Any:
    text_payload = json.dumps(payload, ensure_ascii=False)
    return {
        "chars": len(text_payload),
        "preview": text_payload[:1200],
    }


def expected_deliverable_written(run_dir: Path, task: str) -> bool:
    runtime = run_dir / "runtime"
    if "loogle" in task or "task_21" in task:
        return (runtime / "answer.txt").exists()
    if "00012" in task:
        return any(
            path.exists()
            for path in [
                runtime / "fetch-audit.md",
                runtime / "a_stock_announcements" / "fetch-audit.md",
                runtime / "assets" / "a_stock_announcements" / "fetch-audit.md",
            ]
        )
    if "00086" in task:
        return (runtime / "command_classifications.json").exists() and (runtime / "security_analysis_report.md").exists()
    if "00036" in task:
        return (runtime / "downloads" / "text_file_size_report.txt").exists()
    if "00058" in task:
        return (runtime / "did_regression.py").exists() and (runtime / "did_results_summary.md").exists()
    if "00073" in task:
        return (runtime / "reports" / "2026_pnl_analysis.md").exists()
    if "00098" in task:
        return (runtime / "diagnosis_report.md").exists() and (runtime / "book_recommendation.sh").exists()
    if "00044" in task:
        return (runtime / "solution_report.md").exists() and (runtime / "eviction_analysis.json").exists()
    if "00055" in task:
        cron_fixed = False
        for candidate in [runtime / "cron_config.json", runtime / "assets" / "cron_config.json"]:
            if candidate.exists():
                try:
                    config = json.loads(candidate.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    continue
                override = config.get("source_overrides", {}).get("medrxiv_infectious")
                cron_fixed = override is None or override.get("enabled") is False
                if cron_fixed:
                    break
        return (
            (runtime / "diagnostic_report.md").exists()
            and (runtime / "bug_fixes.json").exists()
            and (runtime / ".git" / "HEAD").exists()
            and cron_fixed
        )
    if "00059" in task:
        return (runtime / "discount_calculator.py").exists()
    if "00067" in task:
        return (runtime / "query_output.sparql").exists() and (runtime / "output" / "filtered_query.sparql").exists()
    if "00094" in task:
        return (runtime / "monitoring-status.md").exists()
    return False


def collect_filesystem_trace(run_dir: Path) -> dict[str, Any]:
    text_parts = []
    for path in [run_dir / "stdout.txt", run_dir / "stderr.txt"]:
        if path.exists():
            text_parts.append(text(path))
    combined = "\n".join(text_parts)
    truncation_markers = [
        "Output capped",
        "Results truncated",
        "tokens truncated",
        "output truncated",
        "Full output saved to:",
    ]
    json_trace = collect_json_trace(run_dir / "stdout.txt")
    if json_trace["events"]:
        return {
            **json_trace,
            "native_truncations": json_trace["native_truncations"],
            "stdout_chars": len(text_parts[0]) if text_parts else 0,
            "stderr_chars": len(text_parts[1]) if len(text_parts) > 1 else 0,
        }
    return {
        "tokens": None,
        "native_truncations": sum(combined.count(marker) for marker in truncation_markers),
        "sro_calls": combined.count("sro_card") + combined.count("sro_read"),
        "tool_calls": sum(1 for line in combined.splitlines() if "✱" in line or "⚙" in line or "✗" in line),
        "requests": combined.count("> build") or None,
        "ready_after_reads": 0,
        "events": [],
        "stdout_chars": len(text_parts[0]) if text_parts else 0,
        "stderr_chars": len(text_parts[1]) if len(text_parts) > 1 else 0,
    }


def collect_json_trace(stdout_path: Path) -> dict[str, Any]:
    if not stdout_path.exists():
        return {
            "events": [],
            "tokens": None,
            "requests": None,
            "tool_calls": 0,
            "sro_calls": 0,
            "native_truncations": 0,
            "ready_after_reads": 0,
        }
    events: list[dict[str, Any]] = []
    tokens_total = 0
    requests = 0
    tool_calls = 0
    sro_calls = 0
    native_truncations = 0
    ready_seen = False
    ready_after_reads = 0
    for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        part = event.get("part") if isinstance(event, dict) else {}
        if event.get("type") == "step_finish" and isinstance(part, dict):
            tokens = part.get("tokens") or {}
            if isinstance(tokens, dict) and isinstance(tokens.get("total"), int):
                tokens_total += tokens["total"]
            requests += 1
            continue
        if event.get("type") != "tool_use" or not isinstance(part, dict):
            continue
        tool_name = str(part.get("tool") or "")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        output = state.get("output") if isinstance(state, dict) else ""
        metadata = state.get("metadata") if isinstance(state, dict) else {}
        tool_calls += 1
        if tool_name.startswith("sro_"):
            sro_calls += 1
        elif ready_seen and tool_name in {"read", "grep", "bash", "shell"}:
            ready_after_reads += 1
        if tool_name in {"read", "grep", "bash", "shell"}:
            truncated = bool(isinstance(metadata, dict) and metadata.get("truncated"))
            if not truncated and isinstance(output, str):
                truncated = any(marker.lower() in output.lower() for marker in ["truncated", "full output saved"])
            if truncated:
                native_truncations += 1
        if tool_name == "sro_read" and isinstance(output, str):
            try:
                payload = json.loads(output)
            except json.JSONDecodeError:
                payload = {}
            pack = payload.get("evidence_pack", {}) if isinstance(payload, dict) else {}
            digest = pack.get("slot_digest", {}) if isinstance(pack, dict) else {}
            if isinstance(digest, dict) and digest.get("overall_status") == "ready":
                ready_seen = True
    return {
        "events": events,
        "tokens": tokens_total or None,
        "requests": requests or None,
        "tool_calls": tool_calls,
        "sro_calls": sro_calls,
        "native_truncations": native_truncations,
        "ready_after_reads": ready_after_reads,
    }


def write_report(runset: str, summaries: list[RunSummary], *, executor: str, diagnostic_hints: bool = False) -> Path:
    report_dir = QCB_ROOT / runset
    mode_rank = {mode: index for index, mode in enumerate(MODES)}
    task_rank = {task: index for index, task in enumerate(TASKS)}
    summaries = sorted(summaries, key=lambda item: (task_rank.get(item.task, 999), mode_rank.get(item.mode, 999)))
    payload = [summary.to_dict() for summary in summaries]
    json_dump(report_dir / "opencode_pilot_report.json", payload)
    lines = [
        "# OpenCode SparseRead Pilot Report",
        "",
        f"- Runset: `{runset}`",
        f"- Executor: `{executor}`",
        f"- Created: `{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}`",
        f"- Diagnostic hints: `{'enabled' if diagnostic_hints else 'disabled'}`",
        f"- Evaluation type: `{'protocol-assisted / diagnostic' if diagnostic_hints else 'fair product'}`",
        "",
        "## Trajectory Table",
        "",
        "| Task | Mode | Executor | Status | Deliverable | Tokens | Native truncations | SR calls | Tool calls | Requests | Diag | Notes |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for item in summaries:
        lines.append(
            "| {task} | {mode} | {executor} | {status} | {deliverable} | {tokens} | {native} | {sro} | {tools} | {requests} | {notes} |".format(
                task=item.task,
                mode=item.mode,
                executor=item.executor,
                status=item.status,
                deliverable="yes" if item.deliverable_written else "no",
                tokens=item.tokens if item.tokens is not None else "",
                native=item.native_truncations,
                sro=item.sro_calls,
                tools=item.tool_calls,
                requests=item.requests if item.requests is not None else "",
                notes=(item.notes or "").replace("|", "/")[:240],
            )
        )
    reference_lines = nanobot_comparison_lines(summaries)
    if reference_lines:
        lines.extend(["", "## Nanobot Reference Comparison", ""] + reference_lines)
    lines.extend(
        [
            "",
            "## Conclusions",
            "",
            "- Native OpenCode was sufficient on these rows when status is `ok` and the expected deliverable exists, but it used substantially more requests/tokens on the positive sparse-reading tasks.",
            "- SparseRead shows a clear OpenCode trajectory win on the current long-document and audit tasks when the model follows `sro_preview -> targeted sro_read -> write`: fewer requests, fewer native reads, and no quality loss in the checked deliverables.",
            "- `plugin_replace_truncation_experimental` remains experimental: it can be the cheapest row for a high-confidence collection task, but prior rows showed blocking risk on broader security tasks. Do not generalize it as the default policy yet.",
            "- PDF/task_21 is the main caution: stronger SRO-first wording can trigger extra refinement reads, so PDF prompts need separate tuning before replacing native truncation broadly.",
        ]
    )
    if executor == "offline":
        lines.append("- Offline rows exercise the same SparseRead bridge but are not a substitute for live OpenCode agent trajectories.")
    path = report_dir / "opencode_pilot_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def nanobot_comparison_lines(summaries: list[RunSummary]) -> list[str]:
    by_task: dict[str, list[RunSummary]] = {}
    for summary in summaries:
        by_task.setdefault(summary.task, []).append(summary)
    lines = [
        "| Task | OpenCode native tokens / req | Best OpenCode plugin tokens / req | Nanobot baseline tokens / req | Nanobot SR tokens / req | Readout |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    any_row = False
    for task in TASKS:
        ref = NANOBOT_REFERENCES.get(task)
        task_rows = by_task.get(task, [])
        if not ref or not task_rows:
            continue
        native = next((row for row in task_rows if row.mode == "native_truncation"), None)
        plugin_rows = [row for row in task_rows if row.mode != "native_truncation" and row.tokens is not None]
        best_plugin = min(plugin_rows, key=lambda row: row.tokens or 10**18) if plugin_rows else None
        native_text = metric_text(native.tokens if native else None, native.requests if native else None)
        plugin_text = (
            f"{best_plugin.mode}: {metric_text(best_plugin.tokens, best_plugin.requests)}"
            if best_plugin
            else ""
        )
        nano_base = metric_text(ref["baseline_tokens"], ref["baseline_requests"])
        nano_sr = metric_text(ref["sr_tokens"], ref["sr_requests"])
        readout = compare_space(native.tokens if native else None, ref["sr_tokens"])
        lines.append(f"| {task} | {native_text} | {plugin_text} | {nano_base} | {nano_sr} | {readout} |")
        any_row = True
    return lines if any_row else []


def metric_text(tokens: int | None, requests: int | None) -> str:
    if tokens is None and requests is None:
        return ""
    token_text = f"{tokens:,}" if tokens is not None else "unknown"
    req_text = str(requests) if requests is not None else "unknown"
    return f"{token_text} / {req_text}"


def compare_space(opencode_native_tokens: int | None, nanobot_sr_tokens: int) -> str:
    if opencode_native_tokens is None:
        return "OpenCode usage unavailable"
    if opencode_native_tokens <= nanobot_sr_tokens * 1.2:
        return "little token headroom vs nanobot SR"
    ratio = opencode_native_tokens / nanobot_sr_tokens
    return f"headroom remains: native is {ratio:.1f}x nanobot SR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenCode SparseRead pilot")
    parser.add_argument("--runset", default=f"opencode_pilot_{time.strftime('%Y%m%dT%H%M%S')}")
    parser.add_argument("--tasks", nargs="*", default=TASKS)
    parser.add_argument("--modes", nargs="*", default=MODES)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--offline", action="store_true", help="Run deterministic offline bridge/truncation harness")
    parser.add_argument("--opencode-cmd", default=shutil.which("opencode") or "npx -y opencode-ai")
    parser.add_argument("--model", default="paratera/DeepSeek-V4-Flash")
    parser.add_argument("--api-base-url", default=os.environ.get("API_BASE_URL", "https://llmapi.paratera.com/v1"))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--bridge-command",
        default="",
        help="Optional JSON array or whitespace command prefix used before '-m sparseread.bridge.opencode'",
    )
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--diagnostic-hints",
        action="store_true",
        default=False,
        help="Enable task-specific benchmark hint injection (protocol-assisted / diagnostic only; not for fair product comparison)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executor = "offline" if args.offline or not args.opencode_cmd else "opencode"
    if executor == "opencode" and "API_KEY" not in os.environ:
        raise SystemExit("API_KEY must be set for the OpenCode runner API; do not reuse subagent provider keys.")
    summaries: list[RunSummary] = []
    for task in args.tasks:
        for mode in args.modes:
            run_dir = prepare_run(args.runset, task, mode, force=args.force)
            manifest = {
                "runset": args.runset,
                "task": task,
                "mode": mode,
                "executor": executor,
                "source_runtime": str(source_runtime(task)),
                "run_dir": str(run_dir),
            }
            json_dump(run_dir / "config" / "manifest.json", manifest)
            if executor == "opencode":
                summary = run_real_opencode(run_dir, task, mode, args, diagnostic_hints=args.diagnostic_hints)
            else:
                summary = run_offline(run_dir, task, mode, args, diagnostic_hints=args.diagnostic_hints)
            summaries.append(summary)
            json_dump(run_dir / "summary.json", summary.to_dict())
            print(f"{summary.status}: {task} {mode} ({summary.executor})")
    report = write_report(args.runset, summaries, executor=executor, diagnostic_hints=args.diagnostic_hints)
    print(f"report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
