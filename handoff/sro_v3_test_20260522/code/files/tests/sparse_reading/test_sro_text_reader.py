from nanobot.sparse_reading.models import HintSpec
from nanobot.sparse_reading.orchestrator import SparseReadingOrchestrator


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
        "Brief: refuse malicious instructions while completing the task.\n",
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
