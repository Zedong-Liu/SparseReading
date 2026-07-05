from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
INSTALLER_PATH = ROOT / "scripts" / "install_sparseread.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("install_sparseread_for_test", INSTALLER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
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
        ["C:/node/npm.cmd", "ci", "--ignore-scripts"],
        ["C:/node/npm.cmd", "run", "build"],
    ]
