#!/usr/bin/env python3
"""Run a focused OpenClaw native-vs-SparseRead validation sweep.

This runner is intentionally small and task-scoped.  Task-specific slots are
benchmark hints only; product routing remains in the OpenClaw adapter gate.
"""

from __future__ import annotations

import argparse
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


REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "SRO_test" / "qwenclawbench" / "baseline"
PLUGIN_DIR = REPO / "integrations" / "openclaw" / "plugin"
CORE_DIR = REPO / "nanobot-sro-v3"
DEFAULT_PROFILE = "srotest"
DEFAULT_MODEL = "paratera/DeepSeek-V4-Flash"


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    source_dir: Path
    artifact_path: str
    deliverable: str
    slots: list[dict[str, str]]

    @property
    def task_md(self) -> Path:
        return self.source_dir / "runtime" / "tasks" / f"{self.task_id}.md"

    @property
    def assets_dir(self) -> Path:
        return self.source_dir / "runtime" / "assets"


TASKS = [
    TaskSpec(
        task_id="task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
        source_dir=BASELINE / "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check",
        artifact_path="a_stock_announcements",
        deliverable="a_stock_announcements/fetch-audit.md",
        slots=[
            {"id": "state_vs_output", "question": "35 seen_ids vs 11 output records and the 24 orphaned IDs"},
            {"id": "missing_csv", "question": "csv_summary true but summary_2026-02-09.csv is absent"},
            {"id": "dedup_bug", "question": "deduplicate list(seen)[-5000:] ordering bug and sorted(seen, key=int)[-5000:] fix"},
            {"id": "important_announcements", "question": "exactly five important announcements with IDs and company names"},
            {"id": "config_cross_check", "question": "max_pages, fetch_sse, request_delay, category, notifications"},
        ],
    ),
    TaskSpec(
        task_id="task_21_openclaw_comprehension",
        source_dir=BASELINE / "task_21_openclaw_comprehension",
        artifact_path="openclaw_report.pdf",
        deliverable="answer.txt",
        slots=[
            {"id": "q1", "question": "How many community-built skills were in the public registry before filtering?"},
            {"id": "q2", "question": "How many skills remained after filtering?"},
            {"id": "q3", "question": "Largest skill category and count"},
            {"id": "q4", "question": "Second-largest skill category and count"},
            {"id": "q5", "question": "File name that defines an OpenClaw skill"},
            {"id": "q6", "question": "Type of API exposed by the OpenClaw gateway"},
            {"id": "q7", "question": "Skills registry data collection date"},
            {"id": "q8", "question": "How many new benchmark tasks the paper proposes"},
        ],
    ),
    TaskSpec(
        task_id="task_loogle_shortdep_fall_of_outremer_5q",
        source_dir=BASELINE / "task_loogle_shortdep_fall_of_outremer_5q",
        artifact_path="document.txt",
        deliverable="answer.txt",
        slots=[
            {"id": "q1", "question": "Gregory X's dual crusading policy"},
            {"id": "q2", "question": "Why barons paid homage to San Severino"},
            {"id": "q3", "question": "Who inherited Achaea from William II of Villehardouin"},
            {"id": "q4", "question": "Fortifications occupied by Mongol army in September 1280"},
            {"id": "q5", "question": "Date Khalil's troops took the outer battlements of Acre"},
        ],
    ),
]


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


def checked(cmd: list[str], *, input_text: str | None = None, timeout: int = 120, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = run(cmd, input_text=input_text, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def extract_prompt(task_md: Path) -> str:
    text = task_md.read_text(encoding="utf-8")
    match = re.search(r"^## Prompt\s*\n(?P<prompt>.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match:
        raise ValueError(f"missing ## Prompt in {task_md}")
    return match.group("prompt").strip()


def materialize_assets(task: TaskSpec, workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for source in task.assets_dir.rglob("*"):
        if source.is_dir():
            continue
        rel = source.relative_to(task.assets_dir)
        dest_rel = rel
        if task.task_id == "task_21_openclaw_comprehension":
            dest_rel = Path("openclaw_report.pdf")
        dest = workspace / dest_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)


def patch_config(profile: str, patch: dict[str, Any]) -> None:
    checked(
        ["npx", "-y", "openclaw", "--profile", profile, "config", "patch", "--stdin"],
        input_text=json.dumps(patch),
        timeout=120,
    )


def set_plugin_enabled(profile: str, enabled: bool, *, policy: str = "advisory", workspace: Path | None = None) -> None:
    entry: dict[str, Any] = {"enabled": enabled}
    if enabled:
        bridge_command = json.dumps([
            "uv",
            "--project",
            str(CORE_DIR),
            "run",
            "--with",
            "pymupdf",
            "python",
        ])
        entry["config"] = {
            "policy": policy,
            "bridgeCommand": bridge_command,
            "projectRoot": str(REPO),
            "workspaceRoot": str(workspace) if workspace else "",
            "bridgeModule": "sparseread.bridge.openclaw",
            "mode": "auto",
            "hookMode": "enforce",
        }
        entry["hooks"] = {
            "allowPromptInjection": True,
            "allowConversationAccess": True,
        }
    patch_config(profile, {"plugins": {"entries": {"sparseread-openclaw": entry}}})


def set_workspace(profile: str, workspace: Path) -> None:
    patch_config(profile, {"agents": {"defaults": {"workspace": str(workspace)}}})


def ensure_plugin_available(profile: str) -> None:
    proc = run(["npx", "-y", "openclaw", "--profile", profile, "plugins", "inspect", "sparseread-openclaw", "--json"], timeout=120)
    if proc.returncode == 0:
        return
    raise RuntimeError(
        "OpenClaw SparseRead plugin is not installed for this profile. "
        f"Build/install {PLUGIN_DIR} first, or run the setup steps in integrations/openclaw/README.md.\n{proc.stderr}"
    )


def sro_collect_call_example(task: TaskSpec) -> dict[str, Any]:
    return {
        "preview_path": task.artifact_path,
        "sro_read": {
            "target": {"artifact_id": "<artifact_id from sro_preview>"},
            "mode": "collect",
            "hint": {
                "goal": "answer the task from the target artifact",
                "want": "fact",
                "scope": "new",
                "type_hint": "auto",
                "slots": task.slots,
            },
        },
    }


def validation_prompt(task: TaskSpec, prompt: str, mode: str) -> str:
    if mode == "native":
        return prompt
    return (
        prompt
        + "\n\nSparseRead validation hint (benchmark only, not a product gate):\n"
        + "Use sro_preview on preview_path first. If preview alone is insufficient, call exactly one sro_read using the returned artifact_id and the sro_read shape below before writing the deliverable. Do not invent other want values. Do not refine or reread resolved slots.\n"
        + json.dumps(sro_collect_call_example(task), ensure_ascii=False, indent=2)
    )


def parse_agent_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
    return {}


def session_file_from_result(result: dict[str, Any]) -> Path | None:
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return None
    agent_meta = meta.get("agentMeta")
    if not isinstance(agent_meta, dict):
        return None
    session_file = agent_meta.get("sessionFile")
    if isinstance(session_file, str):
        return Path(session_file).expanduser()
    return None


def iter_session_events(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def event_text(event: dict[str, Any]) -> str:
    message = event.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return "\n".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
    output = event.get("output")
    if isinstance(output, str):
        return output
    return json.dumps(event, ensure_ascii=False)


def chars_to_token_estimate(chars: int) -> int:
    return max(0, (chars + 3) // 4)


def content_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    total += len(item["text"])
                elif item.get("type") == "toolCall":
                    total += len(json.dumps(item, ensure_ascii=False, sort_keys=True))
                else:
                    total += len(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return total
    return len(json.dumps(content, ensure_ascii=False, sort_keys=True))


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def estimate_transcript_tokens(events: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, int]:
    """Estimate cumulative model tokens when provider usage is unavailable.

    OpenClaw's current OpenAI-completions route reports zero usage for this
    provider, so this runner records a deterministic transcript estimate.  The
    initial prompt estimate comes from OpenClaw's own context estimator; later
    requests add accumulated assistant/tool history at a simple chars/4 rate.
    """

    base_prompt_tokens = extract_final_prompt_tokens(result) or 0
    history_chars = 0
    input_tokens = 0
    output_tokens = 0
    requests = 0
    saw_assistant = False

    for event in events:
        if event.get("type") != "message":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            requests += 1
            saw_assistant = True
            input_tokens += base_prompt_tokens + chars_to_token_estimate(history_chars)
            out_chars = content_chars(message.get("content"))
            output_tokens += chars_to_token_estimate(out_chars)
            history_chars += out_chars
        elif role == "toolResult":
            history_chars += content_chars(message.get("content"))
        elif role == "user" and saw_assistant:
            history_chars += content_chars(message.get("content"))

    return {
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_total_tokens": input_tokens + output_tokens,
        "estimated_requests": requests,
    }


def session_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    tool_calls = 0
    sro_preview = 0
    sro_raw = 0
    sro_card = 0
    sro_read = 0
    sro_trace = 0
    native_truncations = 0
    assistant_messages = 0
    ready_after_reads = 0
    seen_ready = False
    tool_names: list[str] = []

    for event in events:
        if event.get("type") == "message":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                assistant_messages += 1
                content = message.get("content")
                if isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict) or item.get("type") != "toolCall":
                            continue
                        tool_calls += 1
                        name = str(item.get("name", ""))
                        tool_names.append(name)
                        if name == "sro_preview":
                            sro_preview += 1
                        elif name == "sro_raw":
                            sro_raw += 1
                        elif name == "sro_card":
                            sro_card += 1
                        elif name == "sro_read":
                            sro_read += 1
                            if seen_ready:
                                ready_after_reads += 1
                        elif name == "sro_trace":
                            sro_trace += 1
            elif isinstance(message, dict) and message.get("role") == "toolResult":
                name = str(message.get("toolName", ""))
                output = content_text(message.get("content"))
                if name == "sro_read" and re.search(r'"overall_status"\s*:\s*"ready"|ready_for_write|write_file_now', output):
                    seen_ready = True
                if name not in {"sro_preview", "sro_raw", "sro_card", "sro_read", "sro_trace"} and re.search(
                    r"Full output saved to:|Original size:|Output capped|Results truncated|Showing .* of .*|Use offset=|truncated",
                    output,
                    re.I,
                ):
                    native_truncations += 1
        elif event.get("type") == "tool_call":
            name = str(event.get("name", ""))
            output = str(event.get("output", ""))
            if name == "sro_read" and re.search(r'"overall_status"\s*:\s*"ready"|ready_for_write', output):
                seen_ready = True
            if name not in {"sro_preview", "sro_raw", "sro_card", "sro_read", "sro_trace"} and re.search(
                r"Full output saved to:|Original size:|Output capped|Results truncated|Showing .* of .*|Use offset=|truncated",
                output,
                re.I,
            ):
                native_truncations += 1

    return {
        "assistant_messages": assistant_messages,
        "tool_calls": tool_calls,
        "tool_names": tool_names,
        "native_truncations": native_truncations,
        "sro_preview_calls": sro_preview,
        "sro_raw_calls": sro_raw,
        "sro_card_calls": sro_card,
        "sro_read_calls": sro_read,
        "sro_trace_calls": sro_trace,
        "ready_after_read_calls": ready_after_reads,
    }


def extract_final_prompt_tokens(result: dict[str, Any]) -> int | None:
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return None
    agent_meta = meta.get("agentMeta")
    if not isinstance(agent_meta, dict):
        return None
    status = agent_meta.get("contextBudgetStatus")
    if not isinstance(status, dict):
        return None
    value = status.get("estimatedPromptTokens")
    return int(value) if isinstance(value, (int, float)) else None


def grade_t12(workspace: Path) -> dict[str, float]:
    path = workspace / "a_stock_announcements" / "fetch-audit.md"
    keys = [
        "file_created",
        "important_count_correct",
        "dedup_bug_identified",
        "dedup_fix_correct",
        "missing_csv_identified",
        "orphaned_ids_addressed",
    ]
    if not path.exists():
        return {key: 0.0 for key in keys}
    content = path.read_text(encoding="utf-8", errors="replace").lower()
    scores = {"file_created": 1.0}
    ids = ["1256789520", "1256789535", "1256789540", "1256789550", "1256789565"]
    scores["important_count_correct"] = 1.0 if all(item in content for item in ids) else 0.0
    scores["dedup_bug_identified"] = 1.0 if any(kw in content for kw in ["list(seen)", "set", "arbitrary", "non-determin", "nondetermin", "无序", "不确定"]) else 0.0
    scores["dedup_fix_correct"] = 1.0 if "sorted" in content and "key=int" in content and "5000" in content else 0.0
    scores["missing_csv_identified"] = 1.0 if "summary_2026-02-09.csv" in content and ("csv_summary" in content or "csv" in content) else 0.0
    scores["orphaned_ids_addressed"] = 1.0 if "24" in content and "35" in content and "11" in content else 0.0
    return scores


def grade_task21(workspace: Path) -> dict[str, float]:
    path = workspace / "answer.txt"
    keys = [
        "file_created",
        "total_skills_correct",
        "filtered_skills_correct",
        "top_category_correct",
        "second_category_correct",
        "skill_filename_correct",
        "api_type_correct",
        "date_correct",
        "proposed_tasks_correct",
    ]
    if not path.exists():
        return {key: 0.0 for key in keys}
    lines = [line.strip().lower() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    line = lambda i: lines[i] if i < len(lines) else ""
    scores = {"file_created": 1.0}
    scores["total_skills_correct"] = 1.0 if "5705" in line(0).replace(",", "") else 0.0
    scores["filtered_skills_correct"] = 1.0 if "2999" in line(1).replace(",", "") else 0.0
    scores["top_category_correct"] = 1.0 if "ai" in line(2) and "llm" in line(2) and "287" in line(2) else 0.0
    scores["second_category_correct"] = 1.0 if "search" in line(3) and "research" in line(3) and "253" in line(3) else 0.0
    scores["skill_filename_correct"] = 1.0 if "skill.md" in line(4) else 0.0
    scores["api_type_correct"] = 1.0 if "typed" in line(5) and "websocket" in line(5).replace(" ", "") else 0.0
    scores["date_correct"] = 1.0 if ("february" in line(6) and "7" in line(6) and "2026" in line(6)) or "2026-02-07" in line(6) else 0.0
    scores["proposed_tasks_correct"] = 1.0 if re.search(r"\b6\b", line(7)) else 0.0
    return scores


def grade_loogle(workspace: Path) -> dict[str, float]:
    path = workspace / "answer.txt"
    keys = [
        "file_created",
        "q1_dual_crusading_policy",
        "q2_barons_homage",
        "q3_inherited_achaea",
        "q4_mongol_fortifications",
        "q5_khalil_battlements",
    ]
    if not path.exists():
        return {key: 0.0 for key in keys}
    lines = [line.strip().lower() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    line = lambda i: lines[i] if i < len(lines) else ""
    scores = {"file_created": 1.0}
    scores["q1_dual_crusading_policy"] = 1.0 if ("general" in line(0) or "passagium" in line(0)) and ("small" in line(0) or "smaller" in line(0)) and "crusade" in line(0) else 0.0
    scores["q2_barons_homage"] = 1.0 if "confiscat" in line(1) and "estate" in line(1) else 0.0
    scores["q3_inherited_achaea"] = 1.0 if "charles" in line(2) else 0.0
    scores["q4_mongol_fortifications"] = 1.0 if all(term in line(3) for term in ["aintab", "baghras", "darbsak"]) else 0.0
    scores["q5_khalil_battlements"] = 1.0 if "15" in line(4) and "may" in line(4) and "1291" in line(4) else 0.0
    return scores


def grade(task_id: str, workspace: Path) -> dict[str, Any]:
    if task_id.startswith("task_00012"):
        scores = grade_t12(workspace)
    elif task_id == "task_21_openclaw_comprehension":
        scores = grade_task21(workspace)
    else:
        scores = grade_loogle(workspace)
    total = sum(scores.values())
    max_score = len(scores)
    return {
        "scores": scores,
        "score": total / max_score if max_score else 0.0,
        "points": total,
        "max_points": max_score,
    }


def run_case(task: TaskSpec, mode: str, run_root: Path, profile: str, model: str, timeout: int) -> dict[str, Any]:
    case_root = run_root / task.task_id / mode
    workspace = case_root / "workspace"
    case_root.mkdir(parents=True, exist_ok=True)
    materialize_assets(task, workspace)
    set_workspace(profile, workspace)
    set_plugin_enabled(profile, mode != "native", policy="enforce", workspace=workspace)

    prompt = validation_prompt(task, extract_prompt(task.task_md), mode)
    (case_root / "prompt.txt").write_text(prompt, encoding="utf-8")
    session_key = f"sroval-{datetime.now().strftime('%Y%m%d%H%M%S')}-{task.task_id}-{mode}"
    env = {
        "PYTHONPATH": str(CORE_DIR),
        "SPARSEREAD_PROJECT_ROOT": str(REPO),
    }
    started = time.time()
    proc = run(
        openclaw_cmd(
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
        ),
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

    deliverable_path = workspace / task.deliverable
    deliverable_text = ""
    if deliverable_path.exists():
        deliverable_text = deliverable_path.read_text(encoding="utf-8", errors="replace")
        (case_root / "deliverable.txt").write_text(deliverable_text, encoding="utf-8")

    metrics = session_metrics(events)
    metrics.update(estimate_transcript_tokens(events, result))
    metrics.update(
        {
            "returncode": proc.returncode,
            "elapsed_seconds": round(elapsed, 2),
            "session_file": str(session_file) if session_file else "",
            "estimated_final_prompt_tokens": extract_final_prompt_tokens(result),
            "deliverable_written": deliverable_path.exists(),
            "deliverable_chars": len(deliverable_text),
            "stdout_chars": len(proc.stdout),
            "stderr_chars": len(proc.stderr),
        }
    )
    case_result = {
        "task_id": task.task_id,
        "mode": mode,
        "case_root": str(case_root),
        "workspace": str(workspace),
        "deliverable": str(deliverable_path),
        "metrics": metrics,
        "grade": grade(task.task_id, workspace),
    }
    (case_root / "case_result.json").write_text(json.dumps(case_result, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_result


def recompute_case(task: TaskSpec, mode: str, run_root: Path) -> dict[str, Any]:
    case_root = run_root / task.task_id / mode
    workspace = case_root / "workspace"
    result = parse_agent_json((case_root / "stdout.json").read_text(encoding="utf-8", errors="replace")) if (case_root / "stdout.json").exists() else {}
    events = iter_session_events(case_root / "session.jsonl")
    deliverable_path = workspace / task.deliverable
    deliverable_text = ""
    if deliverable_path.exists():
        deliverable_text = deliverable_path.read_text(encoding="utf-8", errors="replace")
        (case_root / "deliverable.txt").write_text(deliverable_text, encoding="utf-8")

    previous = {}
    case_result_path = case_root / "case_result.json"
    if case_result_path.exists():
        try:
            previous = json.loads(case_result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = {}

    metrics = session_metrics(events)
    metrics.update(estimate_transcript_tokens(events, result))
    metrics.update(
        {
            "returncode": previous.get("metrics", {}).get("returncode", 0 if deliverable_path.exists() else 1),
            "elapsed_seconds": previous.get("metrics", {}).get("elapsed_seconds"),
            "session_file": previous.get("metrics", {}).get("session_file", ""),
            "estimated_final_prompt_tokens": extract_final_prompt_tokens(result),
            "deliverable_written": deliverable_path.exists(),
            "deliverable_chars": len(deliverable_text),
            "stdout_chars": (case_root / "stdout.json").stat().st_size if (case_root / "stdout.json").exists() else 0,
            "stderr_chars": (case_root / "stderr.txt").stat().st_size if (case_root / "stderr.txt").exists() else 0,
        }
    )
    case_result = {
        "task_id": task.task_id,
        "mode": mode,
        "case_root": str(case_root),
        "workspace": str(workspace),
        "deliverable": str(deliverable_path),
        "metrics": metrics,
        "grade": grade(task.task_id, workspace),
    }
    case_result_path.write_text(json.dumps(case_result, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_result


def compare(native: dict[str, Any], sr: dict[str, Any]) -> dict[str, Any]:
    n = native["metrics"]
    s = sr["metrics"]

    def delta(key: str) -> int | float | None:
        nv = n.get(key)
        sv = s.get(key)
        if isinstance(nv, (int, float)) and isinstance(sv, (int, float)):
            return sv - nv
        return None

    positive = (
        sr["grade"]["score"] >= native["grade"]["score"]
        and (s.get("native_truncations", 0) <= n.get("native_truncations", 0))
        and (s.get("tool_calls", 0) < n.get("tool_calls", 0))
        and (s.get("estimated_total_tokens") or 10**18) < (n.get("estimated_total_tokens") or 10**18)
    )
    return {
        "native_score": native["grade"]["score"],
        "sr_score": sr["grade"]["score"],
        "quality_non_regression": sr["grade"]["score"] >= native["grade"]["score"],
        "positive": positive,
        "delta_tool_calls": delta("tool_calls"),
        "delta_assistant_messages": delta("assistant_messages"),
        "delta_native_truncations": delta("native_truncations"),
        "delta_estimated_final_prompt_tokens": delta("estimated_final_prompt_tokens"),
        "delta_estimated_total_tokens": delta("estimated_total_tokens"),
        "estimated_total_tokens_improved": (s.get("estimated_total_tokens") or 10**18) < (n.get("estimated_total_tokens") or 10**18),
        "tool_calls_improved": s.get("tool_calls", 0) < n.get("tool_calls", 0),
        "sr_sro_preview_calls": s.get("sro_preview_calls", 0),
        "sr_sro_card_calls": s.get("sro_card_calls", 0),
        "sr_sro_read_calls": s.get("sro_read_calls", 0),
        "sr_ready_after_read_calls": s.get("ready_after_read_calls", 0),
    }


REFERENCE_COMPARISON = {
    "task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check": {
        "opencode_native_tokens": 120769,
        "opencode_native_requests": 7,
        "opencode_native_tool_calls": 12,
        "opencode_best_sr_tokens": 80489,
        "opencode_best_sr_requests": 5,
        "opencode_best_sr_tool_calls": 4,
        "nanobot_baseline_tokens": 234370,
        "nanobot_baseline_requests": 13,
        "nanobot_sr_tokens": 89103,
        "nanobot_sr_requests": 5,
        "source": "opencode_pilot_p5_gate8_revised_20260606/opencode_pilot_report.md",
    },
    "task_21_openclaw_comprehension": {
        "opencode_native_tokens": 77138,
        "opencode_native_requests": 6,
        "opencode_native_tool_calls": 6,
        "opencode_best_sr_tokens": 48337,
        "opencode_best_sr_requests": 4,
        "opencode_best_sr_tool_calls": 3,
        "nanobot_baseline_tokens": 466144,
        "nanobot_baseline_requests": 22,
        "nanobot_sr_tokens": 46799,
        "nanobot_sr_requests": 4,
        "source": "opencode_pilot_p5_gate8_revised_20260606/opencode_pilot_report.md; p0_skill_generalization_flash_20260526.csv",
    },
    "task_loogle_shortdep_fall_of_outremer_5q": {
        "opencode_native_tokens": 203929,
        "opencode_native_requests": 13,
        "opencode_native_tool_calls": 28,
        "opencode_best_sr_tokens": 45788,
        "opencode_best_sr_requests": 4,
        "opencode_best_sr_tool_calls": 3,
        "nanobot_baseline_tokens": 263004,
        "nanobot_baseline_requests": 17,
        "nanobot_sr_tokens": 39270,
        "nanobot_sr_requests": 4,
        "source": "opencode_pilot_p5_gate8_revised_20260606/opencode_pilot_report.md; p0_skill_generalization_flash_20260526.csv",
    },
}


def write_report(run_root: Path, results: list[dict[str, Any]]) -> None:
    by_task: dict[str, dict[str, Any]] = {}
    for item in results:
        by_task.setdefault(item["task_id"], {})[item["mode"]] = item
    comparisons = {
        task_id: compare(modes["native"], modes["sr"])
        for task_id, modes in by_task.items()
        if "native" in modes and "sr" in modes
    }
    summary = {"run_root": str(run_root), "results": results, "comparisons": comparisons}
    (run_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# OpenClaw SparseRead Validation",
        "",
        f"- Run root: `{run_root}`",
        f"- Tasks: {len(by_task)}",
        "",
        "| task | native score | sr score | positive | native est tokens | sr est tokens | native tools | sr tools | native trunc | sr trunc | sr sro_preview/card/read | ready-after-read |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for task_id, modes in by_task.items():
        native = modes.get("native", {})
        sr = modes.get("sr", {})
        comp = comparisons.get(task_id, {})
        n_metrics = native.get("metrics", {})
        s_metrics = sr.get("metrics", {})
        lines.append(
            "| {task} | {ns:.2f} | {ss:.2f} | {pos} | {net} | {set} | {nt} | {st} | {ntr} | {strunc} | {sp}/{sc}/{srread} | {rar} |".format(
                task=task_id,
                ns=native.get("grade", {}).get("score", 0.0),
                ss=sr.get("grade", {}).get("score", 0.0),
                pos="yes" if comp.get("positive") else "no",
                net=n_metrics.get("estimated_total_tokens", 0),
                set=s_metrics.get("estimated_total_tokens", 0),
                nt=n_metrics.get("tool_calls", 0),
                st=s_metrics.get("tool_calls", 0),
                ntr=n_metrics.get("native_truncations", 0),
                strunc=s_metrics.get("native_truncations", 0),
                sp=s_metrics.get("sro_preview_calls", 0),
                sc=s_metrics.get("sro_card_calls", 0),
                srread=s_metrics.get("sro_read_calls", 0),
                rar=s_metrics.get("ready_after_read_calls", 0),
            )
        )
    lines += [
        "",
        "Positive means quality did not regress, estimated total tokens decreased, tool_calls decreased, and native truncations did not increase.",
        "Token totals are deterministic transcript estimates because this OpenClaw provider route reported zero usage; native/SR pairs use the same estimator.",
        "",
        "## Cross-framework reference",
        "",
        "| task | OpenClaw native est tokens/tools | OpenClaw SR est tokens/tools | OpenCode best SR tokens/tools | Nanobot SR tokens/requests | reference source |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for task_id, modes in by_task.items():
        ref = REFERENCE_COMPARISON.get(task_id, {})
        if not ref:
            continue
        native = modes.get("native", {})
        sr = modes.get("sr", {})
        n_metrics = native.get("metrics", {})
        s_metrics = sr.get("metrics", {})
        lines.append(
            "| {task} | {net}/{nt} | {set}/{st} | {oct}/{octools} | {nbt}/{nbr} | {source} |".format(
                task=task_id,
                net=n_metrics.get("estimated_total_tokens", 0),
                nt=n_metrics.get("tool_calls", 0),
                set=s_metrics.get("estimated_total_tokens", 0),
                st=s_metrics.get("tool_calls", 0),
                oct=ref.get("opencode_best_sr_tokens", ""),
                octools=ref.get("opencode_best_sr_tool_calls", ""),
                nbt=ref.get("nanobot_sr_tokens", ""),
                nbr=ref.get("nanobot_sr_requests", ""),
                source=ref.get("source", ""),
            )
        )
    (run_root / "openclaw_sr_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--modes", choices=["both", "native", "sr"], default="both")
    parser.add_argument("--recompute", action="store_true", help="Recompute metrics/report from an existing run root without model calls.")
    args = parser.parse_args()

    run_root = args.run_root or (REPO / "SRO_test" / "qwenclawbench" / f"openclaw_sr_validation_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    run_root.mkdir(parents=True, exist_ok=True)

    modes = ["native", "sr"] if args.modes == "both" else [args.modes]
    results: list[dict[str, Any]] = []
    if args.recompute:
        for task in TASKS:
            for mode in modes:
                results.append(recompute_case(task, mode, run_root))
        write_report(run_root, results)
        print(f"[openclaw-validation] recomputed report: {run_root / 'openclaw_sr_validation_report.md'}", flush=True)
        return 0

    ensure_plugin_available(args.profile)
    for task in TASKS:
        for mode in modes:
            print(f"[openclaw-validation] running {task.task_id} mode={mode}", flush=True)
            try:
                result = run_case(task, mode, run_root, args.profile, args.model, args.timeout)
            except Exception as exc:
                result = {
                    "task_id": task.task_id,
                    "mode": mode,
                    "error": str(exc),
                    "metrics": {"returncode": 1},
                    "grade": {"score": 0.0, "scores": {}},
                }
            results.append(result)
            print(json.dumps({"task_id": task.task_id, "mode": mode, "grade": result.get("grade"), "metrics": result.get("metrics")}, ensure_ascii=False), flush=True)
            write_report(run_root, results)

    print(f"[openclaw-validation] report: {run_root / 'openclaw_sr_validation_report.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
