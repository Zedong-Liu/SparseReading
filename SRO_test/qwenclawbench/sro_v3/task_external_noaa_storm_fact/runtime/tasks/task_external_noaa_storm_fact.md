---
id: task_external_noaa_storm_fact
name: NOAA Storm Events Local Event Fact
category: structured_csv_local_fact
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'noaa_storm_events_2023.csv', dest: 'noaa_storm_events_2023.csv'}
---

## Prompt

The workspace contains the NOAA Storm Events 2023 details CSV. For event ID `1109333`, report the state, event type, county or zone name, begin date/time, and magnitude. Write `answer.json` with exactly these keys: `state`, `event_type`, `cz_name`, `begin_date_time` (all strings), and `magnitude` (number).

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
        "state": float(answer.get("state") == "TEXAS"),
        "event_type": float(answer.get("event_type") == "Hail"),
        "cz_name": float(answer.get("cz_name") == "GRAYSON"),
        "begin_date_time": float(answer.get("begin_date_time") == "15-JUN-23 21:55:00"),
        "magnitude": float(answer.get("magnitude") == 1.25),
    }
```
