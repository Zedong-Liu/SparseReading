#!/usr/bin/env python3
"""
openclaw shim — maps PinchBench's openclaw CLI calls to nanobot's native agent loop.

Commands handled:
  openclaw agents list
  openclaw agents add <id> --model <m> --workspace <w> --non-interactive
  openclaw agents delete <name> --force
  openclaw agent --agent <id> --session-id <sid> --message <prompt>
  openclaw --version
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── directories ──────────────────────────────────────────────────────────────
BENCH_AGENTS_DIR = Path.home() / ".openclaw" / "agents"
HISTORY_DIR = Path(os.environ.get("PINCHBENCH_HISTORY_DIR", "/data/lzd/agent-comp/pinchbench/qwen35"))

NANOBOT_BIN = "/root/miniconda3/envs/kvserve-qwen35/bin/nanobot"
NANOBOT_CONFIG = Path(__file__).parent / "nanobot_bench_config.json"

# Used for the judge agent (no tools, pure-JSON output)
VLLM_BASE  = "http://127.0.0.1:8000/v1"
MODEL_NAME = "qwen35-local"

JUDGE_SYSTEM_PROMPT = (
    "You are a grading function. Output ONLY a single JSON object — no prose, "
    "no markdown, no code fences, no tool calls. /no_think"
)

# ── helpers ───────────────────────────────────────────────────────────────────

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


def _oc_usage(usage: dict | None) -> dict:
    if not isinstance(usage, dict) or not usage:
        return {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    cache_read_tokens = int(usage.get("cache_read_tokens") or usage.get("cached_tokens") or 0)
    cache_write_tokens = int(usage.get("cache_write_tokens") or 0)
    total_tokens = int(
        usage.get("total_tokens")
        or usage.get("totalTokens")
        or (input_tokens + output_tokens + cache_read_tokens + cache_write_tokens)
    )
    if total_tokens <= 0 and not any((input_tokens, output_tokens, cache_read_tokens, cache_write_tokens)):
        return {}
    return {
        "input": input_tokens,
        "output": output_tokens,
        "cacheRead": cache_read_tokens,
        "cacheWrite": cache_write_tokens,
        "totalTokens": total_tokens,
        "cost": {"total": 0.0},
    }


def _attach_usage_to_transcript(transcript: list, usage: dict | None) -> list:
    oc_usage = _oc_usage(usage)
    if not oc_usage:
        return transcript
    for entry in reversed(transcript):
        if entry.get("type") != "message":
            continue
        msg = entry.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        msg["usage"] = oc_usage
        return transcript
    return transcript


# ── nanobot session → openclaw transcript conversion ─────────────────────────

def _nanobot_to_openclaw(session_path: Path, user_message: str = "") -> list:
    """Convert nanobot's session JSONL to openclaw transcript format.

    Nanobot format (per message):
      {"role": "user"|"assistant"|"tool", "content": "...", "tool_calls": [...], ...}

    Openclaw format:
      {"type": "message", "message": {"role": "...", "content": [...]}}
      {"type": "tool_call", "name": "...", "input": "...", "output": "..."}
    """
    if not session_path.exists():
        return []

    messages = []
    runtime_checkpoint: dict | None = None
    for line in session_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("_type") == "metadata":
            meta = obj.get("metadata") or {}
            ckpt = meta.get("runtime_checkpoint")
            if isinstance(ckpt, dict):
                runtime_checkpoint = ckpt
            continue
        messages.append(obj)

    transcript = []
    # Map tool_call_id -> (name, input_str) for pairing with tool results
    pending: dict[str, tuple[str, str]] = {}

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if role == "user":
            text = content if isinstance(content, str) else json.dumps(content)
            transcript.append({
                "type": "message",
                "message": {"role": "user", "content": _oc_content_text(text)},
            })

        elif role == "assistant":
            if tool_calls:
                blocks: list = []
                if content and str(content).strip():
                    blocks.append({"type": "text", "text": str(content)})
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name", "exec")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    blocks.append({"type": "toolCall", "name": name, "arguments": args})
                    # Determine the "input" string for the tool_call entry
                    inp = args.get("command") or args.get("path") or json.dumps(args)
                    pending[tc.get("id", "")] = (name, str(inp))
                assistant_message = {"role": "assistant", "content": blocks}
                usage = _oc_usage(msg.get("usage"))
                if usage:
                    assistant_message["usage"] = usage
                transcript.append({
                    "type": "message",
                    "message": assistant_message,
                })
            else:
                text = content if isinstance(content, str) else str(content)
                if text.strip():
                    assistant_message = {"role": "assistant", "content": _oc_content_text(text)}
                    usage = _oc_usage(msg.get("usage"))
                    if usage:
                        assistant_message["usage"] = usage
                    transcript.append({
                        "type": "message",
                        "message": assistant_message,
                    })

        elif role == "tool":
            output = content if isinstance(content, str) else str(content)
            tid = msg.get("tool_call_id", "")
            name, inp = pending.pop(tid, ("exec", ""))
            transcript.append({
                "type": "tool_call",
                "name": name,
                "input": inp,
                "output": output,
            })

    recovered = _recover_transcript_from_checkpoint(runtime_checkpoint, user_message=user_message)
    has_non_user = any(
        entry.get("type") == "tool_call"
        or (
            entry.get("type") == "message"
            and (entry.get("message") or {}).get("role") == "assistant"
        )
        for entry in transcript
    )
    if has_non_user or not recovered:
        return transcript

    # The session file may have flushed the user turn but still hold the assistant/tool
    # state only inside runtime_checkpoint. In that case, merge the recovered suffix so
    # benchmark accounting sees the actual tool usage instead of a user-only transcript.
    merged = list(transcript)
    start_idx = 0
    if merged and recovered:
        first_live = merged[0]
        first_recovered = recovered[0]
        if (
            first_live.get("type") == "message"
            and first_recovered.get("type") == "message"
            and (first_live.get("message") or {}).get("role") == "user"
            and (first_recovered.get("message") or {}).get("role") == "user"
        ):
            start_idx = 1
    merged.extend(recovered[start_idx:])
    return merged

    return transcript


def _recover_transcript_from_checkpoint(runtime_checkpoint: dict | None, *, user_message: str = "") -> list[dict]:
    if not runtime_checkpoint:
        return []

    recovered: list[dict] = []
    pending: dict[str, tuple[str, str]] = {}

    if user_message.strip():
        recovered.append({
            "type": "message",
            "message": {"role": "user", "content": _oc_content_text(user_message)},
        })

    assistant = runtime_checkpoint.get("assistant_message") or {}
    if isinstance(assistant, dict):
        content = assistant.get("content") or ""
        tool_calls = assistant.get("tool_calls") or []
        blocks: list = []
        if str(content).strip():
            blocks.append({"type": "text", "text": str(content)})
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "exec")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            blocks.append({"type": "toolCall", "name": name, "arguments": args})
            inp = args.get("command") or args.get("path") or json.dumps(args)
            pending[tc.get("id", "")] = (name, str(inp))
        if blocks:
            assistant_message = {"role": "assistant", "content": blocks}
            usage = _oc_usage(assistant.get("usage"))
            if usage:
                assistant_message["usage"] = usage
            recovered.append({
                "type": "message",
                "message": assistant_message,
            })

    completed = runtime_checkpoint.get("completed_tool_results") or []
    if isinstance(completed, list):
        for msg in completed:
            tid = msg.get("tool_call_id", "")
            name, inp = pending.pop(tid, (msg.get("name", "exec"), ""))
            recovered.append({
                "type": "tool_call",
                "name": name,
                "input": inp,
                "output": str(msg.get("content", "")),
            })

    return recovered


def _persist_transcript(
    agent_id: str,
    session_id: str,
    transcript: list,
) -> Path:
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


# ── sub-commands ──────────────────────────────────────────────────────────────

def cmd_agents_list() -> int:
    if not BENCH_AGENTS_DIR.exists():
        return 0
    for d in sorted(BENCH_AGENTS_DIR.iterdir()):
        if d.is_dir():
            cfg = _load_config(d.name)
            print(f"- {d.name}")
            ws = cfg.get("workspace", "")
            if ws:
                print(f"  Workspace: {ws}")
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
    """Run a judge turn using the OpenAI client directly (no tools, pure JSON)."""
    from openai import OpenAI

    # Load prior judge conversation state
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

    judge_model = _first_env(
        "NANOBOT_BENCH_JUDGE_MODEL", "JUDGE_MODEL",
        "NANOBOT_BENCH_MODEL", "MODEL",
    ) or MODEL_NAME
    judge_base = _first_env(
        "NANOBOT_BENCH_JUDGE_API_BASE_URL", "JUDGE_API_BASE_URL",
        "NANOBOT_BENCH_API_BASE_URL", "API_BASE_URL",
    ) or VLLM_BASE
    judge_key = _first_env(
        "NANOBOT_BENCH_JUDGE_API_KEY", "JUDGE_API_KEY",
        "NANOBOT_BENCH_API_KEY", "API_KEY",
    ) or "dummy"

    client = OpenAI(api_key=judge_key, base_url=judge_base)
    try:
        kwargs = dict(
            model=judge_model,
            messages=messages,
            max_tokens=4096,
            temperature=0.0,
        )
        if "127.0.0.1" in judge_base or "localhost" in judge_base:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        resp = client.chat.completions.create(**kwargs)
        reply = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"[shim judge error] {e}", file=sys.stderr)
        reply = "{}"

    messages.append({"role": "assistant", "content": reply})
    state_path.write_text(json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")
    _persist_transcript(
        agent_id,
        session_id,
        [
            {
                "type": "message",
                "message": {"role": "user", "content": _oc_content_text(message)},
            },
            {
                "type": "message",
                "message": {"role": "assistant", "content": _oc_content_text(reply)},
            },
        ],
    )
    print(reply)
    return 0


def cmd_agent_run(argv: list) -> int:
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
        return _run_judge(agent_id, message, session_id)

    # ── task agent: delegate to nanobot native agent ──────────────────────────
    workspace_cwd = os.getcwd()

    # Create a bootstrap file so nanobot disables Qwen3 thinking mode
    bootstrap_dir = Path(workspace_cwd) / "bootstrap"
    bootstrap_dir.mkdir(exist_ok=True)
    (bootstrap_dir / "no_think.md").write_text("/no_think\n")

    # nanobot session key: "cli:<session_id>" → stored as "cli_<session_id>.jsonl"
    nanobot_session_key = f"cli:{session_id}"
    safe_key = _safe_filename(nanobot_session_key.replace(":", "_"))
    nanobot_session_path = Path(workspace_cwd) / "sessions" / f"{safe_key}.jsonl"

    env = os.environ.copy()
    # Ensure no terminal interference
    env.pop("TERM", None)
    env["PYTHONIOENCODING"] = "utf-8"

    # Nanobot timeout must be shorter than the benchmark task timeout so the
    # shim always gets to call _persist_transcript before being killed.
    # start_new_session=True puts nanobot in its own process group so we can kill
    # the entire group (including any grandchildren) on timeout, preventing orphan
    # processes from accumulating and overloading vLLM.
    # The benchmark runner passes NANOBOT_TIMEOUT as
    # task.timeout_seconds * timeout_multiplier - 2. Honor that value for slow
    # API models instead of clamping to the old 180s default.
    # - too high risks shim being SIGKILLed first (orphan risk),
    # - too low can cut off long SRO runs after analysis but before write_file/report turns.
    try:
        configured_timeout = int(os.environ.get("NANOBOT_TIMEOUT", "178"))
    except ValueError:
        configured_timeout = 178
    NANOBOT_TIMEOUT = max(30, configured_timeout)

    # ── Inline Library Execution ──
    import asyncio
    
    def _get_nanobot_path() -> str:
        env_path = os.environ.get("NANOBOT_SOURCE_PATH", "").strip()
        if env_path and Path(env_path).exists():
            return env_path
        for p in ("/data1/lzd/nanobot", "/data/lzd/nanobot"):
            if Path(p).exists():
                return p
        return "/data/lzd/nanobot"
        
    nanobot_pkg = _get_nanobot_path()
    if nanobot_pkg not in sys.path:
        sys.path.insert(0, nanobot_pkg)
    
    import inspect

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.cli.commands import _load_runtime_config, _make_provider
    from nanobot.session.manager import SessionManager
    from nanobot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE
    
    import nanobot.utils.helpers
    import nanobot.agent.memory
    
    def _heuristic_tokens(obj):
        # Length-based heuristic for Qwen (mixed EN/CN ~3 chars/token)
        # Avoids loading slow HF tokenizer or triggering tiktoken network fetch
        text = json.dumps(obj, ensure_ascii=False)
        return max(4, len(text) // 3)

    def _mock_estimate_prompt_tokens(messages, tools=None):
        return _heuristic_tokens({"m": messages, "t": tools})

    def _mock_estimate_message_tokens(message):
        return _heuristic_tokens(message)

    def _mock_estimate_chain(*args, **kwargs):
        msgs = args[2] if len(args) > 2 else kwargs.get("messages", [])
        tls = args[3] if len(args) > 3 else kwargs.get("tools", None)
        return (_mock_estimate_prompt_tokens(msgs, tls), "heuristic")
    
    nanobot.utils.helpers.estimate_prompt_tokens = _mock_estimate_prompt_tokens
    nanobot.utils.helpers.estimate_message_tokens = _mock_estimate_message_tokens
    nanobot.agent.memory.estimate_message_tokens = _mock_estimate_message_tokens
    nanobot.agent.memory.estimate_prompt_tokens_chain = _mock_estimate_chain

    def _first_env(*names: str) -> str:
        for name in names:
            value = os.environ.get(name, "").strip()
            if value:
                return value
        return ""

    def _apply_bench_provider_overrides(runtime_config):
        """Allow benchmark runs to switch OpenAI-compatible backends via env.

        Defaults stay in nanobot_bench_config.json for qwen35-local, but new
        model/API experiments should not require editing that shared file.
        """
        model = _first_env("NANOBOT_BENCH_MODEL", "MODEL")
        api_base = _first_env("NANOBOT_BENCH_API_BASE_URL", "API_BASE_URL")
        api_key = _first_env("NANOBOT_BENCH_API_KEY", "API_KEY")
        max_tokens = _first_env("NANOBOT_BENCH_MAX_TOKENS", "MAX_TOKENS")
        context_window = _first_env("NANOBOT_BENCH_CONTEXT_WINDOW_TOKENS", "CONTEXT_WINDOW_TOKENS")

        if model:
            runtime_config.agents.defaults.model = model
        if api_base:
            runtime_config.agents.defaults.provider = "custom"
            runtime_config.providers.custom.api_base = api_base
        if api_key:
            runtime_config.agents.defaults.provider = "custom"
            runtime_config.providers.custom.api_key = api_key
        if max_tokens:
            try:
                runtime_config.agents.defaults.max_tokens = int(max_tokens)
            except ValueError:
                print(f"[shim warning] invalid MAX_TOKENS={max_tokens!r}", file=sys.stderr)
        if context_window:
            try:
                runtime_config.agents.defaults.context_window_tokens = int(context_window)
            except ValueError:
                print(f"[shim warning] invalid CONTEXT_WINDOW_TOKENS={context_window!r}", file=sys.stderr)

        if model or api_base or api_key:
            safe_base = runtime_config.providers.custom.api_base or ""
            print(
                f"[shim config] model={runtime_config.agents.defaults.model} "
                f"provider={runtime_config.agents.defaults.provider} api_base={safe_base}",
                file=sys.stderr,
            )
        return runtime_config
    
    async def _run_inline():
        runtime_config = _load_runtime_config(str(NANOBOT_CONFIG), workspace_cwd)
        runtime_config = _apply_bench_provider_overrides(runtime_config)
        provider = _make_provider(runtime_config)
        bus = MessageBus()
        session_manager = SessionManager(Path(workspace_cwd))
        
        loop_kwargs = {
            "bus": bus,
            "provider": provider,
            "workspace": Path(workspace_cwd),
            "model": runtime_config.agents.defaults.model,
            "max_iterations": runtime_config.agents.defaults.max_tool_iterations,
            "context_window_tokens": runtime_config.agents.defaults.context_window_tokens,
            "context_block_limit": runtime_config.agents.defaults.context_block_limit,
            "max_tool_result_chars": runtime_config.agents.defaults.max_tool_result_chars,
            "provider_retry_mode": runtime_config.agents.defaults.provider_retry_mode,
            "exec_config": runtime_config.tools.exec,
            "restrict_to_workspace": runtime_config.tools.restrict_to_workspace,
            "session_manager": session_manager,
            "mcp_servers": runtime_config.tools.mcp_servers,
            "channels_config": runtime_config.channels,
            "timezone": runtime_config.agents.defaults.timezone,
        }
        loop_params = inspect.signature(AgentLoop.__init__).parameters
        if "web_config" in loop_params:
            loop_kwargs["web_config"] = runtime_config.tools.web
        elif "web_search_config" in loop_params:
            loop_kwargs["web_search_config"] = runtime_config.tools.web.search
            loop_kwargs["web_proxy"] = runtime_config.tools.web.proxy or None

        agent_loop = AgentLoop(**loop_kwargs)
        
        try:
            resp = await agent_loop.process_direct(
                message,
                session_key=nanobot_session_key,
                channel="cli",
                chat_id="direct",
                on_progress=None,
                on_stream=None,
                on_stream_end=None,
            )
            final_resp = resp.content if resp else ""
            if not final_resp:
                final_resp = EMPTY_FINAL_RESPONSE_MESSAGE
            return {
                "final_response": final_resp,
                "usage": dict(getattr(agent_loop, "_last_usage", {}) or {}),
            }
        finally:
            await agent_loop.close_mcp()

    def _on_sigterm(_signum, _frame):
        # We raise an exception so wait_for inside asyncio stops and we can persist transcript
        raise KeyboardInterrupt()

    prev_term = signal.signal(signal.SIGTERM, _on_sigterm)
    try:
        final_response = asyncio.run(asyncio.wait_for(_run_inline(), timeout=NANOBOT_TIMEOUT))
    except asyncio.TimeoutError:
        final_response = "[agent timed out]"
    except KeyboardInterrupt:
        final_response = "[agent interrupted]"
    except Exception as e:
        final_response = f"[shim error] {e}"
        print(f"[shim error] {e}", file=sys.stderr)
    finally:
        signal.signal(signal.SIGTERM, prev_term)

    # Convert nanobot session to openclaw transcript format
    transcript = _nanobot_to_openclaw(nanobot_session_path, user_message=message)

    # Persist transcript to openclaw paths + history dir
    _persist_transcript(agent_id, session_id, transcript)

    print(final_response)
    return 0


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        print("openclaw shim — use: agents list|add|delete  or  agent ...", file=sys.stderr)
        sys.exit(1)

    if argv[0] == "--version":
        print("openclaw 0.1.0-shim")
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
