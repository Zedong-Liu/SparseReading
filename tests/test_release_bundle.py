from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_RELEASE_PATH = ROOT / "scripts" / "package_release.py"
PACKAGE_RELEASE_SPEC = importlib.util.spec_from_file_location("package_release_for_test", PACKAGE_RELEASE_PATH)
assert PACKAGE_RELEASE_SPEC is not None
assert PACKAGE_RELEASE_SPEC.loader is not None
PACKAGE_RELEASE = importlib.util.module_from_spec(PACKAGE_RELEASE_SPEC)
sys.modules[PACKAGE_RELEASE_SPEC.name] = PACKAGE_RELEASE
PACKAGE_RELEASE_SPEC.loader.exec_module(PACKAGE_RELEASE)
SOURCE_PATHS = PACKAGE_RELEASE.SOURCE_PATHS
build_source_installer = PACKAGE_RELEASE.build_source_installer
TOML_VERSION_FILES = PACKAGE_RELEASE.TOML_VERSION_FILES
JSON_VERSION_FILES = PACKAGE_RELEASE.JSON_VERSION_FILES
LOCK_VERSION_FILES = PACKAGE_RELEASE.LOCK_VERSION_FILES
VERSION_METADATA_FILES = PACKAGE_RELEASE.VERSION_METADATA_FILES


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _metadata_text(path: str, version: str, nested_version: str | None = None) -> str:
    if path in TOML_VERSION_FILES:
        package = path.removesuffix("/pyproject.toml") or "fixture-root"
        return f'[project]\nname = "{package}"\nversion = "{version}"\n'
    payload: dict = {"name": "fixture", "version": version}
    if path in LOCK_VERSION_FILES:
        payload["lockfileVersion"] = 3
        payload["packages"] = {"": {"name": "fixture", "version": nested_version or version}}
    return json.dumps(payload) + "\n"


def _source_repo(
    tmp_path: Path,
    version: str = "0.1.2",
    stale_path: str | None = None,
    stale_lock_package: str | None = None,
) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    _git(repo, "config", "user.name", "Release Test")

    for relative in dict.fromkeys((*SOURCE_PATHS, *VERSION_METADATA_FILES)):
        target = repo / relative
        if relative in VERSION_METADATA_FILES:
            fixture_version = "0.1.1" if relative == stale_path else version
            target.parent.mkdir(parents=True, exist_ok=True)
            nested_version = "0.1.1" if relative == stale_lock_package else None
            target.write_text(_metadata_text(relative, fixture_version, nested_version), encoding="utf-8")
        elif relative.endswith("/src") or relative.endswith("/skills"):
            target.mkdir(parents=True, exist_ok=True)
            (target / "release_fixture.py").write_text("SOURCE = True\n", encoding="utf-8")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"tracked source: {relative}\n", encoding="utf-8")

    excluded = {
        "benchmarks/local-records/private.jsonl": "private record\n",
        "dist/old-distribution.whl": "preserve this artifact\n",
        "integrations/agent-tools/python/tests/test_local.py": "test source\n",
        "integrations/pi/package/test/private.test.mjs": "test source\n",
        "integrations/pi/package/node_modules/example/index.js": "installed dependency\n",
        "runtime/credentials/token.key": "secret\n",
    }
    for relative, content in excluded.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "commit", "-qm", "fixture source")
    return repo


def test_source_installer_archives_include_production_source_and_checksums(tmp_path: Path) -> None:
    repo = _source_repo(tmp_path)
    output = tmp_path / "dist"
    output.mkdir()
    existing = output / "sparseread-core.whl"
    existing.write_bytes(b"existing distribution")
    hidden = output / ".gitignore"
    hidden.write_text("*\n", encoding="utf-8")

    tar_path, zip_path, sums_path = build_source_installer(repo, output, "v0.1.2")

    assert tar_path.name == "sparseread-source-installer-v0.1.2.tar.gz"
    assert zip_path.name == "sparseread-source-installer-v0.1.2.zip"
    assert existing.read_bytes() == b"existing distribution"
    with tarfile.open(tar_path, "r:gz") as bundle:
        tar_names = set(bundle.getnames())
    with zipfile.ZipFile(zip_path) as bundle:
        zip_names = set(bundle.namelist())
        bundle.extractall(tmp_path / "zip-extract")

    required = {
        "sparseread-v0.1.2/README.md",
        "sparseread-v0.1.2/README.zh-CN.md",
        "sparseread-v0.1.2/docs/installation.md",
        "sparseread-v0.1.2/docs/codex-pi-adapters.md",
        "sparseread-v0.1.2/scripts/install_sparseread.py",
        "sparseread-v0.1.2/pyproject.toml",
        "sparseread-v0.1.2/packages/sparseread-core/src/release_fixture.py",
        "sparseread-v0.1.2/integrations/agent-tools/python/src/release_fixture.py",
        "sparseread-v0.1.2/integrations/codex/plugin/sparseread-codex/README.md",
        "sparseread-v0.1.2/integrations/pi/package/src/release_fixture.py",
        "sparseread-v0.1.2/integrations/pi/package/package-lock.json",
        "sparseread-v0.1.2/integrations/opencode/plugin/package-lock.json",
        "sparseread-v0.1.2/integrations/openclaw/plugin/src/release_fixture.py",
    }
    assert required <= tar_names
    assert required <= zip_names
    assert (tmp_path / "zip-extract/sparseread-v0.1.2/scripts/install_sparseread.py").is_file()

    forbidden = ("local-records", "private.jsonl", "/tests/", "/test/", "node_modules", "/dist/", "credentials", ".key")
    assert not any(any(token in name for token in forbidden) for name in tar_names | zip_names)

    lines = sums_path.read_text(encoding="utf-8").splitlines()
    checksums = {name: digest for digest, name in (line.split("  ", 1) for line in lines)}
    expected_artifacts = {tar_path.name, zip_path.name, existing.name}
    assert set(checksums) == expected_artifacts
    for path in (tar_path, zip_path, existing):
        assert checksums[path.name] == hashlib.sha256(path.read_bytes()).hexdigest()

    first_archives = (tar_path.read_bytes(), zip_path.read_bytes())
    build_source_installer(repo, output, "v0.1.2")
    assert first_archives == (tar_path.read_bytes(), zip_path.read_bytes())


def test_source_installer_requires_a_release_tag(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="v-prefixed release tag"):
        build_source_installer(tmp_path, tmp_path / "dist", "0.1.2")


@pytest.mark.parametrize("stale_path", VERSION_METADATA_FILES)
def test_source_installer_rejects_each_stale_committed_version(tmp_path: Path, stale_path: str) -> None:
    repo = _source_repo(tmp_path, stale_path=stale_path)
    output = tmp_path / "must-not-be-created"

    with pytest.raises(ValueError, match="does not match release tag"):
        build_source_installer(repo, output, "v0.1.2")

    assert not output.exists()


@pytest.mark.parametrize("stale_path", LOCK_VERSION_FILES)
def test_source_installer_rejects_stale_npm_root_package_versions(tmp_path: Path, stale_path: str) -> None:
    repo = _source_repo(tmp_path, stale_lock_package=stale_path)
    output = tmp_path / "must-not-be-created"

    with pytest.raises(ValueError, match=r'packages\[""\]'):
        build_source_installer(repo, output, "v0.1.2")

    assert not output.exists()


def test_source_installer_checks_committed_metadata_not_working_tree(tmp_path: Path) -> None:
    repo = _source_repo(tmp_path, version="0.1.1")
    for path in VERSION_METADATA_FILES:
        (repo / path).write_text(_metadata_text(path, "0.1.2"), encoding="utf-8")
    output = tmp_path / "must-not-be-created"

    with pytest.raises(ValueError, match="committed pyproject.toml version '0.1.1'"):
        build_source_installer(repo, output, "v0.1.2")

    assert not output.exists()
