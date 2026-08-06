import json
from pathlib import Path

import yaml

from sparseread.core.denoise import denoise_text

from sparseread.core.orchestrator import SparseReadingOrchestrator


def test_html_denoise_keeps_main_content_and_drops_carrier_noise() -> None:
    main = "".join(
        f'<p class="result-row repeated-layout-class" data-component="quarterly-result" '
        f'data-analytics-id="result-{index}">Quarter {index}: revenue evidence {index}.</p>'
        for index in range(120)
    )
    html = (
        "<html><head><title>Quarterly evidence</title>"
        "<style>.noise{display:none}</style><script>window.noise='x'.repeat(5000)</script></head>"
        "<body><nav>Home Products Pricing Login</nav>"
        f"<main><h1>Results</h1>{main}</main>"
        "<footer>Terms Privacy Cookies</footer></body></html>"
    )

    result = denoise_text(html, source_kind="html", min_chars=0)

    assert "Quarterly evidence" in result.text
    assert "Quarter 119: revenue evidence 119." in result.text
    assert "window.noise" not in result.text
    assert "display:none" not in result.text
    assert "Terms Privacy Cookies" not in result.text
    assert "html_main" in result.rules
    assert result.output_chars < result.input_chars * 0.6


def test_repetition_denoise_preserves_counts_endpoints_errors_and_fenced_code() -> None:
    repeated = "heartbeat ok\n" * 8
    progress = "\n".join(f"download {value}%" for value in range(0, 100, 10))
    fenced = "```text\n" + ("same code line\n" * 4) + "```"
    text = f"\x1b[32mSTART\x1b[0m\n{repeated}{progress}\nERROR id=42 failed\n{fenced}\n"

    result = denoise_text(text, source_kind="terminal", min_chars=0)

    assert "\x1b" not in result.text
    assert "[repeated identical line ×8]" in result.text
    assert "download 0%" in result.text
    assert "download 90%" in result.text
    assert "collapsed 8 intermediate progress updates" in result.text
    assert "ERROR id=42 failed" in result.text
    assert result.text.count("same code line") == 4


def test_short_text_is_unchanged_by_default() -> None:
    text = "status ok\nstatus ok\nstatus ok\n"
    result = denoise_text(text)

    assert result.text == text
    assert result.rules == ()


def test_denoise_view_does_not_change_gate_or_episode_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SRO_ENABLED", "1")
    page = tmp_path / "evidence.html"
    paragraphs = "".join(
        f"<p>Evidence row {index}: planted answer is 5705.</p>"
        for index in range(160)
    )
    page.write_text(
        "<html><head><script>ignored()</script></head>"
        f"<body><main><h1>Registry</h1>{paragraphs}</main></body></html>",
        encoding="utf-8",
    )
    orchestrator = SparseReadingOrchestrator(tmp_path, macro_available=True)
    orchestrator.set_context({"conversation_id": "denoise", "turn_id": "one"})
    decision, episode = orchestrator.bind_episode(
        page,
        {"goal": "selective_read", "relation": "new", "coverage": "selective"},
    )
    card = orchestrator.card(page)
    pack = orchestrator.read(
        {"artifact_id": card.artifact_id},
        "collect",
        {
            "goal": "find the planted answer",
            "type_hint": "html",
            "slots": [
                {
                    "id": "answer",
                    "question": "What is the planted answer?",
                    "expected": "number",
                }
            ],
        },
    )
    current = orchestrator.current_episode()

    assert (decision.mode, decision.code) == ("force_sro", "long_document_selective")
    assert card.type == "html"
    assert pack.slot_digest["overall_status"] == "ready"
    assert pack.slot_digest["slots"][0]["candidate"]
    assert current is not None
    assert current.episode_id == episode.episode_id
    assert current.decision == decision


def test_volatile_prefix_folding_preserves_endpoints_counts_and_unique_bodies() -> None:
    lines = [f"2026-08-14T10:15:{index:02d}Z heartbeat ok" for index in range(5)]
    lines.append("2026-08-14T10:15:05Z ERROR code=9371 retries=42")
    lines.append("2026-08-14T10:15:06Z item 42 failed")
    lines.append("2026-08-14T10:15:07Z item 43 failed")
    text = "\n".join(lines)

    result = denoise_text(text, source_kind="log", min_chars=0)

    assert "volatile_prefix" in result.rules
    assert "2026-08-14T10:15:00Z heartbeat ok" in result.text
    assert "2026-08-14T10:15:04Z heartbeat ok" in result.text
    assert "[repeated line with volatile prefix ×5]" in result.text
    assert "ERROR code=9371 retries=42" in result.text
    assert "item 42 failed" in result.text
    assert "item 43 failed" in result.text


def test_volatile_prefix_folding_supports_bracket_and_time_only_prefixes() -> None:
    text = "[10:15:00] ping ok\n[10:15:01] ping ok\n[10:15:02] ping ok\n"

    result = denoise_text(text, source_kind="log", min_chars=0)

    assert "volatile_prefix" in result.rules
    assert "[10:15:00] ping ok" in result.text
    assert "[10:15:02] ping ok" in result.text
    assert "[repeated line with volatile prefix ×3]" in result.text


def test_volatile_prefix_folding_protects_fenced_code() -> None:
    text = "```\n2026-08-14T10:15:00Z step\n2026-08-14T10:15:01Z step\n2026-08-14T10:15:02Z step\n```\n"

    result = denoise_text(text, source_kind="log", min_chars=0)

    assert "volatile_prefix" not in result.rules
    assert result.text.count("Z step") == 3


def test_repeated_block_folding_collapses_consecutive_identical_blocks() -> None:
    block = ["# STATUS", "state=running", "jobs=3"]
    text = "\n".join(block * 3) + "\nDONE\n"

    result = denoise_text(text, source_kind="terminal", min_chars=0)

    assert "repeated_block" in result.rules
    assert result.text.count("state=running") == 1
    assert "[repeated block ×3]" in result.text
    assert result.text.endswith("DONE")


def test_repeated_block_folding_keeps_non_consecutive_blocks() -> None:
    block = ["alpha", "beta", "gamma"]
    text = "\n".join(block) + "\nbetween\n" + "\n".join(block) + "\n"

    result = denoise_text(text, source_kind="text", min_chars=0)

    assert "repeated_block" not in result.rules
    assert result.text.count("alpha") == 2


def test_repeated_block_folding_requires_three_repeats_for_two_line_blocks() -> None:
    block = ["a=1", "b=2"]

    twice = denoise_text("\n".join(block * 2), source_kind="text", min_chars=0)
    thrice = denoise_text("\n".join(block * 3), source_kind="text", min_chars=0)

    assert "repeated_block" not in twice.rules
    assert "repeated_block" in thrice.rules
    assert "[repeated block ×3]" in thrice.text


def test_repeated_block_folding_protects_fenced_code() -> None:
    block = ["```python", "x = 1", "y = 2", "```"]
    text = "\n".join(block * 2)

    result = denoise_text(text, source_kind="text", min_chars=0)

    assert "repeated_block" not in result.rules
    assert result.text.count("x = 1") == 2


def test_structured_flatten_json_is_lossless_and_duplicate_key_safe() -> None:
    text = (
        '{\n  "b": 1,\n  "a": "x y",\n  "b": 2,\n'
        '  "nested": {\n    "flag": true,\n    "items": [1, 2, 3]\n  }\n}\n'
    )

    result = denoise_text(text, source_kind="json", min_chars=0)

    assert "structured_json" in result.rules
    assert "\n" not in result.text
    assert json.loads(result.text) == json.loads(text)
    assert result.text.count('"b"') == 2
    assert "x y" in result.text


def test_structured_flatten_yaml_preserves_data_and_skips_comments() -> None:
    text = "config:\n  name: demo\n  ports:\n    - 80\n    - 443\n"

    result = denoise_text(text, source_kind="yaml", min_chars=0)

    assert "structured_yaml" in result.rules
    assert yaml.safe_load(result.text) == {"config": {"name": "demo", "ports": [80, 443]}}

    commented = "# note\nconfig:\n  name: demo\n"
    result_comment = denoise_text(commented, source_kind="yaml", min_chars=0)
    assert "structured_yaml" not in result_comment.rules

    invalid = "a: [unclosed\n"
    result_invalid = denoise_text(invalid, source_kind="yaml", min_chars=0)
    assert "structured_yaml" not in result_invalid.rules

    exotic = "items: !!set\n  ? a\n  ? b\n"
    result_exotic = denoise_text(exotic, source_kind="yaml", min_chars=0)
    assert "structured_yaml" in result_exotic.rules
    assert yaml.safe_load(result_exotic.text) == yaml.safe_load(exotic)


def test_structured_flatten_is_off_for_generic_text_kind_and_short_inputs() -> None:
    text = '{\n  "a": 1,\n  "b": 2\n}\n'

    as_text = denoise_text(text, source_kind="text", min_chars=0)
    short_json = denoise_text(text, source_kind="json")

    assert not any(rule.startswith("structured_") for rule in as_text.rules)
    assert short_json.rules == ()


def test_structured_flatten_yaml_skips_alias_documents() -> None:
    aliased = "defaults: &defaults\n  host: localhost\nservice:\n  <<: *defaults\n"

    result = denoise_text(aliased, source_kind="yaml", min_chars=0)

    assert "structured_yaml" not in result.rules
    assert "&defaults" in result.text


def test_structured_flatten_yaml_enforces_size_node_and_depth_limits(monkeypatch) -> None:
    monkeypatch.setattr("sparseread.core.denoise._MAX_STRUCTURED_CHARS", 24)
    small = "config:\n  name: demo\n"
    assert "structured_yaml" in denoise_text(small, source_kind="yaml", min_chars=0).rules
    big = "config:\n  name: demo with a longer value\n"
    assert "structured_yaml" not in denoise_text(big, source_kind="yaml", min_chars=0).rules

    monkeypatch.setattr("sparseread.core.denoise._MAX_YAML_NODES", 4)
    many = "a: 1\nb: 2\nc: 3\n"
    assert "structured_yaml" not in denoise_text(many, source_kind="yaml", min_chars=0).rules

    monkeypatch.setattr("sparseread.core.denoise._MAX_YAML_DEPTH", 3)
    nested = "a:\n  b:\n    c:\n      d: 1\n"
    assert "structured_yaml" not in denoise_text(nested, source_kind="yaml", min_chars=0).rules
