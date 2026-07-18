---
id: task_external_nasa_exoplanet_fact
name: NASA Exoplanet Archive Local Planet Fact
category: structured_csv_local_fact
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'nasa_pscomppars_2026-07-15.csv', dest: 'nasa_pscomppars_2026-07-15.csv'}
---

## Prompt

The workspace contains a CSV snapshot of the NASA Exoplanet Archive Planetary Systems Composite Parameters table. For planet `HD 109271 c`, report its discovery year and discovery method. Write `answer.json` with exactly these keys: `discovery_year` (integer) and `discovery_method` (string).

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
        "discovery_year": float(answer.get("discovery_year") == 2013),
        "discovery_method": float(answer.get("discovery_method") == "Radial Velocity"),
    }
```
