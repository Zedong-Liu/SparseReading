---
id: task_external_usgs_earthquake_fact
name: USGS Earthquake Catalog Local Event Fact
category: structured_csv_local_fact
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'usgs_earthquakes_2024-01.csv', dest: 'usgs_earthquakes_2024-01.csv'}
---

## Prompt

The workspace contains a CSV export of the USGS Earthquake Catalog for January 2024. For event ID `mb90038833`, report its UTC time, magnitude, depth, and place. Write `answer.json` with exactly these keys: `time` (string), `magnitude` (number), `depth` (number), and `place` (string).

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
        "time": float(answer.get("time") == "2024-01-18T12:27:31.880Z"),
        "magnitude": float(answer.get("magnitude") == 0.64),
        "depth": float(answer.get("depth") == 8.19),
        "place": float(answer.get("place") == "28 km NE of Kelly, Wyoming"),
    }
```
