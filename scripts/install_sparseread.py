#!/usr/bin/env python3
"""Install the repo-backed SparseRead adapters for existing agent CLIs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "nanobot-sro-v3"
OPENCODE_PLUGIN = ROOT / "integrations" / "opencode" / "plugin"
OPENCLAW_PLUGIN = ROOT / "integrations" / "openclaw" / "plugin"
WINDOWS_COMMAND_SUFFIXES = (".cmd", ".exe", ".bat")


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


def bridge_command(uv_cmd: str | None = None) -> list[str]:
    command = uv_cmd or require_command("uv", install_hint="Install uv first: https://docs.astral.sh/uv/")
    return [command, "--project", str(CORE), "run", "--with", "pymupdf", "python"]


def bridge_env(policy: str, mode: str) -> str:
    lines = [
        "# Source this before launching OpenCode from this workspace.",
        f"export SPARSEREAD_PROJECT_ROOT={json.dumps(str(ROOT))}",
        f"export SPARSEREAD_BRIDGE_COMMAND={json.dumps(json.dumps(bridge_command()))}",
        f"export SPARSEREAD_POLICY={json.dumps(policy)}",
        f"export SPARSEREAD_MODE={json.dumps(mode)}",
        "",
    ]
    return "\n".join(lines)


def npm_install_and_build(plugin_dir: Path, *, dry_run: bool) -> None:
    npm_cmd = require_command("npm")
    if (plugin_dir / "package-lock.json").exists():
        run([npm_cmd, "ci", "--ignore-scripts"], cwd=plugin_dir, dry_run=dry_run)
    else:
        run([npm_cmd, "install", "--ignore-scripts"], cwd=plugin_dir, dry_run=dry_run)
    run([npm_cmd, "run", "build"], cwd=plugin_dir, dry_run=dry_run)


def install_opencode(args: argparse.Namespace) -> None:
    workspace = Path(args.opencode_workspace or os.getcwd()).expanduser().resolve()
    plugin_target = workspace / ".opencode" / "plugins" / "sparseread.ts"
    env_target = workspace / ".opencode" / "sparseread.env"
    require_command(args.opencode_cmd)
    if not args.skip_build:
        npm_install_and_build(OPENCODE_PLUGIN, dry_run=args.dry_run)
    print(f"[opencode] install workspace: {workspace}")
    if not args.dry_run:
        plugin_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OPENCODE_PLUGIN / "sparseread.ts", plugin_target)
        env_target.write_text(bridge_env(args.policy, args.mode), encoding="utf-8")
    print(f"[opencode] plugin: {plugin_target}")
    print(f"[opencode] env: {env_target}")
    print("[opencode] launch with: source .opencode/sparseread.env && opencode run ...")


def install_openclaw(args: argparse.Namespace) -> None:
    openclaw_cmd = require_command(args.openclaw_cmd)
    if not args.skip_build:
        npm_install_and_build(OPENCLAW_PLUGIN, dry_run=args.dry_run)
    profile_args = ["--profile", args.openclaw_profile] if args.openclaw_profile else []
    run(
        [openclaw_cmd, *profile_args, "plugins", "install", "--link", str(OPENCLAW_PLUGIN)],
        check=False,
        dry_run=args.dry_run,
    )
    run(
        [openclaw_cmd, *profile_args, "plugins", "enable", "sparseread-openclaw"],
        check=False,
        dry_run=args.dry_run,
    )
    patch = {
        "plugins": {
            "entries": {
                "sparseread-openclaw": {
                    "enabled": True,
                    "config": {
                        "policy": args.policy,
                        "bridgeCommand": json.dumps(bridge_command()),
                        "projectRoot": str(ROOT),
                        "workspaceRoot": str(Path(args.openclaw_workspace).expanduser().resolve())
                        if args.openclaw_workspace
                        else "",
                        "bridgeModule": "sparseread.bridge.openclaw",
                        "mode": args.mode,
                    },
                }
            }
        }
    }
    run(
        [openclaw_cmd, *profile_args, "config", "patch", "--stdin"],
        input_text=json.dumps(patch),
        dry_run=args.dry_run,
    )
    inspect = run(
        [
            openclaw_cmd,
            *profile_args,
            "plugins",
            "inspect",
            "sparseread-openclaw",
            "--runtime",
            "--json",
        ],
        check=False,
        dry_run=args.dry_run,
    )
    if not args.dry_run and inspect.returncode != 0:
        raise SystemExit(
            "OpenClaw plugin install did not load cleanly. "
            f"Inspect stderr:\n{inspect.stderr}\nInspect stdout:\n{inspect.stdout}"
        )
    if inspect.returncode == 0 and inspect.stdout:
        print("[openclaw] runtime inspect passed")
    print("[openclaw] restart the gateway or start a new agent run after install")


def bridge_smoke(module: str, *, dry_run: bool) -> None:
    uv_cmd = require_command("uv", install_hint="Install uv first: https://docs.astral.sh/uv/")
    with tempfile.TemporaryDirectory(prefix="sparseread-install-smoke-") as tmp:
        workspace = Path(tmp)
        target = workspace / "report.md"
        target.write_text("# Report\n\nROOT_CAUSE: cache invalidation used tenant_id.\n", encoding="utf-8")
        payload = "\n".join(
            [
                json.dumps({"id": "1", "method": "preview", "params": {"path": str(target)}}),
                json.dumps({"id": "2", "method": "trace", "params": {}}),
                json.dumps({"id": "3", "method": "shutdown", "params": {}}),
                "",
            ]
        )
        proc = run(
            [*bridge_command(uv_cmd), "-m", module, "--workspace", str(workspace), "--mode", "force"],
            input_text=payload,
            check=False,
            dry_run=dry_run,
        )
        if dry_run:
            return
        if proc.returncode != 0:
            raise SystemExit(f"{module} smoke failed:\n{proc.stderr}")
        if '"sro_preview_calls":1' not in proc.stdout.replace(" ", ""):
            raise SystemExit(f"{module} smoke did not report preview call:\n{proc.stdout}")
        print(f"[doctor] {module} bridge smoke passed")


def doctor(args: argparse.Namespace) -> None:
    require_command("uv", install_hint="Install uv first: https://docs.astral.sh/uv/")
    require_command("node")
    require_command("npm")
    if args.platform in {"opencode", "both"}:
        require_command(args.opencode_cmd)
        bridge_smoke("sparseread.bridge.opencode", dry_run=args.dry_run)
    if args.platform in {"openclaw", "both"}:
        require_command(args.openclaw_cmd)
        bridge_smoke("sparseread.bridge.openclaw", dry_run=args.dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install repo-backed SparseRead adapters.")
    parser.add_argument("--platform", choices=["opencode", "openclaw", "both"], default="both")
    parser.add_argument("--opencode-workspace", default="", help="Workspace to receive .opencode/plugins/sparseread.ts")
    parser.add_argument("--opencode-cmd", default="opencode")
    parser.add_argument("--openclaw-cmd", default="openclaw")
    parser.add_argument("--openclaw-profile", default="", help="Optional OpenClaw profile name")
    parser.add_argument("--openclaw-workspace", default="", help="Optional OpenClaw default SparseRead workspaceRoot")
    parser.add_argument("--policy", choices=["observe", "advisory", "enforce", "native", "auto"], default="auto")
    parser.add_argument(
        "--mode",
        choices=["auto", "bench_protocol", "force", "force_sro", "native", "advisory"],
        default="auto",
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
