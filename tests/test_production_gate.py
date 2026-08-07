from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sparseread.core.benefit_gate import BenefitDecision, GateContext
from sparseread.core.episode import EpisodeController
from sparseread.core.models import EvidenceBlock, EvidencePack
from sparseread.core.orchestrator import SparseReadingOrchestrator
from sparseread.core.tools import SroPreviewTool, SroReadTool
from sparseread_openclaw.bridge import classify_openclaw_gate
from sparseread_opencode.bridge import OpenCodeBridge, classify_opencode_gate


def _mixed_bundle(root: Path, *, opaque: bool = False) -> Path:
    root.mkdir(parents=True)
    names = (
        ("a.py", "b.json", "c.yaml", "d.log", "e.md")
        if opaque
        else ("fetcher.py", "state.json", "config.yaml", "events.log", "report.md")
    )
    bodies = (
        "def run():\n    return True\n" * 80,
        '{"items": [1, 2, 3]}\n' * 80,
        "enabled: true\n" * 80,
        "2026-01-01 INFO completed\n" * 80,
        "# Evidence\nCross-source notes.\n" * 80,
    )
    for name, body in zip(names, bodies, strict=True):
        (root / name).write_text(body, encoding="utf-8")
    return root


def _multitable_bundle(root: Path, *, opaque: bool = False) -> Path:
    root.mkdir(parents=True)
    names = (
        ("a.csv", "b.tsv", "c.csv", "d.json", "e.yaml")
        if opaque
        else ("current.csv", "history.tsv", "benchmarks.csv", "metadata.json", "settings.yaml")
    )
    bodies = (
        "id,value\n" + "1,2\n" * 1_200,
        "id\tvalue\n" + "1\t2\n" * 1_200,
        "id,value\n" + "1,2\n" * 1_200,
        '{"rows": [1, 2, 3]}\n' * 200,
        "enabled: true\n" * 200,
    )
    for name, body in zip(names, bodies, strict=True):
        (root / name).write_text(body, encoding="utf-8")
    return root


def _decision(path: Path, context: dict[str, str] | None = None):
    sro = SparseReadingOrchestrator(path if path.is_dir() else path.parent)
    return sro.benefit_gate.decide(sro.inspect(path), context)


def test_gate_v2_rule_matrix(tmp_path: Path) -> None:
    unsupported = tmp_path / "blob.bin"
    unsupported.write_bytes(b"x" * 20_000)
    small = tmp_path / "small.md"
    small.write_text("small\n" * 100, encoding="utf-8")
    code = tmp_path / "large.py"
    code.write_text("print('x')\n" * 2_000, encoding="utf-8")
    long_text = tmp_path / "document.txt"
    long_text.write_text("historical evidence and context\n" * 4_000, encoding="utf-8")
    structured = tmp_path / "rows.csv"
    structured.write_text("id,value\n" + "\n".join(f"{i},{i}" for i in range(8_000)), encoding="utf-8")
    mixed = _mixed_bundle(tmp_path / "mixed")
    structured_collection = _multitable_bundle(tmp_path / "tables")
    small_structured_collection = tmp_path / "small_tables"
    small_structured_collection.mkdir()
    for index in range(3):
        (small_structured_collection / f"{index}.csv").write_text("id,value\n" + "1,2\n" * 600, encoding="utf-8")

    cases = [
        (unsupported, {"goal": "selective_read"}, "native", "unsupported", False),
        (small, {"goal": "selective_read"}, "native", "below_benefit_floor", False),
        (code, {"goal": "selective_read"}, "native", "native_code_or_config", False),
        (long_text, {"goal": "selective_read"}, "force_sro", "long_document_selective", True),
        (long_text, None, "force_sro", "long_document", True),
        (long_text, {"goal": "full_fidelity"}, "native", "native_full_fidelity", False),
        (structured, {"goal": "selective_read"}, "advisory", "structured_targeted_only", True),
        (mixed, {"goal": "cross_file_evidence"}, "force_sro", "multi_file_evidence", True),
        (mixed, {"goal": "edit_or_execute"}, "native", "native_edit_or_execute", False),
        (mixed, None, "advisory", "collection_goal_required", True),
        (
            small_structured_collection,
            {"goal": "cross_file_evidence"},
            "advisory",
            "multi_file_evidence_boundary",
            True,
        ),
        (
            structured_collection,
            {"goal": "structured_compute"},
            "force_sro",
            "structured_analysis_plan",
            True,
        ),
        (
            structured_collection,
            {"goal": "edit_or_execute"},
            "force_sro",
            "structured_analysis_plan",
            True,
        ),
    ]
    for path, context, mode, code_name, preview in cases:
        decision = _decision(path, context)
        assert (decision.mode, decision.code, decision.preview_recommended) == (mode, code_name, preview)


def test_gate_collection_decision_is_name_and_order_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    named = _mixed_bundle(tmp_path / "named")
    opaque = _mixed_bundle(tmp_path / "opaque", opaque=True)
    context = {"goal": "cross_file_evidence"}
    named_decision = _decision(named, context)
    opaque_sro = SparseReadingOrchestrator(opaque)
    original_items = opaque_sro.collection_reader._items
    monkeypatch.setattr(opaque_sro.collection_reader, "_items", lambda path: list(reversed(original_items(path))))
    opaque_decision = opaque_sro.benefit_gate.decide(opaque_sro.inspect(opaque), context)

    assert (named_decision.mode, named_decision.code, named_decision.preview_recommended) == (
        opaque_decision.mode,
        opaque_decision.code,
        opaque_decision.preview_recommended,
    )


def test_edit_multitable_decision_is_name_and_order_invariant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    named = _multitable_bundle(tmp_path / "named_tables")
    opaque = _multitable_bundle(tmp_path / "opaque_tables", opaque=True)
    context = {"goal": "edit_or_execute"}
    named_decision = _decision(named, context)
    opaque_sro = SparseReadingOrchestrator(opaque)
    original_items = opaque_sro.collection_reader._items
    monkeypatch.setattr(opaque_sro.collection_reader, "_items", lambda path: list(reversed(original_items(path))))
    opaque_decision = opaque_sro.benefit_gate.decide(opaque_sro.inspect(opaque), context)

    assert (named_decision.mode, named_decision.code) == ("force_sro", "structured_analysis_plan")
    assert (opaque_decision.mode, opaque_decision.code) == (named_decision.mode, named_decision.code)


def test_edit_structured_prelude_requires_tabular_dominance_without_logs(tmp_path: Path) -> None:
    config_heavy = tmp_path / "config_heavy"
    config_heavy.mkdir()
    for index in range(4):
        (config_heavy / f"{index}.json").write_text('{"enabled": true}\n' * 400, encoding="utf-8")
    (config_heavy / "rows.csv").write_text("id,value\n" + "1,2\n" * 1_200, encoding="utf-8")

    text_heavy = tmp_path / "text_heavy"
    text_heavy.mkdir()
    for index in range(3):
        (text_heavy / f"{index}.csv").write_text("id,value\n" + "1,2\n" * 1_200, encoding="utf-8")
    for index in range(4):
        (text_heavy / f"{index}.md").write_text("supporting notes\n" * 400, encoding="utf-8")

    logged = tmp_path / "logged"
    logged.mkdir()
    for index in range(3):
        (logged / f"{index}.csv").write_text("id,value\n" + "1,2\n" * 1_200, encoding="utf-8")
    (logged / "notes.md").write_text("analysis notes\n" * 300, encoding="utf-8")
    (logged / "events.log").write_text("INFO completed\n" * 300, encoding="utf-8")

    edit_context = {"goal": "edit_or_execute"}
    for collection in (config_heavy, text_heavy, logged):
        decision = _decision(collection, edit_context)
        assert (decision.mode, decision.code) == ("native", "native_edit_or_execute")

    explicit_compute = _decision(logged, {"goal": "structured_compute"})
    assert (explicit_compute.mode, explicit_compute.code) == ("force_sro", "structured_analysis_plan")


def test_edit_episode_structured_prelude_returns_ready_compute_closure(tmp_path: Path) -> None:
    data = tmp_path / "data"
    scripts = tmp_path / "scripts"
    data.mkdir()
    scripts.mkdir()
    rows = ["firm_id,year,treated,post,did,revenue_growth_pct,industry"]
    for index in range(1_200):
        treated = int(index % 2 == 0)
        post = int(index % 10 >= 5)
        rows.append(
            f"F{index % 30:03d},{2015 + index % 10},{treated},{post},{treated * post},"
            f"{10 + index % 7}.0,Sector{index % 4}"
        )
    (data / "panel.csv").write_text("\n".join(rows), encoding="utf-8")
    (data / "metadata.csv").write_text(
        "firm_id,firm_name,industry\nF000,Alpha,Sector0\nF001,Beta,Sector1\n",
        encoding="utf-8",
    )
    (data / "dictionary.json").write_text(
        '{"notes":["True DGP DID coefficient: 3.5"]}',
        encoding="utf-8",
    )
    (data / "benchmarks.csv").write_text("industry,value\nSector0,1\n", encoding="utf-8")
    (data / "quarterly.csv").write_text("firm_id,quarter,value\nF000,1,1\n", encoding="utf-8")
    (scripts / "analysis.py").write_text("# TODO: replace naive template\n", encoding="utf-8")

    orchestrator = SparseReadingOrchestrator(tmp_path)
    decision = orchestrator.benefit_gate.decide(
        orchestrator.inspect(tmp_path),
        {"goal": "edit_or_execute", "relation": "new"},
    )
    assert (decision.mode, decision.code) == ("force_sro", "structured_analysis_plan")

    orchestrator.bind_episode(
        tmp_path,
        {
            "goal": "edit_or_execute",
            "relation": "new",
            "summary": "Write a DID regression over the panel data",
        },
    )
    handoff = json.loads(orchestrator.handoff_message(tmp_path))
    assert handoff["file_card"]["sparse_recommended"] is True
    assert handoff["file_card"]["recommended_mode"] == "collect"
    assert handoff["next_action"]["hint"]["goal"] == "Write a DID regression over the panel data"
    assert "one bounded SRO collect" in handoff["message"]

    card = orchestrator.card(tmp_path)
    pack = orchestrator.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "write and run the DID script",
            "type_hint": "collection",
            "scope": "new",
            "slots": [
                {
                    "id": "panel_schema",
                    "question": "Exact panel columns and firm/year fixed-effects contract",
                    "expected": "schema",
                }
            ],
        },
        {"goal": "edit_or_execute", "relation": "new"},
    )
    assert [block.anchor for block in pack.evidence] == ["collection_panel_did_closure"]
    assert pack.next_action["overall_status"] == "ready_for_compute"
    assert pack.next_action["required_outputs"] == ["did_regression.py", "did_results_summary.md"]
    assert pack.unresolved == []


def test_gate_ignores_decoy_benchmark_keywords(tmp_path: Path) -> None:
    bundle = _mixed_bundle(tmp_path / "audit_fetch_state_output")
    decision = _decision(bundle)
    assert (decision.mode, decision.code) == ("advisory", "collection_goal_required")


def test_large_text_collection_waits_for_episode_goal(tmp_path: Path) -> None:
    collection = tmp_path / "documents"
    collection.mkdir()
    for index in range(8):
        (collection / f"{index}.md").write_text("evidence\n" * 500, encoding="utf-8")
    unknown = _decision(collection)
    evidence = _decision(
        collection,
        {"goal": "cross_file_evidence", "coverage": "selective"},
    )
    editing = _decision(collection, {"goal": "edit_or_execute"})
    assert (unknown.mode, unknown.code) == ("advisory", "large_text_collection_boundary")
    assert (evidence.mode, evidence.code) == ("force_sro", "multi_file_evidence")
    assert editing.mode == "native"


def test_sro_read_cannot_bypass_explicit_native_episode(tmp_path: Path) -> None:
    collection = _mixed_bundle(tmp_path / "query_project")
    orchestrator = SparseReadingOrchestrator(tmp_path)
    tool = SroReadTool(orchestrator)
    tool.set_context(SimpleNamespace(session_key="chat:one", message_id="m1"))
    result = json.loads(asyncio.run(tool.execute(
        target={"path": str(collection)},
        mode="collect",
        hint={
            "goal": "read supporting docs before writing a query",
            "type_hint": "collection",
            "episode_hint": {
                "goal": "edit_or_execute",
                "relation": "new",
                "coverage": "selective",
            },
        },
    )))

    pack = result["evidence_pack"]
    assert pack["evidence"] == []
    assert pack["summary"].startswith("low-sparse fallback")
    assert result["decision"]["mode"] == "native"


def test_nonready_collection_child_guard_requires_focused_resolution(tmp_path: Path) -> None:
    audit = _mixed_bundle(tmp_path / "audit")
    orchestrator = SparseReadingOrchestrator(tmp_path)
    card = orchestrator.card(audit)
    partial = EvidencePack(
        artifact_id=card.artifact_id,
        mode="collect",
        type="collection",
        summary="partial evidence",
        evidence=[EvidenceBlock(anchor="fetcher.py", text="partial excerpt")],
        next_action={"overall_status": "needs_verify"},
    )
    orchestrator._remember_collection_children(audit, card.artifact_id, partial)

    payload = json.loads(orchestrator._collection_child_guard(audit / "fetcher.py"))
    assert payload["evidence_complete_for_source"] is False
    assert payload["allowed_next"] == ["sro_read"]
    assert payload["next_action"]["mode"] == "focus"

def test_bridge_preview_uses_same_one_shot_hint_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    audit = _mixed_bundle(tmp_path / "audit")
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")

    first = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(audit),
            "context": {"conversation_id": "one", "turn_id": "t1"},
        },
    })
    assert first["sro_gate_probe"] is True

    second = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(audit),
            "context": {"conversation_id": "one", "turn_id": "t1"},
        },
    })
    assert second["preview_pack"]["card"]["type"] == "collection"
    assert second["decision"]["mode"] == "advisory"

    recovered = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(audit),
            "context": {"conversation_id": "one", "turn_id": "t2"},
            "episode_hint": {
                "relation": "new",
                "goal": "cross_file_evidence",
                "coverage": "selective",
            },
        },
    })
    assert recovered["decision"]["mode"] == "force_sro"


def test_gate_does_not_force_tiny_text_collection(tmp_path: Path) -> None:
    bundle = tmp_path / "tiny_texts"
    bundle.mkdir()
    for index in range(3):
        (bundle / f"{index}.md").write_text("x\n", encoding="utf-8")

    decision = _decision(bundle)

    assert (decision.mode, decision.code) == ("advisory", "collection_goal_required")


def test_gate_keeps_long_pdf_route_with_small_sidecar(tmp_path: Path) -> None:
    bundle = tmp_path / "document_bundle"
    bundle.mkdir()
    (bundle / "report.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 20_000)
    (bundle / "README.md").write_text("context\n", encoding="utf-8")

    decision = _decision(bundle)
    full_fidelity = _decision(bundle, {"goal": "full_fidelity"})

    assert (decision.mode, decision.code) == ("force_sro", "collection_long_document")
    assert (full_fidelity.mode, full_fidelity.code) == ("native", "native_full_fidelity")


def test_model_hint_cannot_override_native_veto(tmp_path: Path) -> None:
    code = tmp_path / "audit_report.py"
    code.write_text("print('audit')\n" * 2_000, encoding="utf-8")
    decision = _decision(code, {"goal": "cross_file_evidence", "relation": "new"})
    assert (decision.mode, decision.code) == ("native", "native_code_or_config")


def test_adapters_use_structured_decision_not_reason(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text("evidence\n" * 3_000, encoding="utf-8")
    info = SparseReadingOrchestrator(tmp_path).inspect(target)
    first = BenefitDecision(
        "force_sro",
        "arbitrary prose one",
        0.9,
        "collect",
        code="long_document_selective",
        preview_recommended=True,
        scope_kind="single_document",
    )
    second = BenefitDecision(
        "force_sro",
        "completely unrelated prose two",
        0.9,
        "collect",
        code="long_document_selective",
        preview_recommended=True,
        scope_kind="single_document",
    )
    for classifier in (classify_opencode_gate, classify_openclaw_gate):
        left = classifier(info, first)
        right = classifier(info, second)
        left.pop("reason")
        right.pop("reason")
        assert left == right
        assert left["mode"] == "enforce"
        assert left["decision_code"] == "long_document_selective"


def test_episode_reuses_lease_then_switches_scope(tmp_path: Path) -> None:
    controller = EpisodeController()
    force = BenefitDecision("force_sro", "long", 0.9, "collect", code="long_document")
    native = BenefitDecision("native", "compute", 0.9, "native_read", code="native_compute_or_edit")
    first_path = tmp_path / "one.txt"
    second_path = tmp_path / "two.csv"

    first_decision, first = controller.bind(
        conversation_id="c1",
        turn_id="t1",
        scope=first_path,
        proposed=force,
        hint=GateContext(goal="selective_read", relation="new"),
    )
    repeated_decision, repeated = controller.bind(
        conversation_id="c1",
        turn_id="t1",
        scope=first_path,
        proposed=native,
        hint=GateContext(goal="selective_read", relation="continue"),
    )
    switched_decision, switched = controller.bind(
        conversation_id="c1",
        turn_id="t2",
        scope=second_path,
        proposed=native,
        hint=GateContext(goal="compute_or_edit", relation="switch"),
    )

    assert repeated.episode_id == first.episode_id
    assert repeated_decision == first_decision
    assert switched.episode_id != first.episode_id
    assert switched_decision.mode == "native"


def test_bridge_ready_is_isolated_by_conversation(tmp_path: Path) -> None:
    target = tmp_path / "report.md"
    target.write_text(
        "The registry contains 5,705 skills.\nThe gateway uses a typed WebSocket API.\n" * 300,
        encoding="utf-8",
    )
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    context_one = {"conversation_id": "one", "turn_id": "t1"}
    context_two = {"conversation_id": "two", "turn_id": "t1"}
    preview = bridge.handle(
        {
            "method": "preview",
            "params": {
                "path": str(target),
                "context": context_one,
                "episode_hint": {"goal": "selective_read", "relation": "new"},
            },
        }
    )
    artifact_id = preview["preview_pack"]["artifact_id"]
    first = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "answer two questions",
                    "type_hint": "text",
                    "slots": {
                        "skills": "How many skills?",
                        "api": "Which API?",
                    },
                },
                "context": context_one,
            },
        }
    )
    second_conversation = bridge.handle(
        {
            "method": "decide",
            "params": {
                "path": str(target),
                "context": context_two,
                "episode_hint": {"goal": "selective_read", "relation": "new"},
            },
        }
    )
    second_preview = bridge.handle(
        {
            "method": "preview",
            "params": {
                "path": str(target),
                "context": context_two,
                "episode_hint": {"goal": "selective_read", "relation": "new"},
            },
        }
    )
    second_artifact_id = second_preview["preview_pack"]["artifact_id"]
    second_read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": second_artifact_id},
                "mode": "collect",
                "hint": {
                    "goal": "answer two questions",
                    "type_hint": "text",
                    "slots": {
                        "skills": "How many skills?",
                        "api": "Which API?",
                    },
                },
                "context": context_two,
                "episode_hint": {"goal": "selective_read", "relation": "continue"},
            },
        }
    )

    assert first["evidence_pack"]["episode_status"] == "evidence_ready"
    assert second_conversation["opencode_gate"].get("already_ready") is not True
    assert second_conversation["episode"]["conversation_id"] == "two"
    assert second_artifact_id != artifact_id
    assert not second_read["evidence_pack"]["summary"].startswith("adapter ready guard")

    foreign_read = bridge.handle(
        {
            "method": "read",
            "params": {
                "target": {"artifact_id": artifact_id},
                "mode": "collect",
                "hint": {"goal": "find total", "type_hint": "text"},
                "context": context_two,
            },
        }
    )
    foreign_raw = bridge.handle(
        {
            "method": "raw",
            "params": {
                "raw_ref": preview["preview_pack"]["raw_ref"],
                "context": context_two,
            },
        }
    )
    assert "unknown artifact_id" in foreign_read["evidence_pack"]["error"]
    assert "unknown or stale raw_ref" in foreign_raw["raw"]["error"]


def test_episode_new_rotates_across_turns_but_not_within_one_turn(tmp_path: Path) -> None:
    controller = EpisodeController()
    force = BenefitDecision("force_sro", "long", 0.9, "collect", code="long_document")
    scope = tmp_path / "report.md"

    _, first = controller.bind(
        conversation_id="c1",
        turn_id="t1",
        scope=scope,
        proposed=force,
        hint=GateContext(goal="selective_read", relation="new"),
    )
    _, duplicate_tool_call = controller.bind(
        conversation_id="c1",
        turn_id="t1",
        scope=scope,
        proposed=force,
        hint=GateContext(goal="selective_read", relation="new"),
    )
    _, next_turn = controller.bind(
        conversation_id="c1",
        turn_id="t2",
        scope=scope,
        proposed=force,
        hint=GateContext(goal="selective_read", relation="new"),
    )

    assert duplicate_tool_call.episode_id == first.episode_id
    assert next_turn.episode_id != first.episode_id


def test_episode_new_without_turn_id_reuses_same_scope_lease(tmp_path: Path) -> None:
    controller = EpisodeController()
    force = BenefitDecision("force_sro", "long", 0.9, "collect", code="long_document")
    scope = tmp_path / "report.md"

    _, first = controller.bind(
        conversation_id="legacy",
        turn_id="",
        scope=scope,
        proposed=force,
        hint=GateContext(goal="selective_read", relation="new"),
    )
    _, repeated = controller.bind(
        conversation_id="legacy",
        turn_id="",
        scope=scope,
        proposed=force,
        hint=GateContext(goal="selective_read", relation="new"),
    )

    assert repeated.episode_id == first.episode_id


def test_episode_continue_on_unrelated_sibling_starts_new_episode(tmp_path: Path) -> None:
    controller = EpisodeController()
    force = BenefitDecision("force_sro", "evidence", 0.9, "collect", code="multi_file_evidence")
    first_scope = tmp_path / "audit_a"
    second_scope = tmp_path / "audit_b"

    _, first = controller.bind(
        conversation_id="c1",
        turn_id="t1",
        scope=first_scope,
        proposed=force,
        hint=GateContext(goal="cross_file_evidence", relation="new"),
    )
    _, second = controller.bind(
        conversation_id="c1",
        turn_id="t2",
        scope=second_scope,
        proposed=force,
        hint=GateContext(goal="cross_file_evidence", relation="continue"),
    )

    assert second.episode_id != first.episode_id


def test_resolved_episode_goal_change_discards_stale_closure(tmp_path: Path) -> None:
    controller = EpisodeController()
    force = BenefitDecision("force_sro", "long", 0.9, "collect", code="long_document")
    native = BenefitDecision("native", "full", 0.9, "native_read", code="native_full_fidelity")
    scope = tmp_path / "report.md"
    _, first = controller.bind(
        conversation_id="c1",
        turn_id="t1",
        scope=scope,
        proposed=force,
        hint=GateContext(goal="selective_read", relation="new"),
    )
    controller.mark_ready(conversation_id="c1", scope=scope, closure_ref="sro_old")
    controller.mark_final("c1")

    decision, resumed = controller.bind(
        conversation_id="c1",
        turn_id="t2",
        scope=scope,
        proposed=native,
        hint=GateContext(goal="full_fidelity", relation="continue"),
    )

    assert resumed.episode_id == first.episode_id
    assert decision.mode == "native"
    assert resumed.status == "open"
    assert resumed.closure_ref == ""


def test_task_mix_transitions_and_reopens_new_document_slots(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text(
        ("Skills total: 5,705. Gateway API: typed WebSocket. Release year: 2026.\n" * 300),
        encoding="utf-8",
    )
    table = tmp_path / "small.csv"
    table.write_text("id,value\n1,2\n", encoding="utf-8")
    audit = _mixed_bundle(tmp_path / "audit")
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    c1_t1 = {"conversation_id": "c1", "turn_id": "t1"}

    preview = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(report),
            "context": c1_t1,
            "episode_hint": {"goal": "selective_read", "relation": "new", "coverage": "selective"},
        },
    })
    first_episode = preview["episode"]["episode_id"]
    artifact_id = preview["preview_pack"]["artifact_id"]
    first_read = bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": artifact_id},
            "mode": "collect",
            "hint": {"goal": "find total", "type_hint": "text", "slots": {"total": "How many skills?"}},
            "context": c1_t1,
            "episode_hint": {"goal": "selective_read", "relation": "continue", "coverage": "selective"},
        },
    })
    assert first_read["evidence_pack"]["episode_status"] == "evidence_ready"
    bridge.handle({"method": "lifecycle_event", "params": {"type": "assistant_final", "context": c1_t1}})

    c1_t2 = {"conversation_id": "c1", "turn_id": "t2"}
    resumed = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(report),
            "context": c1_t2,
            "episode_hint": {"goal": "selective_read", "relation": "continue", "coverage": "selective"},
        },
    })
    assert resumed["episode"]["episode_id"] == first_episode
    followup = bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": artifact_id},
            "mode": "collect",
            "hint": {"goal": "find API", "type_hint": "text", "slots": {"api": "Which API is used?"}},
            "context": c1_t2,
            "episode_hint": {"goal": "selective_read", "relation": "continue", "coverage": "selective"},
        },
    })
    assert not followup["evidence_pack"]["summary"].startswith("adapter ready guard")
    assert followup["evidence_pack"]["slot_digest"]["resolved_slot_ids"] == ["api"]
    assert followup["episode"]["episode_id"] == first_episode

    table_route = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(table),
            "context": {"conversation_id": "c1", "turn_id": "t3"},
            "episode_hint": {"goal": "structured_compute", "relation": "switch"},
        },
    })
    assert table_route["decision"]["mode"] == "native"
    assert table_route["episode"]["episode_id"] != first_episode

    audit_route = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(audit),
            "context": {"conversation_id": "c1", "turn_id": "t4"},
            "episode_hint": {
                "goal": "cross_file_evidence",
                "relation": "switch",
                "coverage": "selective",
            },
        },
    })
    assert (audit_route["decision"]["mode"], audit_route["decision"]["code"]) == (
        "force_sro",
        "multi_file_evidence",
    )
    assert audit_route["episode"]["episode_id"] != table_route["episode"]["episode_id"]


def test_new_episode_same_resource_does_not_inherit_ready_guard(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Skills total: 5,705.\n" * 500, encoding="utf-8")
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    first_context = {"conversation_id": "c1", "turn_id": "t1"}
    first_preview = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(report),
            "context": first_context,
            "episode_hint": {"goal": "selective_read", "relation": "new"},
        },
    })
    artifact_id = first_preview["preview_pack"]["artifact_id"]
    hint = {
        "goal": "find total",
        "type_hint": "text",
        "slots": {"total": "How many skills?"},
    }
    bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": artifact_id},
            "mode": "collect",
            "hint": hint,
            "context": first_context,
            "episode_hint": {"goal": "selective_read", "relation": "continue"},
        },
    })

    second_context = {"conversation_id": "c1", "turn_id": "t2"}
    second_preview = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(report),
            "context": second_context,
            "episode_hint": {"goal": "selective_read", "relation": "new"},
        },
    })
    second_read = bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": artifact_id},
            "mode": "collect",
            "hint": hint,
            "context": second_context,
            "episode_hint": {"goal": "selective_read", "relation": "continue"},
        },
    })

    assert second_preview["episode"]["episode_id"] != first_preview["episode"]["episode_id"]
    assert not second_read["evidence_pack"]["summary"].startswith("adapter ready guard")
    assert not second_read["evidence_pack"]["summary"].startswith("slot coverage already available")


def test_nanobot_preview_tool_passes_context_and_model_hint(tmp_path: Path) -> None:
    audit = _mixed_bundle(tmp_path / "audit")
    orchestrator = SparseReadingOrchestrator(tmp_path)
    tool = SroPreviewTool(orchestrator)
    tool.set_context(SimpleNamespace(session_key="chat:one", message_id="m1"))

    result = json.loads(asyncio.run(tool.execute(
        path=str(audit),
        episode_hint={
            "goal": "cross_file_evidence",
            "relation": "new",
            "coverage": "selective",
            "summary": "audit the current evidence bundle",
        },
    )))

    assert (result["decision"]["mode"], result["decision"]["code"]) == (
        "force_sro",
        "multi_file_evidence",
    )
    assert result["episode"]["conversation_id"] == "chat:one"
    assert result["preview_pack"]["card"]["recommended_mode"] == "collect"


def test_nanobot_artifact_and_ready_state_are_isolated_by_conversation(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Skills total: 5,705.\n" * 500, encoding="utf-8")
    orchestrator = SparseReadingOrchestrator(tmp_path)
    preview_tool = SroPreviewTool(orchestrator)
    read_tool = SroReadTool(orchestrator)
    hint = {"goal": "selective_read", "relation": "new", "coverage": "selective"}
    slots = {
        "goal": "find total",
        "type_hint": "text",
        "slots": {"total": "How many skills?"},
    }

    preview_tool.set_context(SimpleNamespace(session_key="chat:one", message_id="m1"))
    first_preview = json.loads(asyncio.run(preview_tool.execute(path=str(report), episode_hint=hint)))
    first_id = first_preview["preview_pack"]["artifact_id"]
    first_read = json.loads(asyncio.run(read_tool.execute(
        target={"artifact_id": first_id},
        mode="collect",
        hint=slots,
        episode_hint={**hint, "relation": "continue"},
    )))
    assert first_read["evidence_pack"]["slot_digest"]["overall_status"] == "ready"

    preview_tool.set_context(SimpleNamespace(session_key="chat:two", message_id="m1"))
    second_preview = json.loads(asyncio.run(preview_tool.execute(path=str(report), episode_hint=hint)))
    second_id = second_preview["preview_pack"]["artifact_id"]
    second_read = json.loads(asyncio.run(read_tool.execute(
        target={"artifact_id": second_id},
        mode="collect",
        hint=slots,
        episode_hint={**hint, "relation": "continue"},
    )))

    assert second_id != first_id
    assert not second_read["evidence_pack"]["summary"].startswith("slot coverage already available")


def test_required_output_write_is_isolated_by_conversation(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("evidence\n" * 1000, encoding="utf-8")
    orchestrator = SparseReadingOrchestrator(tmp_path)
    orchestrator.set_context({"conversation_id": "one", "turn_id": "t1"})
    first_id = orchestrator.preview({"path": str(report)}).artifact_id
    orchestrator.set_context({"conversation_id": "two", "turn_id": "t1"})
    second_id = orchestrator.preview({"path": str(report)}).artifact_id
    orchestrator._required_outputs_by_artifact[first_id] = {"answer.md"}
    orchestrator._required_outputs_by_artifact[second_id] = {"answer.md"}

    orchestrator.set_context({"conversation_id": "one", "turn_id": "t2"})
    output = tmp_path / "answer.md"
    output.write_text("done\n", encoding="utf-8")
    orchestrator.record_output_write(output)

    assert orchestrator._written_outputs_by_artifact[first_id] == {"answer.md"}
    assert orchestrator._written_outputs_by_artifact.get(second_id, set()) == set()


def test_session_reset_discards_core_and_adapter_closure(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Skills total: 5,705.\n" * 500, encoding="utf-8")
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    context = {"conversation_id": "c1", "turn_id": "t1"}
    preview = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(report),
            "context": context,
            "episode_hint": {"goal": "selective_read", "relation": "new"},
        },
    })
    artifact_id = preview["preview_pack"]["artifact_id"]
    bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": artifact_id},
            "mode": "collect",
            "hint": {
                "goal": "find total",
                "type_hint": "text",
                "slots": {"total": "How many skills?"},
            },
            "context": context,
            "episode_hint": {"goal": "selective_read", "relation": "continue"},
        },
    })

    bridge.handle({
        "method": "lifecycle_event",
        "params": {"type": "session_reset", "context": context},
    })
    stale = bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": artifact_id},
            "mode": "collect",
            "hint": {"goal": "find total", "type_hint": "text"},
            "context": {"conversation_id": "c1", "turn_id": "t2"},
        },
    })
    assert "unknown artifact_id" in stale["evidence_pack"]["error"]
    assert bridge.runtime.orchestrator.current_episode() is None

    refreshed = bridge.handle({
        "method": "preview",
        "params": {
            "path": str(report),
            "context": {"conversation_id": "c1", "turn_id": "t2"},
            "episode_hint": {"goal": "selective_read", "relation": "new"},
        },
    })
    refreshed_read = bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": refreshed["preview_pack"]["artifact_id"]},
            "mode": "collect",
            "hint": {
                "goal": "find total",
                "type_hint": "text",
                "slots": {"total": "How many skills?"},
            },
            "context": {"conversation_id": "c1", "turn_id": "t2"},
            "episode_hint": {"goal": "selective_read", "relation": "continue"},
        },
    })
    assert not refreshed_read["evidence_pack"]["summary"].startswith("adapter ready guard")


def test_native_to_force_episode_switch_updates_actual_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    audit = _mixed_bundle(tmp_path / "audit")
    orchestrator = SparseReadingOrchestrator(tmp_path, macro_available=True)
    orchestrator.set_context({"conversation_id": "c1", "turn_id": "t1"})
    native, _ = orchestrator.bind_episode(
        audit,
        {"goal": "edit_or_execute", "relation": "new"},
    )
    assert native.mode == "native"
    assert orchestrator.should_handoff_list(audit) is False

    orchestrator.set_context({"conversation_id": "c1", "turn_id": "t2"})
    force, _ = orchestrator.bind_episode(
        audit,
        {"goal": "cross_file_evidence", "relation": "switch", "coverage": "selective"},
    )
    assert (force.mode, force.code) == ("force_sro", "multi_file_evidence")
    assert orchestrator.should_handoff_list(audit) is True
    assert orchestrator.should_handoff_read(audit / "fetcher.py") is True


def test_preflight_scan_does_not_replace_active_episode(tmp_path: Path) -> None:
    first = tmp_path / "a.md"
    second = tmp_path / "z.md"
    first.write_text("first evidence\n" * 1000, encoding="utf-8")
    second.write_text("second evidence\n" * 1000, encoding="utf-8")
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    context = {"conversation_id": "c1", "turn_id": "t1"}
    active = bridge.handle({
        "method": "decide",
        "params": {
            "path": str(first),
            "context": context,
            "episode_hint": {"goal": "selective_read", "relation": "new"},
        },
    })["episode"]

    bridge.handle({
        "method": "preflight",
        "params": {
            "workspace": str(tmp_path),
            "context": context,
            "episode_hint": {"goal": "selective_read", "relation": "continue"},
        },
    })

    current = bridge.episodes.current("c1")
    assert current is not None
    assert current.episode_id == active["episode_id"]
    assert current.scope == first.resolve()


def test_card_new_episode_is_evaluated_before_ready_guard(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    report.write_text("Skills total: 5,705.\n" * 500, encoding="utf-8")
    bridge = OpenCodeBridge(workspace=tmp_path, mode="auto")
    first_context = {"conversation_id": "c1", "turn_id": "t1"}
    card = bridge.handle({
        "method": "card",
        "params": {
            "path": str(report),
            "context": first_context,
            "episode_hint": {"goal": "selective_read", "relation": "new"},
        },
    })
    artifact_id = card["file_card"]["artifact_id"]
    bridge.handle({
        "method": "read",
        "params": {
            "target": {"artifact_id": artifact_id},
            "mode": "collect",
            "hint": {
                "goal": "find total",
                "type_hint": "text",
                "slots": {"total": "How many skills?"},
            },
            "context": first_context,
            "episode_hint": {"goal": "selective_read", "relation": "continue"},
        },
    })

    fresh = bridge.handle({
        "method": "card",
        "params": {
            "path": str(report),
            "context": {"conversation_id": "c1", "turn_id": "t2"},
            "episode_hint": {"goal": "selective_read", "relation": "new"},
        },
    })

    assert fresh["episode"]["episode_id"] != card["episode"]["episode_id"]
    assert fresh["file_card"].get("closure_once_already_ready") is not True
    assert fresh["opencode_gate"].get("already_ready") is not True
