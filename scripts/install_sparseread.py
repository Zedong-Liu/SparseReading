#!/usr/bin/env python3
"""Install self-contained SparseRead adapters for existing agent CLIs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PACKAGE = ROOT / "packages" / "sparseread-core"
OPENCODE_ADAPTER = ROOT / "integrations" / "opencode" / "python"
OPENCLAW_ADAPTER = ROOT / "integrations" / "openclaw" / "python"
OPENCODE_PLUGIN = ROOT / "integrations" / "opencode" / "plugin"
OPENCLAW_PLUGIN = ROOT / "integrations" / "openclaw" / "plugin"
BRIDGE_PROTOCOL_VERSION = "1.0"
WINDOWS_COMMAND_SUFFIXES = (".cmd", ".exe", ".bat")
WINDOWS_SHELL_EXTS = {".cmd", ".bat"}
OPENCODE_RUNTIME_TOOLS = ("sro_preview", "sro_raw", "sro_card", "sro_read", "sro_trace")
OPENCLAW_RUNTIME_TOOLS = ("sro_preview", "sro_raw", "sro_card", "sro_read", "sro_decide", "sro_trace")


@dataclass(frozen=True)
class CommandSpec:
    executable: str

    def argv(self, *args: str) -> list[str]:
        if is_windows_shell_script(self.executable):
            shell = os.environ.get("COMSPEC") or "cmd.exe"
            return [shell, "/d", "/s", "/c", subprocess.list2cmdline([self.executable, *args])]
        return [self.executable, *args]


@dataclass(frozen=True)
class InstallProfile:
    policy: str
    mode: str
    openclaw_hook_mode: str


def install_profile(args: argparse.Namespace) -> InstallProfile:
    """Map the user-facing SparseRead mode to internal adapter knobs.

    Public installs expose only two modes:
    - auto: gate-controlled interception for high-benefit reads.
    - advisory: prompt/tool guidance only; no OpenClaw native tool interception.

    The legacy internal flags remain accepted for old scripts, but are not
    shown in help or docs.
    """

    public_mode = getattr(args, "sparseread_mode", None) or "auto"
    if public_mode not in {"auto", "advisory"}:
        raise SystemExit(f"invalid SparseRead mode: {public_mode}")
    default_policy = "auto" if public_mode == "auto" else "advisory"
    default_hook_mode = "enforce" if public_mode == "auto" else "prompt"
    return InstallProfile(
        policy=getattr(args, "policy", None) or default_policy,
        mode=getattr(args, "mode", None) or "auto",
        openclaw_hook_mode=getattr(args, "openclaw_hook_mode", None) or default_hook_mode,
    )


def run(
    cmd: list[str],
    *,
    cwd: Path = ROOT,
    input_text: str | None = None,
    check: bool = True,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(cmd), flush=True)
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        raise SystemExit(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def require_command(name: str, *, install_hint: str = "") -> str:
    path = shutil.which(name)
    if path:
        return path
    if os.name == "nt" and not Path(name).suffix:
        for suffix in WINDOWS_COMMAND_SUFFIXES:
            path = shutil.which(f"{name}{suffix}")
            if path:
                return path
    suffix = f" {install_hint}" if install_hint else ""
    raise SystemExit(f"missing required command: {name}.{suffix}")


def command_spec(name: str, *, install_hint: str = "") -> CommandSpec:
    return CommandSpec(require_command(name, install_hint=install_hint))


def openclaw_runtime_hook_names(payload: dict) -> set[str]:
    names: set[str] = set()
    for source in (payload, payload.get("plugin") if isinstance(payload.get("plugin"), dict) else {}):
        if not isinstance(source, dict):
            continue
        for key in ("hookNames", "hooks", "typedHooks"):
            items = source.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    names.add(item)
                elif isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.add(item["name"])
    return names


def is_windows_shell_script(path: str) -> bool:
    return Path(path).suffix.lower() in WINDOWS_SHELL_EXTS


def runtime_python(runtime_dir: Path) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return runtime_dir / scripts / executable


def bridge_command(python: Path) -> list[str]:
    return [str(python)]


def bridge_invocation(python: Path, *args: str) -> list[str]:
    return CommandSpec(str(python)).argv(*args)


def opencode_runtime_dir(workspace: Path) -> Path:
    return workspace / ".sparseread" / "runtime" / "opencode"


def openclaw_runtime_dir(profile: str) -> Path:
    return openclaw_profile_config_path(profile).parent / "sparseread" / "runtime"


def opencode_workspace_config(workspace: Path, python: Path, policy: str, mode: str) -> dict[str, object]:
    return {
        "projectRoot": str(workspace),
        "bridgeCommand": bridge_command(python),
        "bridgeModule": "sparseread_opencode.bridge",
        "bridgeProtocol": BRIDGE_PROTOCOL_VERSION,
        "policy": policy,
        "mode": mode,
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object.")
    return payload


def openclaw_profile_config_path(profile: str) -> Path:
    base = Path.home() / (".openclaw" if not profile else f".openclaw-{profile}")
    return base / "openclaw.json"


def opencode_workspace_paths(workspace: Path) -> tuple[Path, Path]:
    return workspace / ".opencode" / "plugins" / "sparseread.js", workspace / ".opencode" / "sparseread.json"


def validate_opencode_workspace(workspace: Path) -> None:
    plugin_target, config_target = opencode_workspace_paths(workspace)
    if not plugin_target.exists():
        raise SystemExit(f"OpenCode plugin file is missing: {plugin_target}")
    if not config_target.exists():
        raise SystemExit(f"OpenCode config is missing: {config_target}")
    config = read_json(config_target)
    if config.get("projectRoot") != str(workspace):
        raise SystemExit(f"OpenCode config has unexpected projectRoot: {config.get('projectRoot')}")
    if config.get("bridgeModule") != "sparseread_opencode.bridge":
        raise SystemExit(f"OpenCode config has unexpected bridgeModule: {config.get('bridgeModule')}")
    if config.get("bridgeProtocol") != BRIDGE_PROTOCOL_VERSION:
        raise SystemExit(f"OpenCode config has unexpected bridgeProtocol: {config.get('bridgeProtocol')}")
    if config.get("policy") not in {"observe", "advisory", "enforce", "native", "auto"}:
        raise SystemExit(f"OpenCode config has invalid policy: {config.get('policy')}")
    if config.get("mode") not in {"auto", "bench_protocol", "force", "force_sro", "native", "advisory"}:
        raise SystemExit(f"OpenCode config has invalid mode: {config.get('mode')}")
    bridge_cmd = config.get("bridgeCommand")
    if (
        not isinstance(bridge_cmd, list)
        or not bridge_cmd
        or any(not isinstance(part, str) or not part.strip() for part in bridge_cmd)
    ):
        raise SystemExit(f"OpenCode config has invalid bridgeCommand: {bridge_cmd!r}")
    print(f"[doctor] opencode workspace config passed: {config_target}")


def validate_openclaw_runtime(stdout: str, *, hook_mode: str) -> None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"OpenClaw runtime inspect returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("OpenClaw runtime inspect must return a JSON object.")
    plugin = payload.get("plugin")
    if not isinstance(plugin, dict):
        raise SystemExit("OpenClaw runtime inspect must include plugin metadata.")
    root_dir = plugin.get("rootDir")
    if not isinstance(root_dir, str) or not root_dir.strip():
        raise SystemExit("OpenClaw runtime inspect did not report an installed plugin root.")
    if Path(root_dir).resolve() == OPENCLAW_PLUGIN.resolve():
        raise SystemExit("OpenClaw is still loading SparseRead from the source checkout instead of an installed package.")
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, list):
        for item in diagnostics:
            if not isinstance(item, dict):
                continue
            message = item.get("message")
            if isinstance(message, str) and "duplicate plugin id" in message.lower():
                raise SystemExit(f"OpenClaw runtime still reports duplicate SparseRead plugins: {message}")
    status = payload.get("status")
    if isinstance(status, str) and status.lower() != "loaded":
        raise SystemExit(f"OpenClaw runtime inspect reported non-loaded status: {status}")
    text = json.dumps(payload, sort_keys=True)
    missing = [tool for tool in OPENCLAW_RUNTIME_TOOLS if tool not in text]
    if missing:
        raise SystemExit(f"OpenClaw runtime inspect is missing SparseRead tools: {', '.join(missing)}")
    hook_names = openclaw_runtime_hook_names(payload)
    if hook_mode == "off":
        hook_count = payload.get("hookCount")
        hooks = payload.get("hooks")
        if isinstance(hook_count, int) and hook_count != 0:
            raise SystemExit(f"OpenClaw production install expected 0 hooks, got hookCount={hook_count}")
        if hook_names:
            raise SystemExit(f"OpenClaw production install expected no hookNames, got {hook_names}")
        if isinstance(hooks, list) and hooks:
            raise SystemExit(f"OpenClaw production install expected no runtime hooks, got {hooks}")
    if hook_mode == "prompt":
        disallowed = {"before_tool_call", "after_tool_call", "llm_output"} & hook_names
        if disallowed:
            raise SystemExit(
                "OpenClaw prompt install must not register native tool lifecycle hooks: "
                f"{sorted(disallowed)}"
            )
    if hook_mode == "enforce" and "before_tool_call" not in hook_names:
        raise SystemExit(
            "OpenClaw enforce install must register before_tool_call; "
            f"runtime hooks were {sorted(hook_names)}"
        )
    print("[doctor] openclaw runtime inspect passed")


def npm_install_and_build(plugin_dir: Path, *, dry_run: bool) -> None:
    npm_cmd = command_spec("npm")
    if (plugin_dir / "package-lock.json").exists():
        run(npm_cmd.argv("ci", "--ignore-scripts"), cwd=plugin_dir, dry_run=dry_run)
    else:
        run(npm_cmd.argv("install", "--ignore-scripts"), cwd=plugin_dir, dry_run=dry_run)
    run(npm_cmd.argv("run", "build"), cwd=plugin_dir, dry_run=dry_run)


def npm_pack(plugin_dir: Path, destination: Path, *, dry_run: bool) -> Path:
    npm_cmd = command_spec("npm")
    destination.mkdir(parents=True, exist_ok=True)
    proc = run(
        npm_cmd.argv("pack", "--json", "--pack-destination", str(destination)),
        cwd=plugin_dir,
        dry_run=dry_run,
    )
    if dry_run:
        return destination / "sparseread-plugin.tgz"
    payload = json.loads(proc.stdout)
    if (
        not isinstance(payload, list)
        or not payload
        or not isinstance(payload[0], dict)
        or not isinstance(payload[0].get("filename"), str)
    ):
        raise SystemExit(f"npm pack returned an unexpected payload: {proc.stdout}")
    return destination / payload[0]["filename"]


def install_python_runtime(
    runtime_dir: Path,
    adapter: Path,
    *,
    python: str,
    dry_run: bool,
) -> Path:
    uv_cmd = command_spec("uv", install_hint="Install uv first: https://docs.astral.sh/uv/")
    managed_python = runtime_python(runtime_dir)
    run(uv_cmd.argv("venv", str(runtime_dir), "--python", python), dry_run=dry_run)
    with tempfile.TemporaryDirectory(prefix="sparseread-python-pack-") as tmp:
        wheel_dir = Path(tmp)
        run(uv_cmd.argv("build", "--wheel", "--project", str(CORE_PACKAGE), "--out-dir", str(wheel_dir)), dry_run=dry_run)
        run(uv_cmd.argv("build", "--wheel", "--project", str(adapter), "--out-dir", str(wheel_dir)), dry_run=dry_run)
        if dry_run:
            wheels = [wheel_dir / "sparseread_core.whl", wheel_dir / "sparseread_adapter.whl"]
        else:
            wheels = sorted(wheel_dir.glob("*.whl"))
            if len(wheels) != 2:
                raise SystemExit(f"expected core and adapter wheels, found: {wheels}")
        run(
            uv_cmd.argv(
                "pip",
                "install",
                "--force-reinstall",
                "--python",
                str(managed_python),
                *(str(wheel) for wheel in wheels),
                "pymupdf>=1.25.0",
                "openpyxl>=3.1.0,<4.0.0",
            ),
            dry_run=dry_run,
        )
    return managed_python


def install_opencode_plugin_package(workspace: Path, *, dry_run: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="sparseread-opencode-pack-") as tmp:
        tarball = npm_pack(OPENCODE_PLUGIN, Path(tmp), dry_run=dry_run)
        npm_cmd = command_spec("npm")
        run(
            npm_cmd.argv("install", "--prefix", str(workspace / ".opencode"), "--no-save", str(tarball)),
            dry_run=dry_run,
        )


def install_opencode(args: argparse.Namespace) -> None:
    profile = install_profile(args)
    workspace = Path(args.opencode_workspace or os.getcwd()).expanduser().resolve()
    plugin_target, config_target = opencode_workspace_paths(workspace)
    command_spec(args.opencode_cmd)
    if not args.skip_build:
        npm_install_and_build(OPENCODE_PLUGIN, dry_run=args.dry_run)
    managed_python = install_python_runtime(
        opencode_runtime_dir(workspace),
        OPENCODE_ADAPTER,
        python=getattr(args, "python", sys.executable),
        dry_run=args.dry_run,
    )
    install_opencode_plugin_package(workspace, dry_run=args.dry_run)
    print(f"[opencode] install workspace: {workspace}")
    if not args.dry_run:
        plugin_target.parent.mkdir(parents=True, exist_ok=True)
        plugin_target.write_text('export { default } from "@sparseread/opencode"\n', encoding="utf-8")
        write_json(config_target, opencode_workspace_config(workspace, managed_python, profile.policy, profile.mode))
    print(f"[opencode] plugin: {plugin_target}")
    print(f"[opencode] config: {config_target}")
    print("[opencode] launch with: opencode run ...")


def install_openclaw(args: argparse.Namespace) -> None:
    profile = install_profile(args)
    openclaw_cmd = command_spec(args.openclaw_cmd)
    if not args.skip_build:
        npm_install_and_build(OPENCLAW_PLUGIN, dry_run=args.dry_run)
    managed_python = install_python_runtime(
        openclaw_runtime_dir(args.openclaw_profile),
        OPENCLAW_ADAPTER,
        python=getattr(args, "python", sys.executable),
        dry_run=args.dry_run,
    )
    profile_args = ["--profile", args.openclaw_profile] if args.openclaw_profile else []
    hook_policy: dict[str, bool] = {}
    if profile.openclaw_hook_mode in {"prompt", "trace", "enforce"}:
        hook_policy["allowPromptInjection"] = True
    if profile.openclaw_hook_mode in {"prompt", "trace", "enforce"}:
        hook_policy["allowConversationAccess"] = True
    run(
        openclaw_cmd.argv(*profile_args, "plugins", "uninstall", "sparseread-openclaw", "--force"),
        check=False,
        dry_run=args.dry_run,
    )
    with tempfile.TemporaryDirectory(prefix="sparseread-openclaw-pack-") as tmp:
        tarball = npm_pack(OPENCLAW_PLUGIN, Path(tmp), dry_run=args.dry_run)
        run(
            openclaw_cmd.argv(*profile_args, "plugins", "install", str(tarball)),
            check=False,
            dry_run=args.dry_run,
        )
    run(
        openclaw_cmd.argv(*profile_args, "plugins", "enable", "sparseread-openclaw"),
        check=False,
        dry_run=args.dry_run,
    )
    run(openclaw_cmd.argv(*profile_args, "plugins", "registry", "--refresh", "--json"), dry_run=args.dry_run)
    patch = {
        "plugins": {
            "entries": {
                "sparseread-openclaw": {
                    "enabled": True,
                    "hooks": hook_policy,
                    "config": {
                        "policy": profile.policy,
                        "bridgeCommand": json.dumps(bridge_command(managed_python)),
                        "bridgeProtocol": BRIDGE_PROTOCOL_VERSION,
                        "projectRoot": str(openclaw_profile_config_path(args.openclaw_profile).parent),
                        "workspaceRoot": str(Path(args.openclaw_workspace).expanduser().resolve())
                        if args.openclaw_workspace
                        else "",
                        "bridgeModule": "sparseread_openclaw.bridge",
                        "mode": profile.mode,
                        "hookMode": profile.openclaw_hook_mode,
                    },
                }
            }
        }
    }
    run(
        openclaw_cmd.argv(*profile_args, "config", "patch", "--stdin"),
        input_text=json.dumps(patch),
        dry_run=args.dry_run,
    )
    inspect = run(
        openclaw_cmd.argv(*profile_args, "plugins", "inspect", "sparseread-openclaw", "--runtime", "--json"),
        check=False,
        dry_run=args.dry_run,
    )
    if not args.dry_run and inspect.returncode != 0:
        raise SystemExit(
            "OpenClaw plugin install did not load cleanly. "
            f"Inspect stderr:\n{inspect.stderr}\nInspect stdout:\n{inspect.stdout}"
        )
    if inspect.returncode == 0 and inspect.stdout:
        validate_openclaw_runtime(inspect.stdout, hook_mode=profile.openclaw_hook_mode)
    print("[openclaw] restart the gateway or start a new agent run after install")


def bridge_smoke(python: Path, module: str, *, dry_run: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="sparseread-install-smoke-") as tmp:
        workspace = Path(tmp)
        target = workspace / "report.md"
        target.write_text("# Report\n\nROOT_CAUSE: cache invalidation used tenant_id.\n", encoding="utf-8")
        payload = "\n".join(
            [
                json.dumps({"id": "0", "method": "version", "params": {}}),
                json.dumps({"id": "1", "method": "preview", "params": {"path": str(target)}}),
                json.dumps({"id": "2", "method": "trace", "params": {}}),
                json.dumps({"id": "3", "method": "shutdown", "params": {}}),
                "",
            ]
        )
        proc = run(
            bridge_invocation(python, "-m", module, "--workspace", str(workspace), "--mode", "force"),
            input_text=payload,
            check=False,
            dry_run=dry_run,
        )
        if dry_run:
            return
        if proc.returncode != 0:
            raise SystemExit(f"{module} smoke failed:\n{proc.stderr}")
        if f'"protocol_version":"{BRIDGE_PROTOCOL_VERSION}"' not in proc.stdout.replace(" ", ""):
            raise SystemExit(f"{module} smoke reported an incompatible bridge protocol:\n{proc.stdout}")
        if '"sro_preview_calls":1' not in proc.stdout.replace(" ", ""):
            raise SystemExit(f"{module} smoke did not report preview call:\n{proc.stdout}")
        print(f"[doctor] {module} bridge smoke passed")


def doctor(args: argparse.Namespace) -> None:
    profile = install_profile(args)
    command_spec("uv", install_hint="Install uv first: https://docs.astral.sh/uv/")
    command_spec("node")
    command_spec("npm")
    if args.platform in {"opencode", "both"}:
        command_spec(args.opencode_cmd)
        bridge_smoke(runtime_python(opencode_runtime_dir(Path(args.opencode_workspace or os.getcwd()).expanduser().resolve())), "sparseread_opencode.bridge", dry_run=args.dry_run)
        if not args.dry_run:
            validate_opencode_workspace(Path(args.opencode_workspace or os.getcwd()).expanduser().resolve())
    if args.platform in {"openclaw", "both"}:
        openclaw_cmd = command_spec(args.openclaw_cmd)
        bridge_smoke(runtime_python(openclaw_runtime_dir(args.openclaw_profile)), "sparseread_openclaw.bridge", dry_run=args.dry_run)
        if not args.dry_run:
            profile_args = ["--profile", args.openclaw_profile] if args.openclaw_profile else []
            inspect = run(
                openclaw_cmd.argv(*profile_args, "plugins", "inspect", "sparseread-openclaw", "--runtime", "--json"),
                check=False,
                dry_run=False,
            )
            if inspect.returncode != 0:
                raise SystemExit(
                    "OpenClaw runtime inspect failed during doctor. "
                    f"Inspect stderr:\n{inspect.stderr}\nInspect stdout:\n{inspect.stdout}"
                )
            validate_openclaw_runtime(inspect.stdout, hook_mode=profile.openclaw_hook_mode)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install self-contained SparseRead framework adapters.")
    parser.add_argument("--platform", choices=["opencode", "openclaw", "both"], default="both")
    parser.add_argument("--opencode-workspace", default="", help="Workspace to receive the OpenCode SparseRead plugin")
    parser.add_argument("--opencode-cmd", default="opencode")
    parser.add_argument("--openclaw-cmd", default="openclaw")
    parser.add_argument("--openclaw-profile", default="", help="Optional OpenClaw profile name")
    parser.add_argument("--openclaw-workspace", default="", help="Optional OpenClaw default SparseRead workspaceRoot")
    parser.add_argument("--python", default=sys.executable, help="Python used to create the managed SparseRead runtime")
    parser.add_argument(
        "--sparseread-mode",
        choices=["auto", "advisory"],
        default="auto",
        help=(
            "User-facing SparseRead mode. auto is the default: gate-controlled interception for "
            "high-benefit reads. advisory registers tools/prompts only and never intercepts native reads."
        ),
    )
    parser.add_argument(
        "--openclaw-hook-mode",
        choices=["off", "prompt", "trace", "enforce"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--policy",
        choices=["observe", "advisory", "enforce", "native", "auto"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "bench_protocol", "force", "force_sro", "native", "advisory"],
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--skip-build", action="store_true", help="Skip npm install/build for plugin packages")
    parser.add_argument("--doctor", action="store_true", help="Run bridge/CLI checks after install")
    parser.add_argument("--doctor-only", action="store_true", help="Only run checks; do not install")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.dry_run:
        print("[dry-run] no files or framework configs will be changed")
    if args.doctor_only:
        doctor(args)
        return 0
    if args.platform in {"opencode", "both"}:
        install_opencode(args)
    if args.platform in {"openclaw", "both"}:
        install_openclaw(args)
    if args.doctor:
        doctor(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
