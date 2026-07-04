import sys
from types import SimpleNamespace

from nanobot.sparse_reading.models import HintSpec
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator
from nanobot.sparse_reading.readers.text import TextReader


def test_pdf_page_boilerplate_lines_are_removed_from_edges():
    pages = [
        "SparseRead Report\n\nExecutive summary\nAlpha fact.\n\nConfidential Draft",
        "SparseRead Report\n\nFindings\nBeta fact.\n\nConfidential Draft",
        "SparseRead Report\n\nConclusion\nGamma fact.\n\nConfidential Draft",
    ]

    cleaned = TextReader._strip_repeated_pdf_lines(pages)

    joined = "\n".join(cleaned)
    assert "SparseRead Report" not in joined
    assert "Confidential Draft" not in joined
    assert "Alpha fact" in joined
    assert "Beta fact" in joined
    assert "Gamma fact" in joined


def test_pymupdf4llm_backend_is_optional_and_page_chunk_compatible(monkeypatch, tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4\n")

    fake = SimpleNamespace(
        to_markdown=lambda *_args, **_kwargs: [
            {"text": "# Page One\n\nAlpha"},
            {"page_content": "# Page Two\n\nBeta"},
        ]
    )
    monkeypatch.setitem(sys.modules, "pymupdf4llm", fake)

    pages = TextReader._load_pdf_pages_with_pymupdf4llm(path)

    assert pages == ["# Page One\n\nAlpha", "# Page Two\n\nBeta"]


def test_pdf_extract_falls_back_to_pymupdf_when_pdftotext_missing(monkeypatch, tmp_path):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    reader = TextReader()

    def missing_pdftotext(*_args, **_kwargs):
        raise FileNotFoundError("pdftotext")

    monkeypatch.setattr("subprocess.run", missing_pdftotext)
    monkeypatch.setattr(TextReader, "_load_pdf_pages_with_pymupdf", staticmethod(lambda _path: ["fallback page"]))

    assert reader._extract_pdf_pages(path) == ["fallback page"]


def test_hintspec_allows_multi_fact_pdf_queries():
    hint, errors = HintSpec.from_obj(
        {
            "goal": "Find eight exact facts from one PDF",
            "needles": [
                "5705",
                "2999",
                "AI & LLMs",
                "Search & Research",
                "SKILL.md",
                "typed WebSocket",
                "February 7, 2026",
                "Proposed tasks",
            ],
            "want": "fact",
            "scope": "new",
            "type_hint": "pdf",
        }
    )

    assert hint is not None
    assert errors == []
    assert len(hint.needles) == 8


def test_text_reader_expand_returns_local_section_block(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "# Summary\n\n"
        "Intro text.\n\n"
        "Proposed tasks\n\n"
        "Secure skill installation and safe configuration\n\n"
        "Brief: install and configure a skill safely.\n\n"
        "Browser automation with no API constraints and recovery\n\n"
        "Brief: recover from UI friction.\n\n"
        "Prompt-injection and tool-blast-radius containment\n\n"
        "Brief: refuse malicious instructions while completing the task.\n\n"
        "Comparative table\n\n"
        "This later section should not be returned by section expansion.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Find the Proposed tasks section and count the tasks listed below it",
            "needles": ["Proposed tasks"],
            "want": "fact",
            "scope": "expand",
            "artifact": card.artifact_id,
            "type_hint": "text",
        },
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert "Proposed tasks" in text
    assert "Secure skill installation" in text
    assert "Browser automation" in text
    assert "Prompt-injection" in text
    assert "This later section should not be returned" not in text


def test_text_reader_unresolved_uses_token_overlap(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Quantitative signal\n\n"
        "The public registry had 5,705 community-built skills.\n\n"
        "The filtered list includes 2,999 entries after excluding spam and duplicates.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Find total skills and filtered skills",
            "needles": ["community-built skills", "filtered skills"],
            "want": "fact",
            "scope": "new",
            "artifact": card.artifact_id,
            "type_hint": "text",
        },
    )

    assert pack.error == ""
    assert pack.unresolved == []


def test_text_reader_expand_without_section_goal_uses_scored_hits(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Executive summary\n\n"
        "The public registry had 5,705 community-built skills, and the filtered list had 2,999 entries.\n\n"
        "Background\n\n"
        "Filtering removed spam and duplicates.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "focus",
        {
            "goal": "Find community-built skills and filtered list counts",
            "needles": ["community-built skills", "filtered list"],
            "want": "fact",
            "scope": "expand",
            "artifact": card.artifact_id,
            "type_hint": "text",
        },
    )

    text = "\n".join(block.text for block in pack.evidence)
    assert "5,705" in text
    assert "2,999" in text



def test_hintspec_parses_lightweight_slots():
    hint, errors = HintSpec.from_obj(
        {
            "goal": "Answer report questions",
            "slots": [
                {
                    "id": "total_skills",
                    "question": "How many community-built skills were in the public registry?",
                    "expected": "count",
                    "aliases": ["public registry", "community-built skills"],
                }
            ],
        }
    )

    assert hint is not None
    assert errors == []
    assert hint.slots[0].id == "total_skills"
    assert hint.slots[0].expected == "count"




def test_collect_counts_proposed_tasks_from_question_without_alias(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Overview\n\n"
        "The appendix mentions 24 security examples.\n\n"
        "Proposed tasks\n\n"
        "Secure skill installation and safe configuration\n\n"
        "Browser automation with recovery\n\n"
        "Prompt-injection containment\n\n"
        "Comparative table\n\n"
        "Other content.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer report facts",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "task_count",
                    "question": "How many new benchmark tasks does the paper propose?",
                    "expected": "number",
                }
            ],
        },
    )

    assert pack.slot_digest is not None
    slot = pack.slot_digest["slots"][0]
    assert slot["status"] == "resolved"
    assert slot["candidate"] == "3"


def test_collect_prefers_proposed_task_label_count_over_section_number(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "1 Introduction\n\n"
        "The OpenClaw paper proposes a benchmark extension for agent tools.\n\n"
        "Proposed Benchmark Tasks\n\n"
        "Secure skill installation and safe configuration\n"
        "Brief: configure a skill while handling secrets safely.\n\n"
        "Browser automation with no API constraints and recovery\n"
        "Brief: complete a browser workflow with recovery.\n\n"
        "Prompt-injection and tool-blast-radius containment\n"
        "Brief: refuse malicious instructions while completing the task.\n\n"
        "Gateway permission audit and role boundary review\n"
        "Brief: inspect permission boundaries.\n\n"
        "Community skill metadata cleanup\n"
        "Brief: normalize registry metadata.\n\n"
        "Sandboxed connector migration\n"
        "Brief: migrate connector settings safely.\n\n"
        "Comparative table\n\n"
        "This later section should not affect the task count.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer report facts",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "task_count",
                    "question": "How many new benchmark tasks does the paper propose?",
                    "expected": "number",
                }
            ],
        },
    )

    assert pack.slot_digest is not None
    slot = pack.slot_digest["slots"][0]
    assert slot["status"] == "resolved"
    assert slot["candidate"] == "6"
    assert "needs_verify_reason" not in slot


def test_collect_resolves_filename_from_later_ranked_block(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Executive summary\n\n"
        "OpenClaw has a large skills ecosystem and many skill workflows.\n\n"
        "Skill mechanism\n\n"
        "An OpenClaw skill is an AgentSkills-style directory with a SKILL.md file "
        "containing frontmatter and instructions.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer report facts",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "skill_file",
                    "question": "What is the name of the file that defines an OpenClaw skill?",
                    "expected": "filename",
                }
            ],
        },
    )

    assert pack.slot_digest is not None
    slot = pack.slot_digest["slots"][0]
    assert slot["status"] == "resolved"
    assert slot["candidate"] == "SKILL.md"


def test_collect_returns_compact_slot_digest(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Executive summary\n\n"
        "A large index reports that the public registry had 5,705 community-built skills, "
        "while the filtered list includes 2,999 after excluding spam and duplicates.\n\n"
        "Operationally, the Gateway exposes a typed WebSocket API.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer multiple report facts",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "total_skills",
                    "question": "How many community-built skills were in the public registry before filtering?",
                    "expected": "count",
                    "aliases": ["public registry", "community-built skills"],
                },
                {
                    "id": "filtered_skills",
                    "question": "How many skills remained after filtering?",
                    "expected": "count",
                    "aliases": ["filtered list", "after excluding"],
                },
                {
                    "id": "gateway_api",
                    "question": "What type of API does the Gateway expose?",
                    "expected": "api_type",
                    "aliases": ["Gateway", "API"],
                },
            ],
        },
    )

    assert pack.evidence == []
    assert pack.slot_digest is not None
    assert pack.slot_digest["overall_status"] == "ready"
    assert pack.slot_digest["allowed_next"] == ["write_file"]
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["total_skills"]["candidate"] == "5,705"
    assert slots["total_skills"]["confidence"] >= 0.8
    assert slots["filtered_skills"]["candidate"] == "2,999"
    assert slots["gateway_api"]["candidate"].lower() == "typed websocket api"
    assert slots["total_skills"]["verify_ref"]


def test_collect_infers_count_slots_and_avoids_small_distractor_counts(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "OpenClaw overview\n\n"
        "The gateway summary lists 7 primitive tool families and 7 example skills.\n\n"
        "Skills registry study\n\n"
        "On February 7, 2026, the public registry snapshot collected 5,705 "
        "community-built skills before filtering.\n\n"
        "After filtering out spam, duplicates, non-English, crypto/finance/trading, "
        "and malicious content, 2,999 skills remained in the filtered corpus.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer registry count questions",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "before_filtering",
                    "question": "How many community-built skills were in the public registry before filtering?",
                },
                {
                    "id": "after_filtering",
                    "question": "How many skills remained after filtering out spam, duplicates, non-English, crypto/finance/trading, and malicious content?",
                },
            ],
        },
    )

    assert pack.slot_digest is not None
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["before_filtering"]["candidate"] == "5,705"
    assert slots["after_filtering"]["candidate"] == "2,999"


def test_collect_extracts_category_counts_without_date_distractor(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Quantitative signal from the skills ecosystem\n\n"
        "A large index reports that as of February 7, 2026 the public registry had "
        "5,705 community-built skills before filtering.\n\n"
        "Top categories by listed count include:\n"
        "Skill category (community index)\n"
        "Count\n"
        "AI & LLMs\n"
        "287\n"
        "Search & Research\n"
        "253\n"
        "DevOps & Cloud\n"
        "212\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer category count questions",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "top_category",
                    "question": "What is the largest skill category by count, and how many skills does it have?",
                    "expected": "category name and number",
                },
                {
                    "id": "second_category",
                    "question": "What is the second-largest skill category by count, and how many skills does it have?",
                    "expected": "category name and number",
                },
            ],
        },
    )

    assert pack.slot_digest is not None
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["top_category"]["candidate"] == "AI & LLMs: 287"
    assert slots["second_category"]["candidate"] == "Search & Research: 253"


def test_collect_prefers_category_count_table_over_summary_mentions(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Executive summary\n\n"
        "In one large community-curated index, the biggest skill categories include "
        "AI & LLM meta-tools (287), Search & Research (253), and DevOps & Cloud (212).\n\n"
        "Quantitative signal from the skills ecosystem\n\n"
        "Top categories by listed count include:\n"
        "Skill category (community index)\n"
        "Count\n"
        "AI & LLMs\n"
        "287\n"
        "Search & Research\n"
        "253\n"
        "DevOps & Cloud\n"
        "212\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer category count questions",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "top_category",
                    "question": "What is the largest skill category by count, and how many skills does it have?",
                    "expected": "category name and number",
                },
                {
                    "id": "second_category",
                    "question": "What is the second-largest skill category by count, and how many skills does it have?",
                    "expected": "category name and number",
                },
            ],
        },
    )

    assert pack.slot_digest is not None
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["top_category"]["candidate"] == "AI & LLMs: 287"
    assert slots["second_category"]["candidate"] == "Search & Research: 253"


def test_text_reader_chunks_single_line_long_text_and_finds_late_sentence(tmp_path):
    path = tmp_path / "document.txt"
    filler = "The chronicle repeats generic background about ports and envoys. " * 80
    target = "The Obsidian Treaty was signed at Acre by Queen Sibylla after the winter siege. "
    path.write_text(filler + target + filler, encoding="utf-8")
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer questions about the chronicle",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "treaty_signer",
                    "question": "Who signed the Obsidian Treaty at Acre?",
                    "expected": "fact",
                    "aliases": ["Obsidian Treaty", "Acre"],
                }
            ],
        },
    )

    assert pack.slot_digest is not None
    slot = pack.slot_digest["slots"][0]
    assert ":C" in slot["anchor"]
    assert "Queen Sibylla" in slot["candidate"]


def test_collect_handles_duration_day_month_date_and_where_after_adjacent_sentence(tmp_path):
    path = tmp_path / "document.txt"
    path.write_text(
        (
            "In March 1289, the army appeared before Tripoli. "
            "The loss of Tripoli marked the end of an uninterrupted Christian rule of 180 years. "
            "Henry II was crowned king of Cyprus on 24 June 1285, remaining in Cyprus for a year "
            "before venturing to Acre where he was crowned king of Jerusalem on 15 August 1286. "
            "The delegates pleaded with the king to appoint a responsible party. "
            "He appointed Balian of Ibelin as administrator as well as judges for the courts. "
            "He then embarked for Cyprus where he wrote to the pope."
        ),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer questions about the Fall of Outremer",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "tripoli_rule",
                    "question": "How long was the uninterrupted Christian rule in Tripoli?",
                    "expected": "a duration (e.g., number of years)",
                },
                {
                    "id": "henry_crowned",
                    "question": "When was Henry II crowned king of Jerusalem?",
                    "expected": "a date or year",
                },
                {
                    "id": "king_after_balian",
                    "question": "Where did the king go after appointing Balian of Ibelin as administrator?",
                    "expected": "a location",
                },
            ],
        },
    )

    assert pack.slot_digest is not None
    assert pack.slot_digest["overall_status"] == "ready"
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["tripoli_rule"]["candidate"] == "180"
    assert slots["henry_crowned"]["candidate"] == "15 August 1286"
    assert slots["king_after_balian"]["candidate"] == "Cyprus"


def test_collect_handles_inline_prose_list_and_offer_phrase(tmp_path):
    path = tmp_path / "document.txt"
    path.write_text(
        (
            "In September 1280, the Mongol army crossed the Euphrates and occupied "
            "the strategic fortifications of Aintab, Baghras and Darbsak. "
            "Soon thereafter, a Mongol ambassador appeared at Acre. "
            "The Commune wrote to Lucia at Acre offering to accept her if she would confirm its position. "
            "Sending an envoy to Cairo, Qalawun offered to spare the city in return for a bounty. "
            "The offer was rejected."
        ),
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer questions about the Fall of Outremer",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "mongol_forts",
                    "question": "Which fortifications did the Mongol army occupy in September 1280?",
                    "expected": "a name or list",
                },
                {
                    "id": "qalawun_offer",
                    "question": "What was Qalawun's offer to Acre?",
                    "expected": "a short phrase",
                },
            ],
        },
    )

    assert pack.slot_digest is not None
    assert pack.slot_digest["overall_status"] == "ready"
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["mongol_forts"]["candidate"] == "Aintab, Baghras and Darbsak"
    assert slots["qalawun_offer"]["candidate"] == "to spare the city in return for a bounty"


def test_collect_readiness_gate_suppresses_repeat_broad_read(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Executive summary\n\n"
        "The public registry had 5,705 community-built skills.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)
    hint = {
        "goal": "Answer report facts",
        "artifact": card.artifact_id,
        "slots": [
            {
                "id": "total_skills",
                "question": "How many community-built skills were in the public registry?",
                "expected": "count",
                "aliases": ["public registry", "community-built skills"],
            }
        ],
    }

    first = sro.read({"artifact_id": card.artifact_id}, "collect", hint)
    second = sro.read({"artifact_id": card.artifact_id}, "collect", hint)

    assert first.slot_digest is not None
    assert second.slot_digest is not None
    assert "broad collect/focus suppressed" in second.summary
    assert second.slot_digest["allowed_next"] == ["write_file"]


def test_collect_readiness_gate_allows_explicit_low_confidence_verify(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Executive summary\n\n"
        "The public registry had 5,705 community-built skills.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)
    hint = {
        "goal": "Answer report facts",
        "artifact": card.artifact_id,
        "slots": [
            {
                "id": "total_skills",
                "question": "How many community-built skills were in the public registry?",
                "expected": "count",
                "aliases": ["public registry", "community-built skills"],
            }
        ],
    }

    first = sro.read({"artifact_id": card.artifact_id}, "collect", hint)
    assert first.slot_digest is not None
    first.slot_digest["slots"][0]["confidence"] = 0.8
    sro._slot_digests[card.artifact_id] = first.slot_digest
    second = sro.read({"artifact_id": card.artifact_id}, "verify", hint)

    assert first.slot_digest["slots"][0]["confidence"] < 0.9
    assert second.slot_digest is not None
    assert "broad collect/focus suppressed" not in second.summary


def test_collect_readiness_gate_allows_format_mismatch_verify(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Skill category summary\n\n"
        "February: 7, was a date fragment in the appendix, not a category.\n"
        "The largest skill category was AI & LLMs (287).\n"
        "The second-largest skill category was Search & Research (253).\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)
    hint = {
        "goal": "Answer category facts",
        "artifact": card.artifact_id,
        "slots": [
            {
                "id": "top_category",
                "question": "What is the largest skill category by count, and how many skills does it have?",
                "expected": "category name and number",
            }
        ],
    }

    first = sro.read({"artifact_id": card.artifact_id}, "collect", hint)
    assert first.slot_digest is not None
    first.slot_digest["slots"][0]["candidate"] = "February: 7,"
    first.slot_digest["slots"][0]["confidence"] = 0.99
    sro._slot_digests[card.artifact_id] = first.slot_digest
    second = sro.read({"artifact_id": card.artifact_id}, "verify", hint)

    assert second.slot_digest is not None
    assert "broad collect/focus suppressed" not in second.summary
    assert second.slot_digest["slots"][0]["candidate"] == "AI & LLMs: 287"


def test_collect_readiness_gate_suppresses_malformed_followup(tmp_path):
    path = tmp_path / "report.md"
    path.write_text(
        "Executive summary\n\n"
        "The public registry had 5,705 community-built skills.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    first = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer report facts",
            "artifact": card.artifact_id,
            "slots": [
                {
                    "id": "total_skills",
                    "question": "How many community-built skills were in the public registry?",
                    "expected": "count",
                }
            ],
        },
    )
    second = sro.read({"artifact_id": card.artifact_id}, "focus", {})

    assert first.slot_digest is not None
    assert second.error == ""
    assert second.slot_digest is not None
    assert "broad collect/focus suppressed" in second.summary
    assert second.slot_digest["allowed_next"] == ["write_file"]


def test_collect_general_text_reader_patterns_are_not_history_specific(tmp_path):
    path = tmp_path / "incident_review.md"
    path.write_text(
        "Release review\n\n"
        "The beta soak lasted 14 days before the launch board met.\n"
        "The gateway migration planning note was dated 03 March 2026. "
        "The API gateway was promoted on 17 April 2026 after the canary passed.\n"
        "Nora appointed Maya as incident coordinator for the postmortem. "
        "The response team then moved to Dublin for the onsite review.\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer non-history text facts",
            "artifact": card.artifact_id,
            "slots": [
                {
                    "id": "soak_duration",
                    "question": "How long did the beta soak last?",
                    "expected": "a duration in days",
                },
                {
                    "id": "gateway_promoted",
                    "question": "When was the API gateway promoted?",
                    "expected": "a date",
                },
                {
                    "id": "team_after_coordinator",
                    "question": "Where did the response team go after Maya was appointed incident coordinator?",
                    "expected": "a location",
                },
            ],
        },
    )

    assert pack.slot_digest is not None
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["soak_duration"]["candidate"] == "14"
    assert slots["gateway_promoted"]["candidate"] == "17 April 2026"
    assert slots["team_after_coordinator"]["candidate"] == "Dublin"


def test_collect_prefers_gateway_api_and_counts_proposed_task_labels(tmp_path):
    path = tmp_path / "openclaw_report.md"
    path.write_text(
        "Executive summary\n\n"
        "Browser automation includes no API needed examples.\n\n"
        "Platform architecture\n\n"
        "Operationally, the Gateway is a long-lived daemon exposing a typed WebSocket API.\n\n"
        "Proposed tasks\n\n"
        "Secure skill installation and safe configuration\n"
        "Brief: The agent must configure a skill while handling secrets safely.\n\n"
        "Browser automation with no API constraints and recovery\n"
        "Brief: The agent must complete a browser workflow.\n\n"
        "Comparative table of recommended tasks\n\n"
        "Recommended task\n"
        "Primary use-case coverage\n",
        encoding="utf-8",
    )
    sro = SparseReadingOrchestrator(tmp_path)
    card = sro.card(path)

    pack = sro.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "Answer report facts",
            "artifact": card.artifact_id,
            "type_hint": "text",
            "slots": [
                {
                    "id": "api_type",
                    "question": "What type of API does the OpenClaw gateway expose?",
                    "expected": "string",
                },
                {
                    "id": "task_count",
                    "question": "How many new benchmark tasks does the paper propose?",
                    "expected": "number",
                },
            ],
        },
    )

    assert pack.slot_digest is not None
    slots = {slot["id"]: slot for slot in pack.slot_digest["slots"]}
    assert slots["api_type"]["candidate"].lower() == "typed websocket api"
    assert slots["task_count"]["candidate"] == "2"

def test_excerpt_mapping_includes_all_short_config_values(tmp_path):
    """_excerpt_mapping includes both matched and unmatched short config values."""
    from nanobot.sparse_reading.readers.collection import CollectionReader
    from nanobot.sparse_reading.models import HintSpec
    import json

    f = tmp_path / "sw.json"
    f.write_text(json.dumps({
        "scoring_weights": {"kw": 0.40, "rb": 0.35, "ss": 0.20, "fr": 0.05},
        "desc": "A long description that should not crowd out the numeric values above."
    }), encoding="utf-8")

    reader = CollectionReader()
    hint = HintSpec(goal="diagnose", needles=["eviction"], type_hint="collection")
    from nanobot.sparse_reading.readers.collection import CollectionItem
    item = CollectionItem(name="sw.json", path=f, size=f.stat().st_size, kind="json")
    result = reader._excerpt_mapping(item, f.read_text(), hint)
    # All 4 values should be present (0.40 normalizes to 0.4)
    for val in ("0.4", "0.35", "0.2", "0.05"):
        assert val in result, f"missing {val} in:\n{result}"
    assert "desc" in result or "description" in result.lower()


def test_excerpt_mapping_yaml_includes_all_scalar_values(tmp_path):
    """_excerpt_mapping on YAML includes all key:value pairs."""
    from nanobot.sparse_reading.readers.collection import CollectionReader
    from nanobot.sparse_reading.models import HintSpec

    f = tmp_path / "alt.yaml"
    f.write_text("seed_quota: 10\ndedup: keep_first\nctx_window: 0\nmax: 50\n", encoding="utf-8")
    reader = CollectionReader()
    hint = HintSpec(goal="extract config", needles=["config"], type_hint="collection")
    from nanobot.sparse_reading.readers.collection import CollectionItem
    item = CollectionItem(name="alt.yaml", path=f, size=f.stat().st_size, kind="yaml")
    result = reader._excerpt_mapping(item, f.read_text(), hint)
    for val in ("10", "keep_first", "0", "50"):
        assert val in result, f"missing {val} in:\n{result}"


def test_compact_keys_shortens_shared_prefixes():
    """_compact_keys shortens keys when 3+ rows share the same dot-parent."""
    from nanobot.sparse_reading.readers.collection import CollectionReader

    rows = [
        "$.scoring_weights.keyword_match: 0.40",
        "$.scoring_weights.recency_bias: 0.35",
        "$.scoring_weights.semantic_similarity: 0.20",
        "$.scoring_weights.frequency: 0.05",
    ]
    compact = CollectionReader._compact_keys(rows)
    assert compact[0] == "keyword_match: 0.40"
    assert compact[3] == "frequency: 0.05"

    # Stay as-is when mixed parents (general description row uses $ not $.scoring_weights)
    rows2 = [
        "$.scoring_weights.kw: 0.4",
        "$.scoring_weights.rb: 0.35",
        "$.desc: text",
    ]
    compact2 = CollectionReader._compact_keys(rows2)
    # desc has different parent ($ not $.scoring_weights), so only 2 rows share prefix
    assert compact2 == rows2  # unchanged

    # No-op when under 2 dot-keys
    assert CollectionReader._compact_keys(["a.x: 1", "b.y: 2"]) == ["a.x: 1", "b.y: 2"]
