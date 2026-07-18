import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator
from nanobot.sparse_reading.readers.text import TextUnit


def test_preview_csv_without_hintspec_returns_l0_card_and_raw_ref(tmp_path: Path) -> None:
    path = tmp_path / "events.csv"
    path.write_text(
        "id,status,latency\n"
        "1,ok,12\n"
        "2,ok,15\n"
        "3,error,-1\n"
        + "".join(f"{idx},ok,{idx}\n" for idx in range(4, 180)),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    preview = sro.preview({"path": str(path)})

    assert preview.error == ""
    assert preview.card["type"] == "csv"
    assert preview.card["artifact_id"].startswith("sro_")
    assert preview.structure["row_count"] == 179
    assert preview.structure["columns"] == ["id", "status", "latency"]
    assert preview.samples[0]["status"] == "ok"
    assert preview.raw_ref.startswith("raw:")
    assert preview.next_action
    assert "sro_read_with_goal" in preview.next_action["allowed_next"]
    assert "native" in preview.next_action["allowed_next"]
    assert "sro_raw" not in preview.next_action["allowed_next"]
    assert "one bounded local script" in preview.next_action["instruction"]

    raw = sro.raw(preview.raw_ref)
    assert "status,latency" in raw["content"]

    prefix_raw = sro.raw(":".join(preview.raw_ref.split(":")[:2]), selector="error")
    assert prefix_raw["raw_ref"] == preview.raw_ref
    assert prefix_raw["matches"][0]["text"] == "3,error,-1"

    stale_hash_raw = sro.raw(":".join(preview.raw_ref.split(":")[:2] + ["stalehash"]), selector="error")
    assert stale_hash_raw["raw_ref"] == preview.raw_ref
    assert stale_hash_raw["matches"][0]["text"] == "3,error,-1"


def test_preview_csv_counts_large_files_without_expanding_all_rows(tmp_path: Path) -> None:
    path = tmp_path / "large.csv"
    path.write_text(
        "id,status\n" + "".join(f"{idx},ok\n" for idx in range(1, 350)),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    preview = sro.preview({"path": str(path)})

    assert preview.error == ""
    assert preview.structure["row_count"] == 349
    assert len(preview.samples) == 5
    assert preview.samples[-1]["id"] == "5"


def test_preview_xlsx_exposes_formulas_without_dumping_the_workbook(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "formula_repair.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Input"
    ws.append(["name", "result"])
    ws.append(["Ada", "=VLOOKUP(A2,Lookup!A:B,2,FALSE)"])
    lookup = wb.create_sheet("Lookup")
    lookup.append(["name", "value"])
    lookup.append(["Ada ", 7])
    wb.save(path)
    wb.close()

    sro = SparseReadingOrchestrator(tmp_path)
    preview = sro.preview({"path": str(path)})

    assert preview.structure["formula_samples"] == [{
        "sheet": "Input",
        "cell": "B2",
        "formula": "=VLOOKUP(A2,Lookup!A:B,2,FALSE)",
    }]
    assert next(signal for signal in preview.signals if signal["kind"] == "formula_samples")["count"] == 1
    whitespace = next(signal for signal in preview.signals if signal["kind"] == "surrounding_whitespace")
    assert whitespace["sheet"] == "Lookup"
    assert whitespace["columns"] == ["name"]
    assert "double-quoted" in preview.next_action["instruction"]
    assert "$A$1" in preview.next_action["instruction"]
    assert "python3 (not python)" in preview.next_action["instruction"]
    assert "generated output and stop" in preview.next_action["instruction"]
    assert preview.next_action["overall_status"] == "ready_for_edit"
    assert "sro_read_with_goal" not in preview.next_action["allowed_next"]
    redundant = sro.read(
        {"artifact_id": preview.artifact_id},
        "focus",
        {"goal": "scan the full lookup table", "want": "table", "type_hint": "xlsx"},
    )
    assert redundant.evidence == []
    assert redundant.next_action["overall_status"] == "ready_for_edit"


def test_preview_xlsx_identifies_horizontal_formula_fill_range(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "calendar.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ATTENDENCE"
    ws["F3"] = '=TEXT(F4,"DD")'
    start = date(2026, 7, 1)
    for offset, column in enumerate(range(6, 37)):
        ws.cell(row=4, column=column, value=start + timedelta(days=offset))
        if column > 6:
            ws.cell(row=3, column=column, value="stale")
    wb.save(path)
    wb.close()

    sro = SparseReadingOrchestrator(tmp_path)
    preview = sro.preview({"path": str(path)})

    assert preview.structure["formula_fill_candidates"] == [{
        "sheet": "ATTENDENCE",
        "anchor_cell": "F3",
        "formula": '=TEXT(F4,"DD")',
        "fill_range": "F3:AJ3",
        "reference_range": "F4:AJ4",
    }]
    signal = next(item for item in preview.signals if item["kind"] == "horizontal_formula_fill")
    assert signal["fill_range"] == "F3:AJ3"
    assert preview.next_action["overall_status"] == "ready_for_edit"
    assert "not only F3" in preview.next_action["instruction"]
    assert "Do not call sro_read" in preview.next_action["instruction"]
    redundant = sro.read(
        {"artifact_id": preview.artifact_id},
        "focus",
        {"goal": "find other weekday formulas", "want": "fact", "type_hint": "xlsx"},
    )
    assert redundant.evidence == []
    assert redundant.next_action["overall_status"] == "ready_for_edit"


def test_preview_raw_refs_are_bounded(tmp_path: Path) -> None:
    sro = SparseReadingOrchestrator(tmp_path)
    sro._MAX_RAW_REFS = 3

    refs = []
    for idx in range(5):
        path = tmp_path / f"file-{idx}.txt"
        path.write_text(f"content {idx}\n" + "x\n" * 50, encoding="utf-8")
        refs.append(sro.preview({"path": str(path)}).raw_ref)

    assert len(sro._raw_refs) == 3
    assert sro.raw(refs[0])["error"] == "unknown or stale raw_ref; call sro_preview again"
    assert sro.raw(refs[-1])["content"].startswith("content 4")


def test_raw_pdf_returns_extracted_text_view(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF fake")
    sro = SparseReadingOrchestrator(tmp_path)

    def fake_units(_path: Path):
        return [
            TextUnit(anchor="p1:L1-L1", text="Registry data was collected on February 7, 2026."),
            TextUnit(anchor="p2:L1-L1", text="The paper proposes 6 new benchmark tasks."),
        ], [], "pdf"

    monkeypatch.setattr(sro.text_reader, "_load_units", fake_units)

    preview = sro.preview({"path": str(path)})
    raw = sro.raw(preview.raw_ref, selector="February 7")

    assert raw["view"] == "extracted_text"
    assert raw["matches"][0]["text"] == "p1:L1-L1 Registry data was collected on February 7, 2026."


def test_preview_json_array_exposes_schema_and_interesting_signal(tmp_path: Path) -> None:
    path = tmp_path / "records.json"
    path.write_text(
        json.dumps([
            {"id": 1, "status": "ok"},
            {"id": 2, "status": "rate_limited", "error": "timeout"},
        ]),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    preview = sro.preview({"path": str(path)})

    assert preview.error == ""
    assert preview.structure["shape"] == "array"
    assert preview.structure["length"] == 2
    assert any(signal["kind"] == "interesting_scalar" for signal in preview.signals)


def test_preview_log_deduplicates_levels_without_hint(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text(
        "\n".join(
            [
                "2026-01-01 00:00:00 INFO start job_id=abc",
                "2026-01-01 00:00:01 WARN retry job_id=abc",
                "2026-01-01 00:00:02 WARN retry job_id=def",
                "2026-01-01 00:00:03 ERROR timeout job_id=def",
            ]
        ),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    preview = sro.preview({"path": str(path)})

    assert preview.error == ""
    assert preview.structure["level_counts"]["WARN"] == 2
    assert preview.structure["level_counts"]["ERROR"] == 1
    assert any(sample["severity"] == "ERROR" for sample in preview.samples)


def test_preview_long_markdown_returns_skeleton_without_hint(tmp_path: Path) -> None:
    path = tmp_path / "research.md"
    path.write_text(
        "# Executive Summary\n\n"
        "Sparse reading should expose structure before goals.\n\n"
        "## Registry Findings\n\n"
        + "The registry contains repeated operational notes.\n" * 180
        + "\n## Proposed Tasks\n\n"
        + "The benchmark proposes concrete agent tasks.\n" * 120,
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    preview = sro.preview({"path": str(path)})

    assert preview.error == ""
    assert preview.card["type"] == "text"
    assert preview.summary.startswith("text object")
    assert preview.structure["unit_count"] >= 3
    assert any("Executive Summary" in item for item in preview.structure["skeleton"])
    assert preview.samples


def test_preview_collection_uses_grouped_file_card(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "config.yaml").write_text("enabled: true\n", encoding="utf-8")
    (root / "run.py").write_text("print('run')\n", encoding="utf-8")
    (root / "events.log").write_text("ERROR timeout\n" * 20, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    preview = sro.preview({"path": str(root)})

    assert preview.error == ""
    assert preview.card["type"] == "collection"
    assert preview.structure["file_count"] == 3
    assert preview.structure["kind_counts"]["log"] == 1
    assert any(signal["kind"] == "notable_files" for signal in preview.signals)

    raw = sro.raw(preview.raw_ref, selector="config.yaml")
    assert raw["type"] == "collection_child"
    assert raw["path"].endswith("config.yaml")
    assert "enabled: true" in raw["content"]


def test_collect_still_requires_hintspec_after_preview(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    path.write_text("Report\n\n" + "long text\n" * 800, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    preview = sro.preview({"path": str(path)})

    pack = sro.read({"artifact_id": preview.artifact_id}, "collect", {})

    assert pack.error
    assert "hint.goal is required" in pack.error or "collect requires" in pack.error
