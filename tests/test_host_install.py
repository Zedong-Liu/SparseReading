from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_install_sparseread import load_installer


def args(workspace, platform="codex", dry_run=False):
    return SimpleNamespace(workspace=str(workspace), platform=platform, python="python3",
                           dry_run=dry_run, sparseread_mode="auto", reader_extras="none",
                           codex_cmd="codex", pi_cmd="pi")


def test_codex_preserves_marketplace_and_toml(tmp_path):
    installer = load_installer()
    market = tmp_path / ".agents/plugins/marketplace.json"
    market.parent.mkdir(parents=True)
    original = {"name": "existing", "plugins": [{"name": "another", "source": "./other"}], "custom": 7}
    market.write_text(json.dumps(original))
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    content = '# User comment\nmodel = "some-model"\n[features]\nother = true\n'
    config.write_text(content)
    payload, merged, key = installer.codex_project_payloads(tmp_path)
    assert payload["plugins"][0] == original["plugins"][0]
    assert payload["custom"] == 7
    assert payload["name"] == "existing"
    assert merged.startswith(content)
    assert tomllib.loads(merged)["plugins"][key]["enabled"] is True
    assert config.read_text() == content  # preparation must not mutate


def test_codex_preserves_disabled_setting(tmp_path):
    installer = load_installer()
    payload, merged, key = installer.codex_project_payloads(tmp_path)
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    disabled = merged.replace("enabled = true", "enabled = false")
    config.write_text(disabled)
    _, second, _ = installer.codex_project_payloads(tmp_path)
    assert second == disabled


def test_codex_refuses_foreign_entry_and_invalid_config(tmp_path):
    installer = load_installer()
    config = tmp_path / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text("invalid = [")
    with pytest.raises(SystemExit, match="invalid Codex"):
        installer.validate_host_destination(args(tmp_path))
    config.write_text("")
    market = tmp_path / ".agents/plugins/marketplace.json"
    market.parent.mkdir(parents=True)
    market.write_text(json.dumps({"name": "other", "plugins": [{"name": "sparseread-codex", "source": "./foreign"}]}))
    with pytest.raises(SystemExit, match="refusing"):
        installer.validate_host_destination(args(tmp_path))


@pytest.mark.parametrize("platform", ["codex", "pi"])
def test_dry_run_no_mutation_or_python_build(tmp_path, monkeypatch, platform):
    installer = load_installer()
    calls = []
    monkeypatch.setattr(installer, "install_python_runtime", lambda runtime, adapter, **kw: calls.append(kw) or runtime / "bin/python")
    monkeypatch.setattr(installer, "require_command", lambda name, **kw: name)
    monkeypatch.setattr(installer, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    installer.install_host(args(tmp_path, platform, True))
    assert calls[0]["dry_run"] is True
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("platform", ["codex", "pi"])
def test_managed_copy_and_retry_retain_old_runtime(tmp_path, monkeypatch, platform):
    installer = load_installer()
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.json").write_text("{}")
    (source / ".codex-plugin").mkdir()
    (source / ".codex-plugin/plugin.json").write_text('{"name":"sparseread-codex","version":"0.1.1"}')
    (source / "hooks").mkdir()
    (source / "hooks/hooks.json").write_text('{"hooks":{"PreToolUse":[{"hooks":[{"type":"command","command":"node old"}]}]}}')
    (source / "node_modules").mkdir()
    (source / "node_modules/not-distributable").write_text("ignore")
    monkeypatch.setattr(installer, "CODEX_PLUGIN", source)
    monkeypatch.setattr(installer, "PI_PLUGIN", source)
    monkeypatch.setattr(installer, "install_python_runtime", lambda runtime, adapter, **kw: runtime / "bin/python")
    monkeypatch.setattr(installer, "require_command", lambda name, **kw: name)
    commands = []
    monkeypatch.setattr(installer, "run", lambda cmd, **kw: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    options = args(tmp_path, platform)
    installer.install_host(options)
    target = tmp_path / ".sparseread" / platform / f"sparseread-{platform}"
    first = json.loads((target / ".sparseread-runtime.json").read_text())
    installer.install_host(options)
    second = json.loads((target / ".sparseread-runtime.json").read_text())
    assert first["python"] != second["python"]
    assert not (target / "node_modules").exists()
    assert first["workspace"] == str(tmp_path)
    assert second["protocol"] == "1.0"
    if platform == "pi":
        assert commands == [["pi", "install", "--local", str(target)]] * 2
    else:
        manifest = json.loads((target / ".codex-plugin/plugin.json").read_text())
        assert manifest["version"].startswith("0.1.1+local.")
        hook = json.loads((target / "hooks/hooks.json").read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        assert str(target / "scripts/launcher.mjs") in hook["command"]
        mcp = json.loads((target / ".mcp.json").read_text())["mcpServers"]["sparseread"]
        assert mcp["command"] == "node"
        assert mcp["args"] == [str(target / "scripts/launcher.mjs"), "mcp"]
        assert "${" not in json.dumps(mcp)
        market = json.loads((tmp_path / ".agents/plugins/marketplace.json").read_text())
        assert len(market["plugins"]) == 1
        assert str(target) not in (tmp_path / ".codex/config.toml").read_text()


def test_build_failure_does_not_replace_active_config(tmp_path, monkeypatch):
    installer = load_installer()
    target = tmp_path / ".sparseread/codex/sparseread-codex"
    target.mkdir(parents=True)
    config = target / ".sparseread-runtime.json"
    config.write_text('{"python":"previous"}')
    def failure(*a, **kw):
        raise SystemExit("network error")
    monkeypatch.setattr(installer, "install_python_runtime", failure)
    with pytest.raises(SystemExit, match="network error"):
        installer.install_host(args(tmp_path))
    assert config.read_text() == '{"python":"previous"}'


def test_pi_project_approval_is_explicit_and_scoped(tmp_path, monkeypatch):
    installer = load_installer()
    monkeypatch.setattr(installer, "require_command", lambda name, **kw: name)
    monkeypatch.setattr(installer, "install_python_runtime", lambda runtime, adapter, **kw: runtime / "bin/python")
    commands = []
    monkeypatch.setattr(installer, "run", lambda cmd, **kw: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    options = args(tmp_path, "pi", True)
    options.pi_approve = True
    installer.install_host(options)
    assert commands[0][-1] == "--approve"
    assert commands[0][1:3] == ["install", "--local"]


def test_pi_untrusted_project_has_actionable_error_without_auto_approval(tmp_path, monkeypatch):
    installer = load_installer()
    monkeypatch.setattr(installer, "require_command", lambda name, **kw: name)
    monkeypatch.setattr(installer, "install_python_runtime", lambda runtime, adapter, **kw: runtime / "bin/python")
    commands = []
    def denied(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, "", "Project is not trusted. Use --approve")
    monkeypatch.setattr(installer, "run", denied)
    with pytest.raises(SystemExit, match="review|Review"):
        installer.install_host(args(tmp_path, "pi", True))
    assert "--approve" not in commands[0]


def test_invalid_pi_settings_rejected_before_runtime_creation(tmp_path):
    installer = load_installer()
    settings = tmp_path / ".pi/settings.json"
    settings.parent.mkdir()
    settings.write_text("{not-json}")
    with pytest.raises(SystemExit, match="Cannot merge Pi settings"):
        installer.validate_host_destination(args(tmp_path, "pi"))
    assert not (tmp_path / ".sparseread").exists()


@pytest.mark.parametrize("platform", ["codex", "pi"])
@pytest.mark.parametrize("location", ["parent", "plugin", "nested", "config"])
def test_symlink_destinations_rejected_before_any_build(tmp_path, monkeypatch, platform, location):
    installer = load_installer()
    workspace = tmp_path / "project"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "package.json"
    sentinel.write_text("user-owned")
    (outside / ".sparseread-runtime.json").write_text("{}")
    plugin = workspace / ".sparseread" / platform / f"sparseread-{platform}"
    if location == "parent":
        link = workspace / ".sparseread"
    elif location == "plugin":
        plugin.parent.mkdir(parents=True)
        link = plugin
    elif location == "config":
        link = workspace / (".codex" if platform == "codex" else ".pi")
    else:
        plugin.mkdir(parents=True)
        (plugin / ".sparseread-runtime.json").write_text("{}")
        link = plugin / "src"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are unavailable")
    calls = []
    monkeypatch.setattr(installer, "install_python_runtime", lambda *a, **kw: calls.append(a))
    with pytest.raises(SystemExit, match="symlink"):
        installer.install_host(args(workspace, platform))
    assert not calls
    assert sentinel.read_text() == "user-owned"
