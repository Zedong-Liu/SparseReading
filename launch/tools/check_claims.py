#!/usr/bin/env python3
"""Fail if a public surface cites a claim that is missing or unapproved."""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[2]
CLAIMS_PATH = ROOT / "launch" / "claims.yaml"
SCAN_DIRS = [
    ROOT / "website",
    ROOT / "demo",
    ROOT / "launch" / "copy",
    ROOT / "assets" / "launch",
]
CLAIM_RE = re.compile(r"claim[_-]id[\"']?\s*[:=]\s*[\"']([a-z0-9-]+)[\"']", re.I)
ID_RE = re.compile(r"\b((?:paper|case|local|demo|fig8|fig7)-[a-z0-9-]+)\b")


def load_claims() -> dict[str, dict]:
    text = CLAIMS_PATH.read_text(encoding="utf-8")
    if yaml is not None:
        data = yaml.safe_load(text)
        return {item["id"]: item for item in data.get("claims", [])}
    ids = re.findall(r"^  - id: ([a-z0-9-]+)", text, re.M)
    approved = set(
        re.findall(
            r"- id: ([a-z0-9-]+)\n(?:.*\n)*?    approved_for_publication: true",
            text,
        )
    )
    return {
        cid: {"id": cid, "approved_for_publication": cid in approved} for cid in ids
    }


def main() -> int:
    claims = load_claims()
    approved = {k for k, v in claims.items() if v.get("approved_for_publication") is True}
    errors: list[str] = []
    for folder in SCAN_DIRS:
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".ts", ".tsx", ".js", ".astro", ".html", ".svg", ".py", ".json", ".yaml"}:
                continue
            if "node_modules" in path.parts or ".astro" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            cited = set(CLAIM_RE.findall(text))
            for cid in cited:
                if cid not in claims:
                    errors.append(f"{path}: unknown claim {cid}")
                elif cid not in approved:
                    errors.append(f"{path}: unapproved claim {cid}")
    print(f"claims loaded: {len(claims)}  approved: {len(approved)}")
    if errors:
        print("FAIL")
        for err in errors:
            print(" ", err)
        return 1
    print("PASS: no unapproved claim citations in launch surfaces")
    return 0


if __name__ == "__main__":
    sys.exit(main())
