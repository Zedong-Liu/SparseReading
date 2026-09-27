"""Reporting tests use synthetic content; real local records are never fixtures."""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("record_replay", Path(__file__).resolve().parents[1] / "scripts/replay_host_records.py")
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


def test_partial_focus_followup_and_private_body_exclusion(tmp_path):
    body = "私有记录🙂\nROOT: fixed\nOWNER: Ada\n"
    (tmp_path / "记录.md").write_text(body, encoding="utf-8")
    calls = []

    def call(name, params):
        calls.append((name, params))
        if name == "sro_decide":
            return {"decision": {"mode": "force_sro"}}
        if name == "sro_preview":
            return {"preview_pack": {"artifact_id": "one", "raw_ref": "ref"}}
        if name == "sro_read":
            text = "ROOT: fixed" if params["mode"] == "focus" else "OWNER: Ada"
            return {"evidence_pack": {"evidence": [{"anchor": "L2-L3", "text": text}]}}
        start, end = params["range"]["start"], params["range"]["end"]
        return {"raw": {"content": body[start:end]}}

    case = {"id": "synthetic-case", "path": "记录.md", "goal": "Find the cause and owner", "needles": ["ROOT"],
            "expected": ["fixed", "Ada"], "route": "force_sro", "followup_hint": {"goal": "Find the owner", "needles": ["OWNER"]}}
    report = replay.replay_case(call, case, tmp_path)
    assert report["initial_evidence_pass"] is False
    assert report["evidence_pass"] and report["raw_pass"] and report["anchors_pass"]
    rendered = json.dumps(report)
    assert all(private not in rendered for private in ["私有记录", "fixed", "Ada", "记录.md", str(tmp_path)])
    assert all("expected" not in params for _, params in calls)


def test_native_route_is_not_claimed_as_sparse_evidence(tmp_path):
    (tmp_path / "session.jsonl").write_text('{"role":"user"}\n')
    report = replay.replay_case(lambda *_: {"decision": {"mode": "native"}},
                               {"id": "native", "path": "session.jsonl", "expected": ["user"], "route": "native"}, tmp_path)
    assert report["evidence_pass"] is None and report["raw_pass"] is None
    assert report["native_source_pass"]


def test_outside_target_and_invalid_oracle_fail_before_host_call(tmp_path):
    def unexpected(*_):
        pytest.fail("invalid cases must fail before tool calls")
    with pytest.raises(ValueError, match="inside workspace"):
        replay.replay_case(unexpected, {"path": "../outside.md"}, tmp_path)
    (tmp_path / "note.md").write_text("a note")
    with pytest.raises(ValueError, match="expectations must exist"):
        replay.replay_case(unexpected, {"path": "note.md", "expected": ["not in note"]}, tmp_path)
