from __future__ import annotations

import ast
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORE_SOURCE = ROOT / "packages" / "sparseread-core" / "src" / "sparseread"


def imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    for source in path.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def test_core_has_no_framework_imports() -> None:
    assert not (
        {
            "nanobot",
            "sparseread_nanobot",
            "sparseread_opencode",
            "sparseread_openclaw",
            "sparseread_claude",
            "mcp",
        }
        & imported_roots(CORE_SOURCE)
    )


def test_each_framework_adapter_is_an_independent_distribution() -> None:
    expected = {
        "nanobot": "sparseread-nanobot",
        "opencode": "sparseread-opencode",
        "openclaw": "sparseread-openclaw",
        "claude": "sparseread-claude",
    }
    for framework, distribution in expected.items():
        pyproject = ROOT / "integrations" / framework / "python" / "pyproject.toml"
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        assert payload["project"]["name"] == distribution
        expected_deps = (
            ["sparseread-core>=0.1,<0.2", "mcp>=1.26,<2.0"]
            if framework == "claude"
            else ["sparseread-core>=0.1,<0.2"]
        )
        assert payload["project"]["dependencies"] == expected_deps


def test_javascript_plugins_are_publishable_and_versioned() -> None:
    for framework in ("opencode", "openclaw"):
        package = json.loads(
            (ROOT / "integrations" / framework / "plugin" / "package.json").read_text(encoding="utf-8")
        )
        assert package["name"] == f"@sparseread/{framework}"
        assert package["private"] is False
        assert package["version"] == "0.1.0"
        assert package["scripts"]["prepack"] == "npm run build"


def test_release_has_one_canonical_framework_source() -> None:
    assert not (ROOT / "opencode_pilot").exists()
    assert not (ROOT / "openclaw_pilot").exists()
