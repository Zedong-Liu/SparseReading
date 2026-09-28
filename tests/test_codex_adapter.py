from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

from sparseread_agent_tools.codex_hook import (
    _parse_unbounded_cat,
    handle_event,
    run_hook,
)

LAUNCHER = (
    Path(__file__).resolve().parents[1]
    / "integrations"
    / "codex"
    / "plugin"
    / "sparseread-codex"
    / "scripts"
    / "launcher.mjs"
)
PLUGIN_ROOT = LAUNCHER.parent.parent


def _event(command: str, workspace: Path, *, session_id: str = "session-1") -> dict[str, Any]:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "turn_id": "turn-1",
        "cwd": str(workspace),
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }


def _force_result() -> dict[str, Any]:
    return {
        "host_gate": {
            "mode": "enforce",
            "block_native_read": True,
            "block_native_exec_dump": True,
            "nudge_native": True,
            "reason": "large text document",
        }
    }


class _FakeBridge:
    def __init__(
        self,
        result: dict[str, Any],
        *,
        calls: list[dict[str, Any]] | None = None,
        barrier: Barrier | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.calls = calls
        self.barrier = barrier
        self.error = error

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.calls is not None:
            self.calls.append(request)
        if self.barrier is not None:
            self.barrier.wait(timeout=3)
        if self.error is not None:
            raise self.error
        return self.result


def _factory(result: dict[str, Any], **kwargs: Any):
    return lambda **_bridge_kwargs: _FakeBridge(result, **kwargs)


def test_parse_only_plain_unbounded_cat_paths() -> None:
    assert _parse_unbounded_cat('cat "reports/quarterly results.md"') == "reports/quarterly results.md"
    assert _parse_unbounded_cat("/bin/cat ./events.log") == "./events.log"
    for command in (
        "head -n 5 report.md",
        "tail -n 5 report.md",
        "sed -n '1,5p' report.md",
        "rg ERROR report.md",
        "grep ERROR report.md",
        "cat report.md | sed -n '1,5p'",
        "cat report.md; echo done",
        "cat report.md > copy.md",
        "cat one.md two.md",
        "cat",
        "cat 'unterminated path.md",
    ):
        assert _parse_unbounded_cat(command) is None


def test_malformed_hook_stdin_fails_open(tmp_path: Path) -> None:
    assert run_hook("{not-json", workspace=tmp_path) is None
    assert run_hook("[]", workspace=tmp_path) is None


def test_hook_resolves_a_path_with_spaces_inside_workspace(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    target = tmp_path / "quarterly results.md"
    target.write_text("evidence", encoding="utf-8")

    response = handle_event(
        _event('cat "quarterly results.md"', tmp_path),
        workspace=tmp_path,
        plugin_data=tmp_path / "plugin data",
        bridge_factory=_factory(_force_result(), calls=calls),
    )

    assert response is not None
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    request = calls[0]
    assert request["method"] == "decide"
    assert request["params"]["path"] == str(target.resolve())
    assert request["params"]["context"]["session_id"] == "session-1"


def test_force_gate_redirects_a_path_only_once(tmp_path: Path) -> None:
    target = tmp_path / "large.md"
    target.write_text("evidence", encoding="utf-8")
    kwargs = {
        "workspace": tmp_path,
        "plugin_data": tmp_path / "plugin-data",
        "bridge_factory": _factory(_force_result()),
    }
    event = _event("cat large.md", tmp_path)

    first = handle_event(event, **kwargs)
    retry = handle_event(event, **kwargs)
    later_repeat = handle_event(event, **kwargs)

    assert first is not None
    assert first["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "sro_preview" in first["hookSpecificOutput"]["additionalContext"]
    assert retry is None
    assert later_repeat is None


def test_concurrent_redirect_attempts_share_one_locked_claim(tmp_path: Path) -> None:
    target = tmp_path / "large.md"
    target.write_text("evidence", encoding="utf-8")
    barrier = Barrier(2)
    factory = _factory(_force_result(), barrier=barrier)
    kwargs = {
        "workspace": tmp_path,
        "plugin_data": tmp_path / "plugin-data",
        "bridge_factory": factory,
    }
    event = _event("cat large.md", tmp_path)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: handle_event(event, **kwargs), range(2)))

    denied = [response for response in responses if response is not None]
    assert len(denied) == 1
    assert denied[0]["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_advisory_mode_adds_context_without_blocking(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text("evidence", encoding="utf-8")
    response = handle_event(
        _event("cat report.md", tmp_path),
        workspace=tmp_path,
        mode="advisory",
        plugin_data=tmp_path / "plugin-data",
        bridge_factory=_factory(_force_result()),
    )

    assert response is not None
    hook_output = response["hookSpecificOutput"]
    assert "permissionDecision" not in hook_output
    assert "native command remains available" in hook_output["additionalContext"]


def test_non_enforcing_host_gate_never_denies(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text("evidence", encoding="utf-8")
    advisory = {
        "host_gate": {
            "mode": "advisory",
            "block_native_read": False,
            "block_native_exec_dump": False,
            "nudge_native": True,
        }
    }

    response = handle_event(
        _event("cat report.md", tmp_path),
        workspace=tmp_path,
        plugin_data=tmp_path / "plugin-data",
        bridge_factory=_factory(advisory),
    )

    assert response is not None
    assert "permissionDecision" not in response["hookSpecificOutput"]


def test_bridge_and_workspace_failures_use_native_fallback(tmp_path: Path) -> None:
    event = _event("cat report.md", tmp_path)
    broken_bridge = handle_event(
        event,
        workspace=tmp_path,
        plugin_data=tmp_path / "plugin-data",
        bridge_factory=_factory({}, error=RuntimeError("bridge unavailable")),
    )
    outside_workspace = handle_event(
        _event("cat ../outside.md", tmp_path),
        workspace=tmp_path,
        plugin_data=tmp_path / "plugin-data",
        bridge_factory=_factory(_force_result()),
    )

    assert broken_bridge is None
    assert outside_workspace is None


def test_malformed_redirect_state_fails_open(tmp_path: Path) -> None:
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()
    (plugin_data / "codex-hook-state.json").write_text("not-json", encoding="utf-8")
    target = tmp_path / "report.md"
    target.write_text("evidence", encoding="utf-8")

    response = handle_event(
        _event("cat report.md", tmp_path),
        workspace=tmp_path,
        plugin_data=plugin_data,
        bridge_factory=_factory(_force_result()),
    )

    assert response is None


def _copy_launcher(tmp_path: Path) -> tuple[Path, Path]:
    plugin_root = tmp_path / "plugin with spaces"
    scripts = plugin_root / "scripts"
    scripts.mkdir(parents=True)
    launcher = scripts / "launcher.mjs"
    shutil.copy2(LAUNCHER, launcher)
    return plugin_root, launcher


def _runtime_config(plugin_root: Path, workspace: Path) -> None:
    (plugin_root / ".sparseread-runtime.json").write_text(
        json.dumps(
            {
                "python": sys.executable,
                "workspace": str(workspace.resolve()),
                "mode": "auto",
                "protocol": "1.0",
            }
        ),
        encoding="utf-8",
    )


def test_codex_plugin_version_tracks_shared_adapter() -> None:
    plugin_manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    adapter_metadata = tomllib.loads(
        (PLUGIN_ROOT.parents[2] / "agent-tools" / "python" / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert plugin_manifest["version"] == adapter_metadata["project"]["version"] == "0.1.3"
    assert plugin_manifest["interface"]["defaultPrompt"]


def test_launcher_missing_runtime_is_silent_for_hooks_and_session_start(tmp_path: Path) -> None:
    _plugin_root, launcher = _copy_launcher(tmp_path)

    hook = subprocess.run(
        ["node", str(launcher), "--hook"],
        input=json.dumps(_event("cat report.md", tmp_path)),
        capture_output=True,
        text=True,
        check=False,
    )
    session = subprocess.run(
        ["node", str(launcher), "--session-start"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert hook.returncode == 0
    assert hook.stdout == "{}\n"
    assert session.returncode == 0
    assert session.stdout == "{}\n"


def test_launcher_hook_timeout_fails_open(tmp_path: Path) -> None:
    plugin_root, launcher = _copy_launcher(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _runtime_config(plugin_root, workspace)
    fake_package = tmp_path / "slow-python-package" / "sparseread_agent_tools"
    fake_package.mkdir(parents=True)
    (fake_package / "__init__.py").write_text("", encoding="utf-8")
    (fake_package / "codex_hook.py").write_text(
        "import time\ntime.sleep(10)\nprint('{\\\"blocked\\\":true}')\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(fake_package.parent)
    start = time.monotonic()

    result = subprocess.run(
        ["node", str(launcher), "--hook"],
        input=json.dumps(_event("cat report.md", workspace)),
        capture_output=True,
        text=True,
        env=environment,
        timeout=8,
        check=False,
    )

    elapsed = time.monotonic() - start
    assert result.returncode == 0
    assert result.stdout == "{}\n"
    assert elapsed < 6
