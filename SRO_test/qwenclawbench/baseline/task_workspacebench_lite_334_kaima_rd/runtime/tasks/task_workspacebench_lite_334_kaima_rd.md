---
id: task_workspacebench_lite_334_kaima_rd
name: Workspace-Bench-Lite 334 Kaima R&D Fact
category: multi_pdf_local_fact
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: '900939_2021_huili-b_-company-2021-annual-report_2022-04-28.pdf', dest: '900939_2021_huili-b_-company-2021-annual-report_2022-04-28.pdf'}
- {source: '900948_2021_yitai-b-share_inner-mongolia-yitai-coal-co-ltd-2021-annual-report_2022-03-30.pdf', dest: '900948_2021_yitai-b-share_inner-mongolia-yitai-coal-co-ltd-2021-annual-report_2022-03-30.pdf'}
- {source: '900953_2021_kaima-b_2021-annualannual-report-full-text_2022-04-27.pdf', dest: '900953_2021_kaima-b_2021-annualannual-report-full-text_2022-04-27.pdf'}
- {source: '900957_2021_lingyun-b-share_lingyun-b-share-2021-annual-report_2022-04-20.pdf', dest: '900957_2021_lingyun-b-share_lingyun-b-share-2021-annual-report_2022-04-20.pdf'}
---

## Prompt

The workspace contains four B-share companies' 2021 annual reports. Find the total R&D investment reported by Kaima B (900953) for 2021. Write only the amount in yuan, with exactly two decimal places and no currency symbol, to `answer.txt`.

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path
    p = Path(workspace_path) / "answer.txt"
    answer = p.read_text(encoding="utf-8").strip().replace(",", "") if p.is_file() else ""
    return {"exact_amount": float(answer == "87122954.71")}
```
