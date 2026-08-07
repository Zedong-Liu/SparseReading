from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))

import nanobot_sro_driver as driver  # noqa: E402


def test_extract_grade_code_picks_def_grade_block() -> None:
    content = (
        "## Prompt\n"
        "```python\n"
        "state = ['x']\n"
        "state['seen_ids'] = list(seen)[-5000:]\n"
        "```\n"
        "## Automated Checks\n"
        "```python\n"
        "def grade(transcript, workspace_path):\n"
        "    return {'ok': 1.0}\n"
        "```\n"
    )

    code = driver.extract_grade_code(content)

    assert "def grade" in code
    assert "state['seen_ids']" not in code


def test_needs_retry_only_for_infrastructure_failures() -> None:
    assert driver._needs_retry(
        {"stop_reason": "empty_final_response", "timed_out": False}, 0, 2
    )
    assert driver._needs_retry({"timed_out": True, "stop_reason": "timeout"}, 0, 2)
    assert not driver._needs_retry(
        {"stop_reason": "completed", "score": 0.0, "breakdown": {}}, 0, 2
    )
    assert not driver._needs_retry({"stop_reason": "completed", "score": 0.3}, 0, 2)
