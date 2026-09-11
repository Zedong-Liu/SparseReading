"""Build and validate all SparseRead registry artifacts without publishing."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from check_release import NPM_PROJECTS, PYTHON_PROJECTS, ROOT


def run(*command: str, cwd: Path = ROOT) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def require(command: str) -> None:
    if shutil.which(command) is None:
        raise SystemExit(f"Required command is not installed: {command}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist/release",
        help="Artifact root; Python and npm packages are written below it.",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    python_output = output / "python"
    npm_output = output / "npm"
    python_output.mkdir(parents=True, exist_ok=True)
    npm_output.mkdir(parents=True, exist_ok=True)

    require("uv")
    require("npm")
    run(sys.executable, "scripts/check_release.py")

    for pyproject, _module in PYTHON_PROJECTS.values():
        run("uv", "build", "--project", str(pyproject.parent), "--out-dir", str(python_output))

    for package_dir in NPM_PROJECTS.values():
        run("npm", "ci", cwd=package_dir)
        run("npm", "audit", "--omit=dev", cwd=package_dir)
        run("npm", "pack", "--pack-destination", str(npm_output), cwd=package_dir)

    run(sys.executable, "scripts/validate_release_artifacts.py", str(output))
    print(f"Release artifacts are ready at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
