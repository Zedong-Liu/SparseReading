---
id: task_external_nyc311_fact
name: NYC 311 Local Request Fact
category: structured_csv_local_fact
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'nyc_311_2024-01-01.csv', dest: 'nyc_311_2024-01-01.csv'}
---

## Prompt

The workspace contains an official NYC Open Data CSV export of 311 requests created on January 1, 2024. For unique key `59897450`, report the created date/time, agency, complaint type, and status. Write `answer.json` with exactly these keys: `created_date`, `agency`, `complaint_type`, and `status`, all strings.

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
        "created_date": float(answer.get("created_date") == "2024-01-01T13:30:40.000"),
        "agency": float(answer.get("agency") == "NYPD"),
        "complaint_type": float(answer.get("complaint_type") == "Illegal Parking"),
        "status": float(answer.get("status") == "Closed"),
    }
```
