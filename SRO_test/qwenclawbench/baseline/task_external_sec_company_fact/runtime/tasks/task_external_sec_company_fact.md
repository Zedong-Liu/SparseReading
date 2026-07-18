---
id: task_external_sec_company_fact
name: SEC EDGAR Company Metadata Fact
category: structured_json_local_fact
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'sec_msft_submissions_2026-07-15.json', dest: 'sec_msft_submissions_2026-07-15.json'}
---

## Prompt

The workspace contains an SEC EDGAR submissions JSON file for CIK `0000789019`. Report the entity name, its ticker, and its exchange. Write `answer.json` with exactly these keys: `name`, `ticker`, and `exchange`, all strings.

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    import json
    from pathlib import Path
    p = Path(workspace_path) / "answer.json"
    try:
        answer = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        answer = {}
    return {
        "name": float(answer.get("name") == "MICROSOFT CORP"),
        "ticker": float(answer.get("ticker") == "MSFT"),
        "exchange": float(answer.get("exchange") == "Nasdaq"),
    }
```
