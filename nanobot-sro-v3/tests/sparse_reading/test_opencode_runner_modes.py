from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = ROOT / "integrations" / "opencode" / "run_pilot.py"
PILOT_RUNNER_PATH = ROOT / "opencode_pilot" / "run_pilot.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_integrations_runner_exposes_production_auto_mode() -> None:
    runner = load_module(RUNNER_PATH, "opencode_run_pilot_for_test")

    assert "plugin_auto" in runner.MODES
    assert runner.opencode_policy("plugin_auto") == "auto"
    assert runner.opencode_policy("plugin_nudge") == "advisory"
    assert runner.opencode_policy("plugin_replace_truncation_experimental") == "enforce"


def test_pilot_runner_keeps_production_auto_alias() -> None:
    runner = load_module(PILOT_RUNNER_PATH, "opencode_pilot_run_pilot_for_test")

    assert "plugin_auto" in runner.MODES
    assert runner.opencode_policy("plugin_auto") == "auto"


def test_both_runners_write_workspace_sparse_read_config(tmp_path: Path) -> None:
    for module_path, module_name in [
        (RUNNER_PATH, "opencode_run_pilot_install_config_test"),
        (PILOT_RUNNER_PATH, "opencode_pilot_run_pilot_install_config_test"),
    ]:
        runner = load_module(module_path, module_name)
        run_dir = tmp_path / module_name
        (run_dir / "runtime").mkdir(parents=True)

        runner.install_plugin(run_dir)

        config = json.loads((run_dir / "runtime" / ".opencode" / "sparseread.json").read_text(encoding="utf-8"))
        assert config["projectRoot"] == str(runner.ROOT)
        assert config["python"] == sys.executable
        assert config["bridgeModule"] == "sparseread.bridge.opencode"
        assert config["mode"] == "auto"


def test_both_runners_pass_workspace_root_to_real_opencode(monkeypatch, tmp_path: Path) -> None:
    for module_path, module_name in [
        (RUNNER_PATH, "opencode_run_pilot_workspace_env_test"),
        (PILOT_RUNNER_PATH, "opencode_pilot_run_pilot_workspace_env_test"),
    ]:
        runner = load_module(module_path, module_name)
        run_dir = tmp_path / module_name
        runtime = run_dir / "runtime"
        runtime.mkdir(parents=True)
        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            captured["cmd"] = cmd
            captured["cwd"] = kwargs["cwd"]
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(runner, "task_prompt", lambda *_args, **_kwargs: "prompt")
        monkeypatch.setattr(runner, "collect_filesystem_trace", lambda *_args, **_kwargs: {
            "native_truncations": 0,
            "sro_calls": 0,
            "tool_calls": 0,
            "requests": 0,
            "tokens": 0,
            "ready_after_reads": 0,
        })
        monkeypatch.setattr(runner, "expected_deliverable_written", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(runner.subprocess, "run", fake_run)

        args = SimpleNamespace(
            python=sys.executable,
            bridge_command="",
            opencode_cmd='["npx","-y","opencode-ai"]',
            model="paratera/DeepSeek-V4-Flash",
            api_base_url="https://example.test/v1",
            timeout=1,
        )
        summary = runner.run_real_opencode(
            run_dir,
            "task_loogle_shortdep_fall_of_outremer_3q_followup",
            "plugin_auto",
            args,
        )

        env = captured["env"]
        assert isinstance(env, dict)
        assert captured["cwd"] == runtime.resolve()
        assert env["SPARSEREAD_PROJECT_ROOT"] == str(runner.ROOT.resolve())
        assert env["SPARSEREAD_WORKSPACE_ROOT"] == str(runtime.resolve())
        assert env["SPARSEREAD_POLICY"] == "auto"
        assert "--auto" in captured["cmd"]
        assert summary.status == "ok"


def test_both_runners_canonicalize_symlinked_workspace(monkeypatch, tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    runtime = physical / "runtime"
    runtime.mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.symlink_to(physical, target_is_directory=True)

    for module_path, module_name in [
        (RUNNER_PATH, "opencode_run_pilot_symlink_test"),
        (PILOT_RUNNER_PATH, "opencode_pilot_run_pilot_symlink_test"),
    ]:
        runner = load_module(module_path, module_name)
        captured: dict[str, object] = {}

        def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            captured["cmd"] = cmd
            captured["cwd"] = kwargs["cwd"]
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(runner, "task_prompt", lambda *_args, **_kwargs: "prompt")
        monkeypatch.setattr(runner, "collect_filesystem_trace", lambda *_args, **_kwargs: {
            "native_truncations": 0,
            "sro_calls": 0,
            "tool_calls": 0,
            "requests": 0,
            "tokens": 0,
            "ready_after_reads": 0,
        })
        monkeypatch.setattr(runner, "expected_deliverable_written", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(runner.subprocess, "run", fake_run)
        args = SimpleNamespace(
            python=sys.executable,
            bridge_command="",
            opencode_cmd='["npx","-y","opencode-ai@1.18.10"]',
            model="paratera/DeepSeek-V4-Flash",
            api_base_url="https://example.test/v1",
            timeout=1,
        )

        runner.run_real_opencode(
            linked,
            "task_loogle_shortdep_fall_of_outremer_3q_followup",
            "plugin_auto",
            args,
        )

        env = captured["env"]
        assert isinstance(env, dict)
        assert captured["cwd"] == runtime
        assert env["SPARSEREAD_WORKSPACE_ROOT"] == str(runtime)
