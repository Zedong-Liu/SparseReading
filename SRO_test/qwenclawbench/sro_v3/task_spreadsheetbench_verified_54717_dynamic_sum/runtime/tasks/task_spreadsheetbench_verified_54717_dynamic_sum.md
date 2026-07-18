---
id: task_spreadsheetbench_verified_54717_dynamic_sum
name: SpreadsheetBench Verified 54717 dynamic sum
category: structured_spreadsheet
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'workbook.xlsx', dest: 'workbook.xlsx'}
---

## Prompt

Complete `workbook.xlsx` according to the original SpreadsheetBench Verified instruction below. Preserve the workbook and save the completed result as `answer.xlsx`.

How do I use Excel to sum the values in a specific column based on particular start and end row numbers, where the column to search and the row numbers are determined by the values in predefined cells?

These numbers represent the amount owner contributes in the 1.9 thread.

The border means this is the point when P1 and P2 auto prime.

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path
    from openpyxl import load_workbook
    try:
        ws = load_workbook(Path(workspace_path) / "answer.xlsx", data_only=False)["Sheet1"]
        formula = str(ws["S10"].value or "").replace(" ", "").upper()
        required = ("SUMIFS(", "INDEX(", "MATCH(", "$B$3:$P$82", "$B$2:$P$2", 'S4', 'S6', 'S8')
        matched = formula.startswith("=") and all(token in formula for token in required)
    except Exception:
        matched = False
    return {"official_cell_match": float(matched)}
```
