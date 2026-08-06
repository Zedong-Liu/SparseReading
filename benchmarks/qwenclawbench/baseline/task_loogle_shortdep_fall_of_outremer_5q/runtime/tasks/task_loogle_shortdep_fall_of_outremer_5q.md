---
id: task_loogle_shortdep_fall_of_outremer_5q
name: "LooGLE ShortDep QA: Fall of Outremer 5Q"
category: comprehension
grading_type: automated
timeout_seconds: 300
workspace_files:
  - source: "document.txt"
    dest: "document.txt"
---

## Prompt

I have a historical document about the Fall of Outremer in my workspace as `document.txt`. Read the document and answer the following 5 questions. Write your answers to `answer.txt`, one answer per line, in order. Keep each answer concise -- a name, date, short phrase, or one sentence.

Questions:
1. What was Gregory X's 'dual crusading policy'?
2. Why did the barons of the realm pay homage to San Severino?
3. Who inherited Achaea from William II of Villehardouin?
4. Which fortifications did the Mongol army occupy in September 1280?
5. When did Khalil's troops take control of the outer battlements of Acre?

## Expected Behavior

The agent should:
1. Read the file `document.txt` (~100k characters of historical text)
2. Locate the relevant passages for each of the 5 questions
3. Extract short, factual answers from the text
4. Write answers to `answer.txt`, one per line, in the same order as the questions

The questions test short-dependency reading: each answer comes from a single localized passage and does not require synthesizing information across distant sections.

## Grading Criteria

- [ ] Agent reads the document file
- [ ] Output file `answer.txt` is created
- [ ] Q1: dual crusading policy answer is correct (combining general crusade with smaller interventions)
- [ ] Q2: reason for barons paying homage is correct (threatened confiscation of estates)
- [ ] Q3: who inherited Achaea is correct (Charles)
- [ ] Q4: Mongol fortifications are correct (Aintab, Baghras, Darbsak)
- [ ] Q5: date Khalil's troops took battlements is correct (15 May 1291)

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path

    workspace = Path(workspace_path)
    answer_file = workspace / "answer.txt"

    keys = [
        "file_created",
        "q1_dual_crusading_policy",
        "q2_barons_homage",
        "q3_inherited_achaea",
        "q4_mongol_fortifications",
        "q5_khalil_battlements",
    ]

    if not answer_file.exists():
        return {k: 0.0 for k in keys}

    scores = {"file_created": 1.0}
    content = answer_file.read_text(encoding="utf-8", errors="replace").strip()
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    def line(i):
        return lines[i].lower() if i < len(lines) else ""

    a1 = line(0)
    scores["q1_dual_crusading_policy"] = 1.0 if (
        ("general" in a1 or "passagium" in a1) and ("small" in a1 or "smaller" in a1) and ("crusade" in a1)
    ) else 0.5 if ("crusade" in a1 and ("general" in a1 or "combine" in a1 or "dual" in a1)) else 0.0

    a2 = line(1)
    scores["q2_barons_homage"] = 1.0 if (
        "confiscat" in a2 and "estate" in a2
    ) else 0.5 if ("threat" in a2 or "confiscat" in a2) else 0.0

    a3 = line(2)
    scores["q3_inherited_achaea"] = 1.0 if "charles" in a3 else 0.0

    a4 = line(3)
    match_count = sum(term in a4 for term in ("aintab", "baghras", "darbsak"))
    scores["q4_mongol_fortifications"] = 1.0 if match_count >= 3 else 0.5 if match_count >= 2 else 0.0

    a5 = line(4)
    scores["q5_khalil_battlements"] = 1.0 if (
        ("15" in a5 or "fifteenth" in a5) and "may" in a5 and "1291" in a5
    ) else 0.5 if ("1291" in a5 and "may" in a5) else 0.0

    return scores
```
