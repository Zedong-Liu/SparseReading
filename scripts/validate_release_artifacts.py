"""Inspect built SparseRead wheel, sdist, and npm package contents."""

from __future__ import annotations

import argparse
import email
import json
import tarfile
import zipfile
from pathlib import Path

from check_release import NPM_PROJECTS, PYTHON_PROJECTS, project_version


def normalized(name: str) -> str:
    return name.replace("-", "_")


def validate_python(root: Path, errors: list[str]) -> None:
    version = project_version()
    for distribution in PYTHON_PROJECTS:
        stem = normalized(distribution)
        wheels = list(root.glob(f"{stem}-{version}-*.whl"))
        sdists = list(root.glob(f"{stem}-{version}.tar.gz"))
        if len(wheels) != 1:
            errors.append(f"expected one wheel for {distribution}, found {len(wheels)}")
            continue
        if len(sdists) != 1:
            errors.append(f"expected one sdist for {distribution}, found {len(sdists)}")
            continue

        with zipfile.ZipFile(wheels[0]) as archive:
            names = archive.namelist()
            metadata_name = next((name for name in names if name.endswith(".dist-info/METADATA")), None)
            if metadata_name is None:
                errors.append(f"{wheels[0].name}: missing METADATA")
                continue
            metadata = email.message_from_bytes(archive.read(metadata_name))
            if metadata.get("Name") != distribution or metadata.get("Version") != version:
                errors.append(f"{wheels[0].name}: incorrect Name or Version metadata")
            if metadata.get("License-Expression") != "MIT":
                errors.append(f"{wheels[0].name}: missing MIT License-Expression")
            if "LICENSE" not in metadata.get_all("License-File", []):
                errors.append(f"{wheels[0].name}: missing License-File metadata")
            if not any(name.endswith("/licenses/LICENSE") for name in names):
                errors.append(f"{wheels[0].name}: missing packaged LICENSE")
            if not any(name.endswith("/py.typed") for name in names):
                errors.append(f"{wheels[0].name}: missing py.typed marker")

        with tarfile.open(sdists[0], "r:gz") as archive:
            names = archive.getnames()
            if not any(name.endswith("/LICENSE") for name in names):
                errors.append(f"{sdists[0].name}: missing LICENSE")
            if not any(name.endswith("/README.md") for name in names):
                errors.append(f"{sdists[0].name}: missing README.md")


def validate_npm(root: Path, errors: list[str]) -> None:
    version = project_version()
    for package_name, package_dir in NPM_PROJECTS.items():
        tarball_stem = package_name.removeprefix("@").replace("/", "-")
        tarballs = list(root.glob(f"{tarball_stem}-{version}.tgz"))
        if len(tarballs) != 1:
            errors.append(f"expected one npm tarball for {package_name}, found {len(tarballs)}")
            continue
        with tarfile.open(tarballs[0], "r:gz") as archive:
            names = set(archive.getnames())
            required = {"package/package.json", "package/README.md", "package/LICENSE"}
            if package_name.endswith("/openclaw"):
                required |= {
                    "package/openclaw.plugin.json",
                    "package/skills/sparse-reading/SKILL.md",
                }
            missing = required - names
            if missing:
                errors.append(f"{tarballs[0].name}: missing {', '.join(sorted(missing))}")
            if not any(name.startswith("package/dist/") and name.endswith(".js") for name in names):
                errors.append(f"{tarballs[0].name}: missing compiled JavaScript")
            member = archive.extractfile("package/package.json")
            if member is None:
                continue
            package = json.load(member)
            if package.get("name") != package_name or package.get("version") != version:
                errors.append(f"{tarballs[0].name}: incorrect name or version")
            expected_directory = str(package_dir.relative_to(package_dir.parents[2]))
            if package.get("repository", {}).get("directory") != expected_directory:
                errors.append(f"{tarballs[0].name}: incorrect repository.directory")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_root", type=Path)
    args = parser.parse_args()
    errors: list[str] = []
    validate_python(args.artifact_root / "python", errors)
    validate_npm(args.artifact_root / "npm", errors)
    if errors:
        print("Release artifact validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"All SparseRead v{project_version()} release artifacts are complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
