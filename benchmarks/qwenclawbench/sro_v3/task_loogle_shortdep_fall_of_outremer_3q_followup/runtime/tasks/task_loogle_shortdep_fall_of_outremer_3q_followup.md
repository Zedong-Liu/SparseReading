---
id: task_loogle_shortdep_fall_of_outremer_3q_followup
name: "LooGLE ShortDep QA: Fall of Outremer 3Q Follow-up"
category: comprehension
grading_type: automated
timeout_seconds: 300
workspace_files:
  - source: "document.txt"
    dest: "document.txt"
---

## Prompt

I have a historical document about the Fall of Outremer in my workspace as `document.txt`. Read the document and answer the following 3 questions. Write your answers to `answer.txt`, one answer per line, in order. Keep each answer concise -- a date, short phrase, or one sentence.

Questions:
1. How long was the uninterrupted Christian rule in Tripoli?
2. When was Henry II crowned king of Jerusalem?
3. Where did the king go after appointing Balian of Ibelin as administrator?

## Expected Behavior

The agent should:
1. Read the file `document.txt` (~100k characters of historical text)
2. Locate the relevant localized passages for each question
3. Extract short, factual answers from the text
4. Write answers to `answer.txt`, one per line, in the same order as the questions

The questions test short-dependency reading with one numeric answer, one date answer, and one local context/pronoun answer.

## Grading Criteria

- [ ] Agent reads the document file
- [ ] Output file `answer.txt` is created
- [ ] Q1: uninterrupted Christian rule in Tripoli is correct (180 years)
- [ ] Q2: Henry II crowned king of Jerusalem date is correct (15 August 1286)
- [ ] Q3: king went to Cyprus after appointing Balian of Ibelin

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path

    workspace = Path(workspace_path)
    answer_file = workspace / "answer.txt"

    keys = [
        "file_created",
        "q1_tripoli_duration",
        "q2_henry_crowned_jerusalem",
        "q3_king_went_after_balian",
    ]

    if not answer_file.exists():
        return {k: 0.0 for k in keys}

    scores = {"file_created": 1.0}
    content = answer_file.read_text(encoding="utf-8", errors="replace").strip()
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    def line(i):
        return lines[i].lower() if i < len(lines) else ""

    a1 = line(0)
    scores["q1_tripoli_duration"] = 1.0 if "180" in a1 else 0.0

    a2 = line(1)
    scores["q2_henry_crowned_jerusalem"] = 1.0 if (
        ("15" in a2 or "fifteenth" in a2) and "august" in a2 and "1286" in a2
    ) else 0.5 if ("august" in a2 and "1286" in a2) else 0.0

    a3 = line(2)
    scores["q3_king_went_after_balian"] = 1.0 if "cyprus" in a3 else 0.0

    return scores
```
