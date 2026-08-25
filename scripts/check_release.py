"""Validate registry-facing SparseRead release metadata."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]

PYTHON_PROJECTS = {
    "sparseread": (
        ROOT / "packages/sparseread-core/pyproject.toml",
        ROOT / "packages/sparseread-core/src/sparseread/__init__.py",
    ),
    "sparseread-nanobot": (
        ROOT / "integrations/nanobot/python/pyproject.toml",
        ROOT / "integrations/nanobot/python/src/sparseread_nanobot/__init__.py",
    ),
    "sparseread-opencode": (
        ROOT / "integrations/opencode/python/pyproject.toml",
        ROOT / "integrations/opencode/python/src/sparseread_opencode/__init__.py",
    ),
    "sparseread-openclaw": (
        ROOT / "integrations/openclaw/python/pyproject.toml",
        ROOT / "integrations/openclaw/python/src/sparseread_openclaw/__init__.py",
    ),
    "sparseread-claude": (
        ROOT / "integrations/claude/python/pyproject.toml",
        ROOT / "integrations/claude/python/src/sparseread_claude/__init__.py",
    ),
}

NPM_PROJECTS = {
    "@sparseread/opencode": ROOT / "integrations/opencode/plugin",
    "@sparseread/openclaw": ROOT / "integrations/openclaw/plugin",
}

PROJECT_URLS = {"Homepage", "Repository", "Issues", "Paper"}
REPOSITORY_URL = "git+https://github.com/Zedong-Liu/SparseReading.git"


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def project_version() -> str:
    payload = read_toml(PYTHON_PROJECTS["sparseread"][0])
    return str(payload["project"]["version"])


def module_version(path: Path) -> str | None:
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']',
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return match.group(1) if match else None


def validate(tag: str | None = None) -> list[str]:
    errors: list[str] = []
    version = project_version()
    root_license = (ROOT / "LICENSE").read_text(encoding="utf-8")

    for expected_name, (pyproject_path, module_path) in PYTHON_PROJECTS.items():
        payload = read_toml(pyproject_path)
        project = payload.get("project", {})
        prefix = pyproject_path.relative_to(ROOT)
        if project.get("name") != expected_name:
            errors.append(f"{prefix}: expected project name {expected_name!r}")
        if project.get("version") != version:
            errors.append(f"{prefix}: version must be {version}")
        if module_version(module_path) != version:
            errors.append(f"{module_path.relative_to(ROOT)}: __version__ must be {version}")
        if project.get("license") != "MIT" or project.get("license-files") != ["LICENSE"]:
            errors.append(f"{prefix}: expected MIT license and license-files = ['LICENSE']")
        package_license = pyproject_path.parent / "LICENSE"
        if not package_license.exists() or package_license.read_text(encoding="utf-8") != root_license:
            errors.append(f"{package_license.relative_to(ROOT)}: must match the repository LICENSE")
        urls = project.get("urls", {})
        missing_urls = PROJECT_URLS - set(urls)
        if missing_urls:
            errors.append(f"{prefix}: missing project URLs: {', '.join(sorted(missing_urls))}")

        lock_path = pyproject_path.parent / "uv.lock"
        locked_packages = {
            (str(package.get("name")), str(package.get("version")))
            for package in read_toml(lock_path).get("package", [])
        }
        if (expected_name, version) not in locked_packages:
            errors.append(f"{lock_path.relative_to(ROOT)}: missing {expected_name} {version}")
        if expected_name != "sparseread" and ("sparseread", version) not in locked_packages:
            errors.append(f"{lock_path.relative_to(ROOT)}: missing sparseread {version}")

        dependencies = project.get("dependencies", [])
        if expected_name != "sparseread" and f"sparseread>={version},<0.2" not in dependencies:
            errors.append(f"{prefix}: adapter must depend on sparseread>={version},<0.2")

    for expected_name, package_dir in NPM_PROJECTS.items():
        package_path = package_dir / "package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        prefix = package_path.relative_to(ROOT)
        if package.get("name") != expected_name:
            errors.append(f"{prefix}: expected package name {expected_name!r}")
        if package.get("version") != version:
            errors.append(f"{prefix}: version must be {version}")
        if package.get("private") is not False:
            errors.append(f"{prefix}: package must be public")
        if package.get("license") != "MIT":
            errors.append(f"{prefix}: expected MIT license")
        if package.get("repository", {}).get("url") != REPOSITORY_URL:
            errors.append(f"{prefix}: repository.url must match the GitHub repository")
        publish_config = package.get("publishConfig", {})
        if publish_config.get("access") != "public":
            errors.append(f"{prefix}: scoped package must publish with public access")
        if not (package_dir / "LICENSE").exists():
            errors.append(f"{package_dir.relative_to(ROOT)}/LICENSE: missing")

        lock = json.loads((package_dir / "package-lock.json").read_text(encoding="utf-8"))
        if lock.get("version") != version or lock.get("packages", {}).get("", {}).get("version") != version:
            errors.append(f"{package_dir.relative_to(ROOT)}/package-lock.json: version must be {version}")

    manifest = json.loads(
        (ROOT / "integrations/openclaw/plugin/openclaw.plugin.json").read_text(encoding="utf-8")
    )
    if manifest.get("version") != version:
        errors.append("integrations/openclaw/plugin/openclaw.plugin.json: version mismatch")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"^## v{re.escape(version)}\b", changelog, re.MULTILINE):
        errors.append(f"CHANGELOG.md: missing v{version} entry")

    if tag and tag != f"v{version}":
        errors.append(f"release tag {tag!r} does not match package version v{version}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Require this release tag to equal v<package-version>.")
    args = parser.parse_args(argv)
    errors = validate(args.tag)
    if errors:
        print("Release metadata validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Release metadata is consistent for v{project_version()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
