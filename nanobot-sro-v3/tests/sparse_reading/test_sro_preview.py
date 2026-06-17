import json
from pathlib import Path

from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator


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

    raw = sro.raw(preview.raw_ref)
    assert "status,latency" in raw["content"]


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


def test_collect_still_requires_hintspec_after_preview(tmp_path: Path) -> None:
    path = tmp_path / "report.md"
    path.write_text("Report\n\n" + "long text\n" * 800, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    preview = sro.preview({"path": str(path)})

    pack = sro.read({"artifact_id": preview.artifact_id}, "collect", {})

    assert pack.error
    assert "hint.goal is required" in pack.error or "collect requires" in pack.error
