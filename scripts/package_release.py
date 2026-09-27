#!/usr/bin/env python3
"""Build source installer archives and checksums for a GitHub release."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import stat
import subprocess
import tarfile
import time
import tomllib
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r"v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?\Z")
PYTHON_PACKAGES = (
    "packages/sparseread-core",
    "integrations/agent-tools/python",
    "integrations/claude/python",
    "integrations/nanobot/python",
    "integrations/openclaw/python",
    "integrations/opencode/python",
)
TOML_VERSION_FILES = ("pyproject.toml", *(f"{package}/pyproject.toml" for package in PYTHON_PACKAGES))
JSON_VERSION_FILES = (
    "integrations/codex/plugin/sparseread-codex/.codex-plugin/plugin.json",
    "integrations/pi/package/package.json",
    "integrations/opencode/plugin/package.json",
    "integrations/openclaw/plugin/package.json",
    "integrations/openclaw/plugin/openclaw.plugin.json",
)
LOCK_VERSION_FILES = (
    "integrations/pi/package/package-lock.json",
    "integrations/opencode/plugin/package-lock.json",
    "integrations/openclaw/plugin/package-lock.json",
)
VERSION_METADATA_FILES = TOML_VERSION_FILES + JSON_VERSION_FILES + LOCK_VERSION_FILES
SOURCE_PATHS = (
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    "docs/installation.md",
    "docs/codex-pi-adapters.md",
    "integrations/README.md",
    "scripts/install_sparseread.py",
    *(f"{package}/{name}" for package in PYTHON_PACKAGES for name in ("README.md", "pyproject.toml", "src")),
    "integrations/claude/CLAUDE.md",
    "integrations/codex/plugin/sparseread-codex/.codex-plugin/plugin.json",
    "integrations/codex/plugin/sparseread-codex/.mcp.json",
    "integrations/codex/plugin/sparseread-codex/README.md",
    "integrations/codex/plugin/sparseread-codex/hooks/hooks.json",
    "integrations/codex/plugin/sparseread-codex/scripts/launcher.mjs",
    "integrations/codex/plugin/sparseread-codex/skills/sparse-reading/SKILL.md",
    "integrations/pi/package/README.md",
    "integrations/pi/package/package.json",
    "integrations/pi/package/package-lock.json",
    "integrations/pi/package/src",
    *(f"integrations/{framework}/plugin/{name}" for framework in ("opencode", "openclaw") for name in (
        "LICENSE",
        "README.md",
        "package-lock.json",
        "package.json",
        "tsconfig.json",
    )),
    "integrations/opencode/plugin/sparseread.ts",
    "integrations/openclaw/plugin/openclaw.plugin.json",
    "integrations/openclaw/plugin/skills",
    "integrations/openclaw/plugin/src",
)


def _git_revision(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _git_archive(repo: Path, prefix: str, revision: str) -> bytes:
    command = [
        "git",
        "archive",
        "--format=tar",
        f"--prefix={prefix}/",
        revision,
        "--",
        *SOURCE_PATHS,
    ]
    result = subprocess.run(command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip())
    if not result.stdout:
        raise RuntimeError("git archive produced an empty source bundle")
    return result.stdout


def _git_show(repo: Path, revision: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def _check_version(path: str, expected: str, actual: object) -> None:
    if actual != expected:
        raise ValueError(f"committed {path} version {actual!r} does not match release tag v{expected}")


def _validate_release_version(repo: Path, revision: str, version: str) -> None:
    expected = version.removeprefix("v")
    for path in TOML_VERSION_FILES:
        try:
            metadata = tomllib.loads(_git_show(repo, revision, path))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid committed version metadata in {path}: {exc}") from exc
        project = metadata.get("project")
        _check_version(path, expected, project.get("version") if isinstance(project, dict) else None)
    for path in JSON_VERSION_FILES + LOCK_VERSION_FILES:
        try:
            metadata = json.loads(_git_show(repo, revision, path))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid committed version metadata in {path}: {exc}") from exc
        _check_version(path, expected, metadata.get("version"))
        if path in LOCK_VERSION_FILES:
            packages = metadata.get("packages")
            root_package = packages.get("") if isinstance(packages, dict) else None
            root_version = root_package.get("version") if isinstance(root_package, dict) else None
            _check_version(f'{path} packages[""]', expected, root_version)


def _zip_from_tar(tar_bytes: bytes, expected_root: str) -> bytes:
    output = BytesIO()
    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:") as source:
        with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for member in source.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != expected_root:
                    raise RuntimeError(f"unsafe path in git archive: {member.name}")
                if not member.isdir() and not member.isfile():
                    raise RuntimeError(f"unsupported file type in git archive: {member.name}")
                timestamp = time.gmtime(max(member.mtime, 315532800))[:6]
                name = member.name + "/" if member.isdir() and not member.name.endswith("/") else member.name
                info = zipfile.ZipInfo(name, date_time=timestamp)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                file_type = stat.S_IFDIR if member.isdir() else stat.S_IFREG
                info.external_attr = (file_type | member.mode) << 16
                if member.isdir():
                    info.external_attr |= 0x10
                    bundle.writestr(info, b"")
                else:
                    stream = source.extractfile(member)
                    if stream is None:
                        raise RuntimeError(f"could not read git archive member: {member.name}")
                    with stream:
                        bundle.writestr(info, stream.read())
    return output.getvalue()


def write_sha256sums(output_dir: Path) -> Path:
    manifest = output_dir / "SHA256SUMS"
    artifacts = sorted(
        (path for path in output_dir.iterdir() if path.is_file() and not path.is_symlink()
         and not path.name.startswith(".") and path.name != manifest.name),
        key=lambda path: path.name,
    )
    lines = []
    for artifact in artifacts:
        with artifact.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {artifact.name}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def build_source_installer(repo: Path, output_dir: Path, version: str) -> tuple[Path, Path, Path]:
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"version must be a v-prefixed release tag, got {version!r}")
    repo = repo.resolve()
    output_dir = output_dir if output_dir.is_absolute() else repo / output_dir
    revision = _git_revision(repo)
    _validate_release_version(repo, revision, version)

    archive_root = f"sparseread-{version}"
    tar_path = output_dir / f"sparseread-source-installer-{version}.tar.gz"
    zip_path = output_dir / f"sparseread-source-installer-{version}.zip"
    source_tar = _git_archive(repo, archive_root, revision)
    source_zip = _zip_from_tar(source_tar, archive_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    tar_path.write_bytes(gzip.compress(source_tar, compresslevel=9, mtime=0))
    zip_path.write_bytes(source_zip)
    return tar_path, zip_path, write_sha256sums(output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="release tag, for example v0.1.2")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        paths = build_source_installer(ROOT, args.output_dir, args.version)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
