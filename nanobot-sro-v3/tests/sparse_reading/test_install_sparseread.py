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


def test_openclaw_install_patch_defaults_hook_mode_off(monkeypatch, tmp_path: Path) -> None:
    installer = load_installer()
    patches: list[dict] = []

    monkeypatch.setattr(installer, "command_spec", lambda *_args, **_kwargs: installer.CommandSpec("/bin/true"))
    monkeypatch.setattr(installer, "npm_install_and_build", lambda *_args, **_kwargs: None)

    def fake_run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        if cmd[-3:] == ["config", "patch", "--stdin"]:
            patches.append(json.loads(kwargs["input_text"]))
        return subprocess.CompletedProcess(cmd, 0, "{}", "")

    monkeypatch.setattr(installer, "run", fake_run)

    installer.install_openclaw(
        SimpleNamespace(
            openclaw_cmd="openclaw",
            openclaw_profile="",
            openclaw_workspace=str(tmp_path),
            policy="auto",
            mode="auto",
            openclaw_hook_mode="off",
            skip_build=True,
            dry_run=False,
        )
    )

    config = patches[0]["plugins"]["entries"]["sparseread-openclaw"]["config"]
    assert config["hookMode"] == "off"
