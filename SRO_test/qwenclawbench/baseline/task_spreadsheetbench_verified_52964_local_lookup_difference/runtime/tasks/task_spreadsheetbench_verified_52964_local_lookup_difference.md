---
id: task_spreadsheetbench_verified_52964_local_lookup_difference
name: SpreadsheetBench Verified 52964 local lookup difference
category: structured_spreadsheet
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'workbook.xlsx', dest: 'workbook.xlsx'}
---

## Prompt

Complete `workbook.xlsx` according to the original SpreadsheetBench Verified instruction below. Preserve the workbook and save the completed result as `answer.xlsx`.

I need to create a formula for cell C2 that locates the number from A2 within column Q, and where a match is found, the formula should subtract the value in the corresponding row of column Y from the value in column U. I aim to replicate this process for D2 and other cells as well. Additionally, I request an explanation of the formulas provided, specifically the meaning of the array constants {5,9} and {-1,1} used in the SUMPRODUCT and VLOOKUP functions.

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path
    from openpyxl import load_workbook
    try:
        wb = load_workbook(Path(workspace_path) / "answer.xlsx", data_only=True)
        value = wb.active["C2"].value
        matched = round(float(value), 2) == 17.0
    except Exception:
        matched = False
    return {"official_cell_match": float(matched)}
```
