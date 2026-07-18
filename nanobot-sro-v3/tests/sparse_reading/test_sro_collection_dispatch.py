from nanobot.sparse_reading.models import EvidenceBlock, EvidencePack
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator


def _pdf(path):
    path.write_bytes(b"%PDF-1.4\n" + (b"placeholder\n" * 400))


def test_collection_card_indexes_pdf_children_and_keeps_pdf_gate(tmp_path):
    bundle = tmp_path / "reports"
    bundle.mkdir()
    _pdf(bundle / "nike_report.pdf")

    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(bundle)

    assert card.type == "collection"
    assert card.sparse_recommended is True
    assert card.details["file_count"] == 1
    assert card.details["files"][0]["kind"] == "pdf"


def test_single_pdf_collection_dispatches_to_existing_pdf_reader(tmp_path, monkeypatch):
    bundle = tmp_path / "reports"
    bundle.mkdir()
    pdf = bundle / "nike_report.pdf"
    _pdf(pdf)
    sro = SparseReadingOrchestrator(tmp_path)
    calls = []

    def fake_read(path, artifact_id, mode, hint, budget):
        calls.append((path, artifact_id, mode, hint.goal))
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="pdf",
            summary="mock PDF evidence",
            evidence=[EvidenceBlock("p1:L1", "Nike factory evidence", 1.0)],
        )

    monkeypatch.setattr(sro.text_reader, "read", fake_read)
    card = sro.card(bundle)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Find the Nike factory evidence",
            "needles": ["Nike"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert len(calls) == 1
    assert calls[0][0] == pdf
    assert pack.type == "pdf"
    assert pack.artifact_id != card.artifact_id
    assert pack.summary.startswith("collection dispatch selected nike_report.pdf")


def test_single_csv_collection_dispatches_to_structured_reader(tmp_path, monkeypatch):
    bundle = tmp_path / "data"
    bundle.mkdir()
    csv_path = bundle / "events.csv"
    csv_path.write_text("event_id,value\na-1,10\ntarget-991,42\n", encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    calls = []

    def fake_read(path, artifact_id, mode, hint, budget):
        calls.append((path, mode, hint.needles))
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="csv",
            summary="mock CSV evidence",
            evidence=[EvidenceBlock("row 3", "event_id=target-991 | value=42", 1.0)],
        )

    monkeypatch.setattr(sro.structured_reader, "read", fake_read)
    card = sro.card(bundle)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Find target-991 and return its value",
            "needles": ["target-991", "value"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert calls == [(csv_path, "focus", ["target-991", "value"])]
    assert pack.type == "csv"
    assert pack.artifact_id != card.artifact_id
    assert pack.summary.startswith("collection dispatch selected events.csv")


def test_multiple_pdf_collection_returns_registered_child_targets_without_fanout(tmp_path, monkeypatch):
    bundle = tmp_path / "reports"
    bundle.mkdir()
    _pdf(bundle / "nike_report.pdf")
    _pdf(bundle / "adidas_report.pdf")
    sro = SparseReadingOrchestrator(tmp_path)

    def unexpected_read(*args, **kwargs):
        raise AssertionError("multi-child collection must not auto-read every PDF")

    monkeypatch.setattr(sro.text_reader, "read", unexpected_read)
    card = sro.card(bundle)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Compare the reports",
            "needles": ["report"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert pack.type == "collection"
    assert pack.summary.startswith("typed collection dispatch")
    assert [block.anchor for block in pack.evidence] == ["adidas_report.pdf", "nike_report.pdf"]
    targets = pack.next_action["candidate_targets"]
    assert len(targets) == 2
    assert pack.next_action["mode"] == "focus"
    assert {target["type"] for target in targets} == {"pdf"}
    assert all(target["artifact_id"].startswith("sro_") for target in targets)


def test_unique_typed_filename_match_dispatches_without_collection_manifest(tmp_path, monkeypatch):
    bundle = tmp_path / "reports"
    bundle.mkdir()
    kaima = bundle / "900953_kaima_2021_report.pdf"
    _pdf(kaima)
    _pdf(bundle / "900939_huili_2021_report.pdf")
    sro = SparseReadingOrchestrator(tmp_path)
    calls = []

    def fake_read(path, artifact_id, mode, hint, budget):
        calls.append((path, mode))
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="pdf",
            summary="mock PDF evidence",
            evidence=[EvidenceBlock("p1:L1", "R&D investment: 87,122,954.71", 1.0)],
        )

    monkeypatch.setattr(sro.text_reader, "read", fake_read)
    card = sro.card(bundle)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Find Kaima 900953 R&D investment for 2021",
            "needles": ["Kaima", "900953", "R&D"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert calls == [(kaima, "focus")]
    assert pack.type == "pdf"
    assert pack.summary.startswith("collection dispatch selected 900953_kaima_2021_report.pdf")


def test_unique_pdf_single_slot_dispatches_as_focus_without_slot_extraction(tmp_path, monkeypatch):
    bundle = tmp_path / "reports"
    bundle.mkdir()
    kaima = bundle / "900953_kaima_2021_report.pdf"
    _pdf(kaima)
    _pdf(bundle / "900939_huili_2021_report.pdf")
    sro = SparseReadingOrchestrator(tmp_path)
    calls = []

    def fake_read(path, artifact_id, mode, hint, budget):
        calls.append((path, mode, hint.slots, hint.type_hint, hint.needles))
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="pdf",
            summary="mock focused PDF evidence",
            evidence=[EvidenceBlock("p11:L91-L123", "R&D investment: 87,122,954.71", 1.0)],
        )

    monkeypatch.setattr(sro.text_reader, "read", fake_read)
    card = sro.card(bundle)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Find Kaima 900953 R&D investment for 2021",
            "needles": ["Kaima", "900953", "R&D"],
            "want": "fact",
            "type_hint": "collection",
            "slots": [
                {
                    "id": "rd_investment",
                    "question": "What is Kaima's total R&D investment for 2021?",
                    "expected": "amount in yuan",
                }
            ],
        },
    )

    assert len(calls) == 1
    path, mode, slots, type_hint, needles = calls[0]
    assert path == kaima
    assert mode == "focus"
    assert slots == []
    assert type_hint == "pdf"
    assert "What is Kaima's total R&D investment for 2021?" in needles
    assert pack.slot_digest is None
    assert pack.evidence[0].anchor == "p11:L91-L123"


def test_mixed_collection_keeps_text_evidence_and_exposes_pdf_targets(tmp_path, monkeypatch):
    bundle = tmp_path / "sources"
    bundle.mkdir()
    (bundle / "notes.md").write_text("Target summary note\n", encoding="utf-8")
    _pdf(bundle / "target_report.pdf")
    sro = SparseReadingOrchestrator(tmp_path)

    def unexpected_read(*args, **kwargs):
        raise AssertionError("mixed collections should expose, not eagerly read, typed children")

    monkeypatch.setattr(sro.text_reader, "read", unexpected_read)
    card = sro.card(bundle)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Find the target summary and supporting report",
            "needles": ["target"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert any(block.anchor == "notes.md" for block in pack.evidence)
    targets = pack.next_action["candidate_targets"]
    assert [target["name"] for target in targets] == ["target_report.pdf"]
    assert targets[0]["type"] == "pdf"


def test_typed_manifest_does_not_drop_unmatched_children_after_eight(tmp_path, monkeypatch):
    bundle = tmp_path / "reports"
    bundle.mkdir()
    for index in range(10):
        _pdf(bundle / f"report_{index:02d}.pdf")
    sro = SparseReadingOrchestrator(tmp_path)

    def unexpected_read(*args, **kwargs):
        raise AssertionError("ambiguous collections must return a manifest")

    monkeypatch.setattr(sro.text_reader, "read", unexpected_read)
    card = sro.card(bundle)
    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Find an unrelated named source",
            "needles": ["unrelated"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert len(pack.next_action["candidate_targets"]) == 10


def test_single_pdf_collection_scout_without_slots_dispatches_as_focus(tmp_path, monkeypatch):
    bundle = tmp_path / "reports"
    bundle.mkdir()
    _pdf(bundle / "target_report.pdf")
    sro = SparseReadingOrchestrator(tmp_path)
    calls = []

    def fake_read(path, artifact_id, mode, hint, budget):
        calls.append(mode)
        return EvidencePack(
            artifact_id=artifact_id,
            mode=mode,
            type="pdf",
            summary="mock PDF evidence",
            evidence=[EvidenceBlock("p1:L1", "target evidence", 1.0)],
        )

    monkeypatch.setattr(sro.text_reader, "read", fake_read)
    card = sro.card(bundle)
    sro.read(
        {"artifact_id": card.artifact_id},
        "scout",
        {
            "goal": "Find one fact from the report",
            "needles": ["target"],
            "want": "fact",
            "type_hint": "collection",
        },
    )

    assert calls == ["focus"]
