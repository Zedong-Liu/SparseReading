from __future__ import annotations

from pathlib import Path

from sparseread_openclaw.bridge import OpenClawBridge
from sparseread_opencode.bridge import OpenCodeBridge


BRIDGES = (OpenCodeBridge, OpenClawBridge)


def _preview(bridge, path: Path) -> dict:
    return bridge.handle({"method": "preview", "params": {"path": str(path)}})["preview_pack"]


def _read(bridge, artifact_id: str, hint: dict, mode: str = "collect") -> dict:
    return bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": mode,
                "hint": hint,
            },
        }
    )["evidence_pack"]


def test_release_fixture_long_markdown_key_values(tmp_path: Path) -> None:
    target = tmp_path / "incident-report.md"
    lines = ["# Incident Report\n\n"]
    for idx in range(1, 260):
        lines.append(f"## Section {idx}\nRoutine telemetry stayed normal.\n")
        if idx == 42:
            lines.append("ROOT_CAUSE: cache invalidation used customer_id instead of tenant_id.\n")
        if idx == 137:
            lines.append("MITIGATION_OWNER: Mira Chen, Data Platform on-call.\n")
        if idx == 211:
            lines.append("FINAL_DEADLINE: 2026-07-18 09:30 UTC.\n")
        lines.append("\n")
    target.write_text("".join(lines), encoding="utf-8")

    for bridge_cls in BRIDGES:
        bridge = bridge_cls(workspace=tmp_path, mode="force")
        pack = _preview(bridge, target)
        evidence = _read(
            bridge,
            pack["artifact_id"],
            {
                "goal": "Extract incident fields",
                "type_hint": "text",
                "slots": [
                    {"id": "root_cause", "question": "What is ROOT_CAUSE?"},
                    {"id": "owner", "question": "Who is MITIGATION_OWNER?"},
                    {"id": "deadline", "question": "What is FINAL_DEADLINE?"},
                ],
            },
        )
        slots = {slot["id"]: slot for slot in evidence["slot_digest"]["slots"]}

        assert pack["compression"]["recipe"].startswith("l0_text_")
        assert slots["root_cause"]["candidate"] == (
            "cache invalidation used customer_id instead of tenant_id."
        )
        assert slots["owner"]["candidate"] == "Mira Chen, Data Platform on-call."
        assert slots["deadline"]["candidate"] == "2026-07-18 09:30 UTC."


def test_release_fixture_log_preview_and_selector(tmp_path: Path) -> None:
    target = tmp_path / "app.log"
    target.write_text(
        "\n".join(
            ["2026-07-04T00:00:00Z INFO ok"] * 25
            + ["2026-07-04T00:01:00Z ERROR payment timeout request_id=req-42"]
        ),
        encoding="utf-8",
    )

    for bridge_cls in BRIDGES:
        bridge = bridge_cls(workspace=tmp_path, mode="force")
        pack = _preview(bridge, target)
        raw = bridge.handle(
            {"method": "raw", "params": {"raw_ref": pack["raw_ref"], "selector": "ERROR"}}
        )["raw"]

        assert pack["compression"]["recipe"] == "l0_log_dedup_levels"
        assert pack["structure"]["level_counts"]["ERROR"] == 1
        assert pack["signals"][0]["kind"] == "repeated_lines"
        assert raw["matches"][0]["text"].endswith("request_id=req-42")


def test_release_fixture_csv_schema_preview(tmp_path: Path) -> None:
    target = tmp_path / "events.csv"
    target.write_text(
        "id,status,latency\n1,ok,12\n2,error,900\n3,ok,15\n",
        encoding="utf-8",
    )

    for bridge_cls in BRIDGES:
        bridge = bridge_cls(workspace=tmp_path, mode="force")
        pack = _preview(bridge, target)

        assert pack["compression"]["recipe"] == "l0_csv_schema_sample_signals"
        assert pack["structure"]["columns"] == ["id", "status", "latency"]
        assert pack["structure"]["row_count"] == 3
        assert pack["signals"][0]["values"] == ["error"]


def test_release_fixture_json_schema_preview(tmp_path: Path) -> None:
    target = tmp_path / "events.json"
    target.write_text(
        '[{"id":1,"status":"ok"},{"id":2,"status":"error","detail":{"code":"E42"}}]',
        encoding="utf-8",
    )

    for bridge_cls in BRIDGES:
        bridge = bridge_cls(workspace=tmp_path, mode="force")
        pack = _preview(bridge, target)

        assert pack["compression"]["recipe"] == "l0_json_schema_sample_signals"
        assert pack["structure"]["shape"] == "array"
        assert pack["structure"]["length"] == 2
        assert pack["signals"][0]["path"] == "$[1].status"


def test_release_fixture_yaml_schema_preview(tmp_path: Path) -> None:
    target = tmp_path / "config.yaml"
    target.write_text(
        "service:\n  name: gateway\n  retries: 3\nalerts:\n  - error_rate\n",
        encoding="utf-8",
    )

    for bridge_cls in BRIDGES:
        bridge = bridge_cls(workspace=tmp_path, mode="force")
        pack = _preview(bridge, target)

        assert pack["compression"]["recipe"] == "l0_yaml_schema_sample_signals"
        assert pack["structure"]["keys"] == ["service", "alerts"]
        assert pack["structure"]["children"]["service"]["keys"] == ["name", "retries"]
        assert pack["signals"][0]["value"] == "error_rate"


def test_release_fixture_xml_schema_preview(tmp_path: Path) -> None:
    target = tmp_path / "feed.xml"
    target.write_text(
        '<root><item id="1"><status>ok</status></item>'
        '<item id="2"><status>error</status></item></root>',
        encoding="utf-8",
    )

    for bridge_cls in BRIDGES:
        bridge = bridge_cls(workspace=tmp_path, mode="force")
        pack = _preview(bridge, target)

        assert pack["compression"]["recipe"] == "l0_xml_root_schema_sample"
        assert pack["structure"]["root"] == "root"
        assert pack["structure"]["child_counts"] == {"item": 2}
        assert any(sample.get("text") == "error" for sample in pack["samples"])
