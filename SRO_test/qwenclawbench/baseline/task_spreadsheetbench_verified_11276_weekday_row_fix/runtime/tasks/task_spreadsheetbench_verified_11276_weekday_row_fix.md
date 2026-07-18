---
id: task_spreadsheetbench_verified_11276_weekday_row_fix
name: SpreadsheetBench Verified 11276 weekday row fix
category: structured_spreadsheet
grading_type: automated
timeout_seconds: 600
workspace_files:
- {source: 'workbook.xlsx', dest: 'workbook.xlsx'}
---

## Prompt

Complete `workbook.xlsx` according to the original SpreadsheetBench Verified instruction below. Preserve the workbook and save the completed result as `answer.xlsx`.

I am using the TEXT formula from F3:AJ3 in my Excel sheet, which is meant to return the weekday name, but it is incorrectly returning the date value instead. Correct the formula to display the weekday like the other cells that are correctly showing this information? For example, Mon, Wed... etc. Please apply this formula to the rest of the appropriate cells in that row.

My wrong formula: TEXT(F4,"DD")

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    expected = ["Wed", "Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
    answer = Path(workspace_path) / "answer.xlsx"
    actual = []
    try:
        wb = load_workbook(answer, data_only=True)
        ws = wb["ATTENDENCE"]
        actual = [ws.cell(3, col).value for col in range(6, 37)]
        wb.close()
        if actual != expected:
            import formulas
            calculated = formulas.ExcelModel().loads(str(answer)).finish().calculate()
            def formula_value(cell):
                suffix = "ATTENDENCE'!" + cell.upper()
                for key, value in calculated.items():
                    if str(key).upper().endswith(suffix):
                        raw = getattr(value, "value", value)
                        raw = raw.tolist() if hasattr(raw, "tolist") else raw
                        while isinstance(raw, list) and raw:
                            raw = raw[0]
                        return raw
                return None
            actual = [formula_value(f"{get_column_letter(col)}3") for col in range(6, 37)]
    except Exception:
        actual = []
    return {"official_cell_match": float(actual == expected)}
```
