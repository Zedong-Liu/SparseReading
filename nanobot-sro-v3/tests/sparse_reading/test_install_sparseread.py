from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALLER_PATH = ROOT / "scripts" / "install_sparseread.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("install_sparseread_for_test", INSTALLER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_require_command_falls_back_to_windows_cmd_suffix(monkeypatch, tmp_path: Path) -> None:
    installer = load_installer()
    npm_cmd = tmp_path / "npm.cmd"
    npm_cmd.write_text("@echo off\n", encoding="utf-8")
    npm_cmd.chmod(0o755)

    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(installer.os, "name", "nt")

    assert Path(installer.require_command("npm")).name == "npm.cmd"


def test_npm_install_and_build_uses_resolved_command(monkeypatch, tmp_path: Path) -> None:
    installer = load_installer()
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(installer, "require_command", lambda *_args, **_kwargs: "C:/node/npm.cmd")

    def fake_run(cmd: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(installer, "run", fake_run)

    installer.npm_install_and_build(tmp_path, dry_run=False)

    assert calls == [
        ["cmd.exe", "/d", "/s", "/c", "C:/node/npm.cmd ci --ignore-scripts"],
        ["cmd.exe", "/d", "/s", "/c", "C:/node/npm.cmd run build"],
    ]


def test_bridge_command_keeps_appendable_prefix_for_plugin() -> None:
    installer = load_installer()
    spec = installer.CommandSpec("C:/Users/me/bin/uv.cmd")

    command = installer.bridge_command(spec)

    assert command[0] == "C:/Users/me/bin/uv.cmd"
    assert "-m" not in command


def test_bridge_invocation_wraps_cmd_after_full_args() -> None:
    installer = load_installer()
    spec = installer.CommandSpec("C:/Users/me/bin/uv.cmd")

    command = installer.bridge_invocation(spec, "-m", "sparseread.bridge.openclaw")

    assert command == [
        "cmd.exe",
        "/d",
        "/s",
        "/c",
        "C:/Users/me/bin/uv.cmd --project "
        + str(installer.CORE)
        + " run --with pymupdf python -m sparseread.bridge.openclaw",
    ]


def test_public_install_mode_auto_maps_to_internal_defaults() -> None:
    installer = load_installer()

    profile = installer.install_profile(SimpleNamespace(sparseread_mode="auto"))

    assert profile.policy == "auto"
    assert profile.mode == "auto"
    assert profile.openclaw_hook_mode == "enforce"


def test_public_install_mode_advisory_disables_openclaw_interception() -> None:
    installer = load_installer()

    profile = installer.install_profile(SimpleNamespace(sparseread_mode="advisory"))

    assert profile.policy == "advisory"
    assert profile.mode == "auto"
    assert profile.openclaw_hook_mode == "prompt"


def test_installer_help_only_exposes_public_sparse_read_mode() -> None:
    proc = subprocess.run(
        [sys.executable, str(INSTALLER_PATH), "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert "--sparseread-mode" in proc.stdout
    assert "--policy" not in proc.stdout
    assert "--openclaw-hook-mode" not in proc.stdout
    assert " --mode " not in proc.stdout


def test_install_opencode_writes_persistent_workspace_config(monkeypatch, tmp_path: Path) -> None:
    installer = load_installer()
    workspace = tmp_path / "workspace"

    monkeypatch.setattr(installer, "command_spec", lambda *_args, **_kwargs: installer.CommandSpec("/bin/true"))
    monkeypatch.setattr(installer, "npm_install_and_build", lambda *_args, **_kwargs: None)

    installer.install_opencode(
        SimpleNamespace(
            opencode_workspace=str(workspace),
            opencode_cmd="opencode",
            sparseread_mode="auto",
            skip_build=True,
            dry_run=False,
        )
    )

    plugin_target, config_target = installer.opencode_workspace_paths(workspace.resolve())
    config = json.loads(config_target.read_text(encoding="utf-8"))

    assert plugin_target.exists()
    assert config["projectRoot"] == str(installer.ROOT)
    assert config["bridgeModule"] == "sparseread.bridge.opencode"
    assert config["policy"] == "auto"
    assert config["mode"] == "auto"
    assert isinstance(config["bridgeCommand"], list)
    installer.validate_opencode_workspace(workspace.resolve())
    assert not (workspace / ".opencode" / "sparseread.env").exists()


def test_openclaw_install_patch_defaults_enforce_hook_mode(monkeypatch, tmp_path: Path) -> None:
    installer = load_installer()
    calls: list[list[str]] = []
    patches: list[dict] = []

    monkeypatch.setattr(installer, "command_spec", lambda *_args, **_kwargs: installer.CommandSpec("/bin/true"))
    monkeypatch.setattr(installer, "npm_install_and_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(installer, "normalized_openclaw_load_paths", lambda _profile: [str(installer.OPENCLAW_PLUGIN)])

    def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[-3:] == ["config", "patch", "--stdin"]:
            patches.append(json.loads(kwargs["input_text"]))
        if cmd[-5:] == ["plugins", "inspect", "sparseread-openclaw", "--runtime", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps(
                    {
                        "status": "loaded",
                        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
                        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
                        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
                        "hookCount": 5,
                        "typedHooks": [
                            {"name": "before_prompt_build"},
                            {"name": "before_tool_call"},
                            {"name": "after_tool_call"},
                            {"name": "llm_output"},
                            {"name": "agent_end"},
                        ],
                    }
                ),
                "",
            )
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(installer, "run", fake_run)

    installer.install_openclaw(
        SimpleNamespace(
            openclaw_cmd="openclaw",
            openclaw_profile="",
            openclaw_workspace=str(tmp_path),
            sparseread_mode="auto",
            skip_build=True,
            dry_run=False,
        )
    )

    load_paths_patch = patches[0]["plugins"]["load"]["paths"]
    entry = patches[1]["plugins"]["entries"]["sparseread-openclaw"]
    config = entry["config"]
    assert load_paths_patch == [str(installer.OPENCLAW_PLUGIN)]
    assert config["hookMode"] == "enforce"
    assert entry["hooks"]["allowPromptInjection"] is True
    assert entry["hooks"]["allowConversationAccess"] is True
    assert any(cmd[-2:] == ["sparseread-openclaw", "--force"] for cmd in calls)
    assert any(cmd[-3:] == ["registry", "--refresh", "--json"] for cmd in calls)


def test_openclaw_install_patch_advisory_mode_has_no_before_tool_call_policy(monkeypatch, tmp_path: Path) -> None:
    installer = load_installer()
    patches: list[dict] = []

    monkeypatch.setattr(installer, "command_spec", lambda *_args, **_kwargs: installer.CommandSpec("/bin/true"))
    monkeypatch.setattr(installer, "npm_install_and_build", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(installer, "normalized_openclaw_load_paths", lambda _profile: [str(installer.OPENCLAW_PLUGIN)])

    def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if cmd[-3:] == ["config", "patch", "--stdin"]:
            patches.append(json.loads(kwargs["input_text"]))
        if cmd[-5:] == ["plugins", "inspect", "sparseread-openclaw", "--runtime", "--json"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                json.dumps(
                    {
                        "status": "loaded",
                        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
                        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
                        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
                        "hookCount": 2,
                        "hookNames": ["before_prompt_build", "agent_end"],
                    }
                ),
                "",
            )
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(installer, "run", fake_run)

    installer.install_openclaw(
        SimpleNamespace(
            openclaw_cmd="openclaw",
            openclaw_profile="",
            openclaw_workspace=str(tmp_path),
            sparseread_mode="advisory",
            skip_build=True,
            dry_run=False,
        )
    )

    entry = patches[1]["plugins"]["entries"]["sparseread-openclaw"]
    assert entry["config"]["policy"] == "advisory"
    assert entry["config"]["mode"] == "auto"
    assert entry["config"]["hookMode"] == "prompt"
    assert entry["hooks"] == {"allowPromptInjection": True, "allowConversationAccess": True}


def test_validate_openclaw_runtime_accepts_hookless_production_payload() -> None:
    installer = load_installer()
    payload = {
        "status": "loaded",
        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
        "hookCount": 0,
        "hookNames": [],
    }

    installer.validate_openclaw_runtime(json.dumps(payload), hook_mode="off")


def test_validate_openclaw_runtime_accepts_prompt_only_payload() -> None:
    installer = load_installer()
    payload = {
        "status": "loaded",
        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
        "hookCount": 1,
        "hookNames": ["before_prompt_build"],
    }

    installer.validate_openclaw_runtime(json.dumps(payload), hook_mode="prompt")


def test_validate_openclaw_runtime_accepts_enforce_payload() -> None:
    installer = load_installer()
    payload = {
        "status": "loaded",
        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
        "hookCount": 3,
        "typedHooks": [
            {"name": "before_prompt_build"},
            {"name": "before_tool_call"},
            {"name": "after_tool_call"},
        ],
    }

    installer.validate_openclaw_runtime(json.dumps(payload), hook_mode="enforce")


def test_validate_openclaw_runtime_rejects_enforce_without_before_tool_call() -> None:
    installer = load_installer()
    payload = {
        "status": "loaded",
        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
        "hookCount": 2,
        "typedHooks": [{"name": "before_prompt_build"}, {"name": "after_tool_call"}],
    }

    try:
        installer.validate_openclaw_runtime(json.dumps(payload), hook_mode="enforce")
    except SystemExit as exc:
        assert "must register before_tool_call" in str(exc)
    else:
        raise AssertionError("expected enforce mode without before_tool_call to fail doctor")


def test_validate_openclaw_runtime_rejects_prompt_native_lifecycle_hooks() -> None:
    installer = load_installer()
    payload = {
        "status": "loaded",
        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
        "hookCount": 2,
        "hookNames": ["before_prompt_build", "before_tool_call"],
    }

    try:
        installer.validate_openclaw_runtime(json.dumps(payload), hook_mode="prompt")
    except SystemExit as exc:
        assert "must not register native tool lifecycle hooks" in str(exc)
    else:
        raise AssertionError("expected prompt mode native lifecycle hook to fail doctor")


def test_validate_openclaw_runtime_rejects_duplicate_sparse_read_plugins() -> None:
    installer = load_installer()
    payload = {
        "status": "loaded",
        "plugin": {"rootDir": str(installer.OPENCLAW_PLUGIN)},
        "install": {"sourcePath": str(installer.OPENCLAW_PLUGIN)},
        "toolNames": list(installer.OPENCLAW_RUNTIME_TOOLS),
        "hookCount": 0,
        "hookNames": [],
        "diagnostics": [{"message": "duplicate plugin id resolved by explicit config-selected plugin"}],
    }

    try:
        installer.validate_openclaw_runtime(json.dumps(payload), hook_mode="off")
    except SystemExit as exc:
        assert "duplicate SparseRead plugins" in str(exc)
    else:
        raise AssertionError("expected duplicate plugin diagnostic to fail doctor")
