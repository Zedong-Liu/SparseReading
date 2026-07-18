---
id: task_spreadsheetbench_verified_57033_three_way_match
name: SpreadsheetBench Verified 57033 three-way match
category: structured_spreadsheet
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'workbook.xlsx', dest: 'workbook.xlsx'}
---

## Prompt

Complete `workbook.xlsx` according to the original SpreadsheetBench Verified instruction below. Preserve the workbook and save the completed result as `answer.xlsx`.

How can I use a formula to perform a 3-way match in cell K2 of Sheet 4 in my Excel workbook? I've attempted to use an INDEX MATCH formula to reference items from Sheet 4, but it has been unsuccessful, and I am unclear on how to correctly set it up for a 3-way match. I have attached the workbook for reference. Once complete apply regular casing and cell shading with hex code #FF66CC to the affected cells. Please place the correct values into K2:K7.

My current formula is: IF(SUM(([@company]=MD[company])*([@account]=MD[account])*([@xchar]=MD[xchar])),"Match","-")

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path
    from openpyxl import load_workbook
    expected = ["Match", "-", "-", "Match", "Match", "Match"]
    try:
        wb = load_workbook(Path(workspace_path) / "answer.xlsx", data_only=True)
        actual = [wb["Sheet4"][f"K{row}"].value for row in range(2, 8)]
    except Exception:
        actual = []
    return {"official_cell_match": float(actual == expected)}
```
