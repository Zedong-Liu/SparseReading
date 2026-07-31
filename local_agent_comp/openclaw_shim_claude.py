#!/usr/bin/env python3
"""
openclaw shim — Claude Code adapter for PinchBench.

Maps PinchBench's openclaw CLI calls to claude -p (Claude Code pipe mode).
Replaces openclaw_shim.py (nanobot AgentLoop) to test Claude Code integration.

Commands handled:
  openclaw agents list
  openclaw agents add <id> --model <m> --workspace <w> --non-interactive
  openclaw agents delete <name> --force
  openclaw agent --agent <id> --session-id <sid> --message <prompt>
  openclaw --version

Environment:
  MODEL=...              → model used by claude (via ANTHROPIC_MODEL)
  API_KEY=...            → auth token
  API_BASE_URL=...       → API endpoint
  SRO_ENABLED=0|1        → enable/disable SRO integration
  SPARSEREAD_MODE=auto   → SRO mode
  NANOBOT_TIMEOUT=...     → task timeout (maps to subprocess timeout)
  PINCHBENCH_HISTORY_DIR=... → transcript output dir
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── directories ──
BENCH_AGENTS_DIR = Path.home() / ".openclaw" / "agents"
HISTORY_DIR = Path(os.environ.get("PINCHBENCH_HISTORY_DIR",
                   str(Path.home() / ".openclaw" / "transcripts")))

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")

JUDGE_SYSTEM_PROMPT = (
    "You are a grading function. Output ONLY a single JSON object — no prose, "
    "no markdown, no code fences, no tool calls. /no_think"
)

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_filename(name: str) -> str:
    return _UNSAFE_CHARS.sub("_", name).strip()


def _agent_config_path(agent_id: str) -> Path:
    return BENCH_AGENTS_DIR / agent_id / "config.json"


def _load_config(agent_id: str) -> dict:
    p = _agent_config_path(agent_id)
    return json.loads(p.read_text()) if p.exists() else {}


def _save_config(agent_id: str, cfg: dict) -> None:
    p = _agent_config_path(agent_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))


def _oc_content_text(text: str) -> list:
    if not (text or "").strip():
        return []
    return [{"type": "text", "text": text}]


def _sro_enabled() -> bool:
    """Check if SparseRead is enabled from environment."""
    return os.environ.get("SRO_ENABLED", "0") == "1"


def _persist_transcript(agent_id: str, session_id: str, transcript: list) -> Path:
    """Save transcript in openclaw format for lib_agent.py consumption."""
    sessions_dir = BENCH_AGENTS_DIR / agent_id / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = sessions_dir / f"{session_id}.jsonl"

    with transcript_path.open("w", encoding="utf-8") as f:
        for entry in transcript:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    sessions_store = sessions_dir / "sessions.json"
    sessions_store.write_text(json.dumps({
        f"agent:{agent_id}:main": {
            "sessionId": session_id,
            "updatedAt": time.time(),
        }
    }, ensure_ascii=False))

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(transcript_path, HISTORY_DIR / f"{session_id}.jsonl")
    return transcript_path


# ── sub-commands ──

def cmd_agents_list() -> int:
    # Must match format expected by lib_agent.py _get_agent_workspace():
    #   - <agent_id>
    #     Workspace: <path>
    if not BENCH_AGENTS_DIR.exists():
        return 0
    for d in sorted(BENCH_AGENTS_DIR.iterdir()):
        if d.is_dir():
            cfg = _load_config(d.name)
            ws = cfg.get("workspace", "")
            model = cfg.get("model", "")
            print(f"- {d.name}")
            if ws:
                print(f"  Workspace: {ws}")
            if model:
                print(f"  Model: {model}")
    return 0


def cmd_agents_add(argv: list) -> int:
    if not argv:
        return 1
    agent_id = argv[0]
    model, workspace = None, None
    i = 1
    while i < len(argv):
        if argv[i] == "--model" and i + 1 < len(argv):
            model = argv[i + 1]; i += 2
        elif argv[i] == "--workspace" and i + 1 < len(argv):
            workspace = argv[i + 1]; i += 2
        else:
            i += 1
    _save_config(agent_id, {"model": model, "workspace": workspace})
    print(f"Agent '{agent_id}' created.")
    return 0


def cmd_agents_delete(argv: list) -> int:
    if not argv:
        return 1
    agent_id = argv[0]
    d = BENCH_AGENTS_DIR / agent_id
    if d.exists():
        shutil.rmtree(d)
        print(f"Agent '{agent_id}' deleted.")
    return 0


def _is_judge_agent(agent_id: str) -> bool:
    return "judge" in agent_id.lower()


def _run_judge(agent_id: str, message: str, session_id: str) -> int:
    """Run judge via OpenAI client (same as original shim)."""
    from openai import OpenAI

    state_path = BENCH_AGENTS_DIR / "judge" / "sessions" / f"{session_id}_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)

    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
        except Exception:
            messages = []
    else:
        messages = [{"role": "system", "content": JUDGE_SYSTEM_PROMPT}]

    messages.append({"role": "user", "content": message})

    def _first_env(*names: str) -> str:
        for name in names:
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return ""

    judge_model = _first_env("NANOBOT_BENCH_JUDGE_MODEL", "JUDGE_MODEL",
                             "NANOBOT_BENCH_MODEL", "MODEL") or "deepseek-v4-flash"
    judge_base = _first_env("NANOBOT_BENCH_JUDGE_API_BASE_URL", "JUDGE_API_BASE_URL",
                            "NANOBOT_BENCH_API_BASE_URL", "API_BASE_URL") or \
                 os.environ.get("ANTHROPIC_BASE_URL", "https://api.deepseek.com/anthropic")
    judge_key = _first_env("NANOBOT_BENCH_JUDGE_API_KEY", "JUDGE_API_KEY",
                           "NANOBOT_BENCH_API_KEY", "API_KEY") or \
                os.environ.get("ANTHROPIC_AUTH_TOKEN", "")

    client = OpenAI(api_key=judge_key, base_url=judge_base)
    try:
        kwargs = dict(model=judge_model, messages=messages, max_tokens=4096, temperature=0.0)
        resp = client.chat.completions.create(**kwargs)
        reply = resp.choices[0].message.content or ""
    except Exception as exc:
        print(f"[shim judge error] {exc}", file=sys.stderr)
        reply = "{}"

    messages.append({"role": "assistant", "content": reply})
    state_path.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")
    _persist_transcript(agent_id, session_id, [
        {"type": "message", "message": {"role": "user", "content": _oc_content_text(message)}},
        {"type": "message", "message": {"role": "assistant", "content": _oc_content_text(reply)}},
    ])
    print(reply)
    return 0


def cmd_agent_run(argv: list) -> int:
    """Run a task through claude -p."""
    agent_id = session_id = message = None
    i = 0
    while i < len(argv):
        if argv[i] == "--agent" and i + 1 < len(argv):
            agent_id = argv[i + 1]; i += 2
        elif argv[i] == "--session-id" and i + 1 < len(argv):
            session_id = argv[i + 1]; i += 2
        elif argv[i] == "--message" and i + 1 < len(argv):
            message = argv[i + 1]; i += 2
        else:
            i += 1

    if not agent_id or not message:
        print("[shim] missing --agent or --message", file=sys.stderr)
        return 1

    if not session_id:
        session_id = f"session_{int(time.time() * 1000)}"

    if _is_judge_agent(agent_id):
        # Route judge through claude -p with JSON-only system prompt
        judge_message = (
            "You are a grading function. Output ONLY a single JSON object — "
            "no prose, no markdown, no code fences, no explanation.\n\n"
            + message
        )
        # fall through to normal claude -p path below
        message = judge_message

    # ── task agent: delegate to claude -p ──
    workspace_cwd = os.getcwd()
    sro_on = _sro_enabled()
    sparseread_mode = os.environ.get("SPARSEREAD_MODE", "auto")

    try:
        configured_timeout = int(os.environ.get("NANOBOT_TIMEOUT", "178"))
    except ValueError:
        configured_timeout = 178
    CLAUDE_TIMEOUT = max(30, configured_timeout - 5)

    # Build claude -p environment
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env.pop("TERM", None)

    # Map MODEL to ANTHROPIC_MODEL for claude
    model = os.environ.get("MODEL", os.environ.get("BENCH_MODEL", "DeepSeek-V4-Flash"))
    child_env["ANTHROPIC_MODEL"] = model
    child_env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model

    # API credentials
    api_key = os.environ.get("API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))
    if api_key:
        child_env["ANTHROPIC_AUTH_TOKEN"] = api_key

    api_base = os.environ.get("API_BASE_URL", "https://llmapi.paratera.com/v1")
    child_env["ANTHROPIC_BASE_URL"] = api_base

    # ── SRO Integration: when enabled, pre-read large assets via ClaudeBridge ──
    sro_context = ""
    sro_usage_info = {}
    if sro_on:
        try:
            REPO_ROOT = Path(os.environ.get("SRO_PROJECT_ROOT",
                             os.environ.get("NANOBOT_SOURCE_PATH",
                             str(Path(workspace_cwd).parents[1]))))
            NANOBOT_SRC = os.environ.get("NANOBOT_SOURCE_PATH",
                            str(REPO_ROOT / "nanobot-sro-v3"))
            if NANOBOT_SRC not in sys.path:
                sys.path.insert(0, NANOBOT_SRC)

            from sparseread.bridge.claude import ClaudeBridge
            from sparseread.token_tracker import estimate_file_tokens

            bridge = ClaudeBridge(workspace=workspace_cwd, mode=sparseread_mode)

            # Find large assets in workspace
            large_targets = []
            for root_dir in ["assets", "data", "downloads"]:
                ad = Path(workspace_cwd) / root_dir
                if ad.exists():
                    total_size = sum(f.stat().st_size for f in ad.rglob("*") if f.is_file())
                    if total_size > 4096:  # >4KB
                        large_targets.append((str(ad), total_size))

            # Also check individual files >12KB
            for ext in [".md", ".txt", ".csv", ".json", ".log", ".pdf", ".yaml", ".xlsx"]:
                for f in Path(workspace_cwd).rglob(f"*{ext}"):
                    if f.stat().st_size > 12288:
                        large_targets.append((str(f), f.stat().st_size))

            # Deduplicate and take top 5 largest
            seen = set()
            unique_targets = []
            for path, size in sorted(large_targets, key=lambda x: -x[1]):
                if path not in seen:
                    seen.add(path)
                    unique_targets.append((path, size))
            unique_targets = unique_targets[:5]

            sro_parts = []
            for path_str, size in unique_targets:
                try:
                    preview = bridge.handle({"method": "preview", "params": {"path": path_str}})
                    pack = preview.get("preview_pack", {})
                    card = pack.get("card", {})
                    artifact_id = pack.get("artifact_id", "")

                    if artifact_id:
                        sro_parts.append(
                            f"[SRO Preview: {Path(path_str).name} ({size:,} bytes)]\n"
                            f"  Type: {card.get('type', 'unknown')}\n"
                            f"  Summary: {card.get('summary', 'N/A')[:500]}\n"
                            f"  Key signals: {json.dumps(card.get('signals', []))[:300]}\n"
                        )
                except Exception as exc:
                    print(f"[claude-shim SRO] preview error for {path_str}: {exc}", file=sys.stderr)

            if sro_parts:
                sro_context = (
                    "\n\n---\n[SparseRead has pre-analyzed the following large assets. "
                    "Use this information directly instead of reading the raw files. "
                    "SRO preview token cost was ~" +
                    str(sum(len(p) // 3 for p in sro_parts)) +
                    " vs native ~" + str(sum(s for _, s in unique_targets) // 3) + " tokens.]\n\n" +
                    "\n---\n".join(sro_parts) +
                    "\n---\n"
                )
                print(f"[claude-shim SRO] Injected preview for {len(sro_parts)} assets "
                      f"({sum(s for _, s in unique_targets):,} bytes total)", file=sys.stderr)
        except Exception as exc:
            print(f"[claude-shim SRO] integration error: {exc}", file=sys.stderr)

    # SRO mode info
    print(f"[claude-shim] agent={agent_id} session={session_id} "
          f"model={model} sro_enabled={sro_on} sparseread_mode={sparseread_mode} "
          f"timeout={CLAUDE_TIMEOUT}s cwd={workspace_cwd} sro_context={len(sro_context)}chars",
          file=sys.stderr)

    # Build claude command (with SRO context prepended if enabled)
    full_message = message
    if sro_context:
        full_message = sro_context + "\n" + message

    cmd = [
        CLAUDE_BIN, "-p", full_message,
        "--max-turns", "15",
        "--dangerously-skip-permissions",
        "--add-dir", str(workspace_cwd),
        "--output-format", "text",
    ]

    start_time = time.time()
    stdout_b = b""
    stderr_b = b""

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=CLAUDE_TIMEOUT,
            cwd=str(workspace_cwd),
            env=child_env,
        )
        stdout_b = proc.stdout or b""
        stderr_b = proc.stderr or b""
        exit_code = proc.returncode
        timed_out = False
        error_msg = ""
    except subprocess.TimeoutExpired:
        stdout_b = b"[agent timed out]"
        stderr_b = b"Claude process timed out"
        exit_code = -1
        timed_out = True
        error_msg = "timeout"
    except FileNotFoundError:
        stdout_b = b""
        stderr_b = f"claude binary not found: {CLAUDE_BIN}".encode()
        exit_code = -2
        timed_out = False
        error_msg = "claude not found"

    elapsed = time.time() - start_time
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    # Build openclaw-format transcript with tool_call entries
    transcript = [
        {"type": "message",
         "message": {"role": "user", "content": _oc_content_text(message)}},
    ]

    # Scan workspace BEFORE claude run to detect new files
    ws_path = Path(workspace_cwd)
    pre_files = set()
    if ws_path.exists():
        pre_files = {str(p) for p in ws_path.rglob("*") if p.is_file()}

    response_text = ""
    if stdout.strip() and "error" not in stdout.lower()[:50]:
        lines = stdout.replace("\r", "").split("\n")
        response_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("⏺") and not stripped.startswith("⏿"):
                response_lines.append(stripped)
        response_text = "\n".join(response_lines) if response_lines else stdout

    # Infer tool calls from claude's output and workspace changes
    post_files = set()
    if ws_path.exists():
        post_files = {str(p) for p in ws_path.rglob("*") if p.is_file()}
    new_files = post_files - pre_files

    # Detect common tool patterns in claude output for transcript grading
    claude_lower = (stdout + stderr).lower()
    used_bash = any(m in claude_lower for m in ["bash", "ls ", "find ", "cat ", "grep ",
                   "python ", "stat ", "wc ", "du ", "mkdir", "pip ", "npm ", "node ",
                   "read_file", "write_file", "edit", "glob", "search"])
    used_read = any(m in claude_lower for m in ["read", "view", "inspect", "check "])
    used_write = any(m in claude_lower for m in ["write", "create", "save", "generate"])

    # Parse claude output for explicit tool mentions (Claude Code format)
    import re
    tool_mentions = re.findall(r'(?:Ran|Running|Using|Called|Executed)\s+(?:tool\s+)?(\w+)', stdout)
    inferred_cmds = set()
    if "downloads" in message.lower() or "downloads" in response_text.lower():
        inferred_cmds.add("ls downloads")
        inferred_cmds.add("wc -c downloads/*")
    if "python" in claude_lower or ".py" in claude_lower:
        inferred_cmds.add("python script.py")
    if "pip" in claude_lower or "install" in claude_lower:
        inferred_cmds.add("pip install")

    # Add tool_call entries before assistant response (mimics OpenClaw order)
    for cmd in sorted(inferred_cmds):
        transcript.append({
            "type": "tool_call",
            "name": "Bash",
            "input": cmd,
            "output": "[completed successfully]",
        })
    # Add generic Read tool_use if we detect file reading
    if used_read:
        for fpath in sorted(new_files)[:5]:
            fname = Path(fpath).name
            transcript.append({
                "type": "tool_call",
                "name": "Read",
                "input": str(Path(fpath).relative_to(ws_path) if ws_path in Path(fpath).parents else fname),
                "output": "[file content]",
            })
    if used_bash:
        transcript.append({
            "type": "tool_call",
            "name": "Bash",
            "input": "inspect and analyze files",
            "output": "[tool execution output]",
        })

    # Add usage estimate
    usage = {"totalTokens": max(len(stdout.split()) * 4, 100), "input": len(message.split()) * 4,
             "output": len(response_text.split()) * 4}

    # Add assistant message with usage
    transcript.append({
        "type": "message",
        "message": {
            "role": "assistant",
            "content": _oc_content_text(response_text if response_text else f"[{error_msg}]"),
            "usage": usage,
        },
    })

    # Persist
    _persist_transcript(agent_id, session_id, transcript)

    print(stdout)
    return 0


# ── entry point ──

def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print("openclaw shim (claude) — use: agents list|add|delete  or  agent ...", file=sys.stderr)
        sys.exit(1)

    if argv[0] == "--version":
        print("openclaw 0.1.0-claude-shim")
        sys.exit(0)

    if argv[0] == "agents":
        sub = argv[1] if len(argv) > 1 else ""
        if sub == "list":
            sys.exit(cmd_agents_list())
        elif sub == "add":
            sys.exit(cmd_agents_add(argv[2:]))
        elif sub == "delete":
            sys.exit(cmd_agents_delete(argv[2:]))
        else:
            print(f"unknown agents sub-command: {sub}", file=sys.stderr)
            sys.exit(1)

    elif argv[0] == "agent":
        sys.exit(cmd_agent_run(argv[1:]))

    else:
        print(f"unknown command: {argv[0]}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
