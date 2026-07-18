---
id: task_loogle_shortdep_fall_of_outremer
name: "LooGLE ShortDep QA: Fall of Outremer"
category: comprehension
grading_type: automated
timeout_seconds: 300
workspace_files:
  - source: "document.txt"
    dest: "document.txt"
---

## Prompt

I have a historical document about the Fall of Outremer in my workspace as `document.txt`. Read the document and answer the following 10 questions. Write your answers to `answer.txt`, one answer per line, in order. Keep each answer concise -- a name, date, short phrase, or one sentence.

Questions:
1. What was Gregory X's 'dual crusading policy'?
2. Why did the barons of the realm pay homage to San Severino?
3. Who inherited Achaea from William II of Villehardouin?
4. Where did the king go after appointing Balian of Ibelin as administrator?
5. Which fortifications did the Mongol army occupy in September 1280?
6. How long was the uninterrupted Christian rule in Tripoli?
7. What was Qalawun's offer to Acre?
8. When did Khalil's troops take control of the outer battlements of Acre?
9. What was the impact of the fall of Acre on the Latin Christians of Outremer?
10. When was Henry II crowned king of Jerusalem?

## Expected Behavior

The agent should:
1. Read the file `document.txt` (~100k characters of historical text)
2. Locate the relevant passages for each of the 10 questions
3. Extract short, factual answers from the text
4. Write answers to `answer.txt`, one per line, in the same order as the questions

The questions test short-dependency reading: each answer comes from a single localized passage and does not require synthesizing information across distant sections.

## Grading Criteria

- [ ] Agent reads the document file
- [ ] Output file `answer.txt` is created
- [ ] Q1: dual crusading policy answer is correct (combining general crusade with smaller interventions)
- [ ] Q2: reason for barons paying homage is correct (threatened confiscation of estates)
- [ ] Q3: who inherited Achaea is correct (Charles)
- [ ] Q4: where the king went is correct (Cyprus)
- [ ] Q5: Mongol fortifications are correct (Aintab, Baghras, Darbsak)
- [ ] Q6: length of Christian rule in Tripoli is correct (180 years)
- [ ] Q7: Qalawun's offer to Acre is correct (spare the city for a bounty)
- [ ] Q8: date Khalil's troops took battlements is correct (15 May 1291)
- [ ] Q9: impact of fall of Acre is correct (fatal blow)
- [ ] Q10: date Henry II crowned king of Jerusalem is correct (15 August 1286)

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    from pathlib import Path
    import re

    workspace = Path(workspace_path)
    answer_file = workspace / "answer.txt"

    keys = [
        "file_created",
        "q1_dual_crusading_policy",
        "q2_barons_homage",
        "q3_inherited_achaea",
        "q4_king_went",
        "q5_mongol_fortifications",
        "q6_christian_rule_tripoli",
        "q7_qalawun_offer",
        "q8_khalil_battlements",
        "q9_fall_acre_impact",
        "q10_henry_crowned",
    ]

    if not answer_file.exists():
        return {k: 0.0 for k in keys}

    scores = {"file_created": 1.0}
    content = answer_file.read_text(encoding="utf-8", errors="replace").strip()
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    def line(i):
        return lines[i].lower() if i < len(lines) else ""

    # Q1: Gregory X's dual crusading policy
    a1 = line(0)
    scores["q1_dual_crusading_policy"] = 1.0 if (
        ("general" in a1 or "passagium" in a1) and ("small" in a1 or "smaller" in a1) and ("crusade" in a1)
    ) else 0.5 if ("crusade" in a1 and ("general" in a1 or "combine" in a1 or "dual" in a1)) else 0.0

    # Q2: Barons paid homage because he threatened to confiscate their estates
    a2 = line(1)
    scores["q2_barons_homage"] = 1.0 if (
        "confiscat" in a2 and "estate" in a2
    ) else 0.5 if ("threat" in a2 or "confiscat" in a2) else 0.0

    # Q3: Charles inherited Achaea
    a3 = line(2)
    scores["q3_inherited_achaea"] = 1.0 if "charles" in a3.lower() else 0.0

    # Q4: King went to Cyprus
    a4 = line(3)
    scores["q4_king_went"] = 1.0 if "cyprus" in a4.lower() else 0.0

    # Q5: Mongol army occupied Aintab, Baghras, Darbsak
    a5 = line(4)
    has_aintab = "aintab" in a5.lower()
    has_baghras = "baghras" in a5.lower()
    has_darbsak = "darbsak" in a5.lower()
    match_count = sum([has_aintab, has_baghras, has_darbsak])
    scores["q5_mongol_fortifications"] = (
        1.0 if match_count >= 3 else
        0.5 if match_count >= 2 else
        0.0
    )

    # Q6: 180 years of Christian rule
    a6 = line(5)
    scores["q6_christian_rule_tripoli"] = 1.0 if "180" in a6 else 0.0

    # Q7: Qalawun offered to spare the city for a bounty
    a7 = line(6)
    scores["q7_qalawun_offer"] = 1.0 if (
        "spare" in a7 and ("bounty" in a7 or "tribute" in a7 or "payment" in a7 or "ransom" in a7)
    ) else 0.5 if "spare" in a7 or "bounty" in a7 else 0.0

    # Q8: Khalil's troops took control by 15 May 1291
    a8 = line(7)
    scores["q8_khalil_battlements"] = 1.0 if (
        ("15" in a8 or "fifteenth" in a8.lower()) and ("may" in a8.lower()) and ("1291" in a8)
    ) else 0.5 if ("1291" in a8 and "may" in a8.lower()) else 0.0

    # Q9: Fall of Acre was a fatal blow
    a9 = line(8)
    scores["q9_fall_acre_impact"] = 1.0 if (
        "fatal" in a9.lower() and "blow" in a9.lower()
    ) else 0.5 if ("fatal" in a9.lower() or "devastat" in a9.lower() or "end" in a9.lower()) else 0.0

    # Q10: Henry II crowned 15 August 1286
    a10 = line(9)
    scores["q10_henry_crowned"] = 1.0 if (
        ("15" in a10 or "fifteenth" in a10.lower()) and ("august" in a10.lower()) and ("1286" in a10)
    ) else 0.5 if ("1286" in a10 and "august" in a10.lower()) else 0.0

    return scores
```
