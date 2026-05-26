# Sparse Reading Orchestrator v3 Plan

## Summary

v3 implements a Sparse Reading Orchestrator protocol instead of another observation post-compressor:

`FileCard -> HintSpec -> Typed Reader -> EvidencePack -> Refine / Verify`

Principles:

- SRO is a protocol loop, not a specific reader or compression algorithm.
- Typed readers are replaceable executors; artifact continuity, HintSpec routing, and EvidencePack feedback are the system core.
- Phase 1 proves the behavior loop first, then tunes compression ratios and reader details.

Baseline:

- Use clean repo `/data1/lzd/nanobot-sro-v3` on host, `/data/lzd/nanobot-sro-v3` in container.
- Keep `/data/lzd/nanobot` v2 read-only for reference.
- Minimize nanobot core changes: tool registration, skill injection, broad-read handoff, conservative command policy.
- Phase 1 focuses only on structured long files and unstructured long text in PinchBench.

## Protocol And Interfaces

SRO module:

```text
nanobot/sparse_reading/
  models.py
  detector.py
  orchestrator.py
  tools.py
  readers/structured.py
  readers/text.py
  policy.py
```

Agent-facing tools:

```text
sro_card(path: string) -> FileCard
sro_read(target: object, mode: scout|focus|refine|verify, hint: HintSpec) -> EvidencePack
```

Target rule:

- First object discovery uses `{"path": "/path/to/file"}`.
- Follow-up should use `{"artifact_id": "..."}`.
- `path` discovers an object; `artifact_id` continues negotiation over the same object.
- `refine` and `verify` require `artifact_id`; otherwise return a protocol error with a next action.

HintSpec schema:

```json
{
  "goal": "one sentence",
  "needles": ["1-6 short keywords or phrases"],
  "want": "fact|count|verbatim|table|schema|list",
  "scope": "new|narrow|expand|verify",
  "artifact": "artifact id; required for refine/verify",
  "type_hint": "auto|pdf|text|csv|xlsx|json|yaml|xml|mixed",
  "must_keep": ["optional exact strings"]
}
```

EvidencePack includes artifact id, mode, type, short summary, optional skeleton, anchored evidence blocks, unresolved needles/slots, and a suggested next HintSpec when useful.

Phase 1 also records evidence about whether the macro interface should remain mode-based or move toward object/operation-based tools.

## Benefit Gate Philosophy

SRO should be selective infrastructure, not a universal wrapper around every file read. The gate decides whether an artifact should enter SRO based on the local reading economics of the artifact and task context: expected sparse-reading gain, risk of protocol tax, and whether native tool use is already the cheaper path.

The gate remains deterministic and compact. It is not a model-specific switch, not a learned classifier, and not a new agent-facing protocol. All models should see the same policy surface, while the policy itself should leave room for smarter models to use native paths when they are already efficient.

Current decision modes:

- `force_sro`: use SRO when the task has clear sparse-reading structure, such as long PDF/report QA, multi-fact section expansion, audit/diagnosis bundles, or collection search where broad native reads are likely wasteful.
- `native`: avoid SRO handoff when the task is mostly full-table computation, script execution, small structured file reading, or benchmark shapes where SRO has repeatedly added packaging/debug tax without reducing observation cost.
- `advisory`: expose SRO as an available option but do not force interception when the benefit is ambiguous.

The preferred granularity is artifact-level, with file-level refinement inside a bundle. A top-level bundle may qualify for SRO, but small child files should remain native unless they are themselves long, high-value, or likely to trigger broad observation dumps. Conversely, a mostly native task may still route one large report/log/source artifact through SRO if that single file has real sparse-reading potential.

Gate success criteria:

- Positive cases keep the short SRO trajectories that already work, especially `sro_card -> sro_read(mode=collect, slots=...) -> verify/refine only unresolved slots -> write_file`.
- Negative or low-benefit cases stay close to baseline/native behavior, with no SRO guard, no handoff JSON, and no repeated negotiation tax.
- The gate should fail open toward native/advisory on ambiguous computation-heavy tasks, and fail closed toward `force_sro` only when the artifact shape strongly predicts sparse-reading benefit.
- Readiness guards only apply after SRO has produced usable evidence; they should not block native source reads before SRO has actually helped.

High-priority small repair:

- Text/PDF slot readiness guards should allow bounded `verify` on explicitly requested low-quality slots instead of suppressing all verification after `overall_status=ready`.
- Keep the current ready guard for repeated broad `collect`/`focus`/`refine` calls, and keep collection-closure ready guards unchanged.
- Only let `verify` pass when the requested slot IDs are already known and the candidate is suspicious, for example `confidence < 0.9`, too short for the expected answer shape, visibly truncated, or format-incompatible with the slot expectation.
- Acceptance criteria: a `task_21`-style regression where bad category candidates such as `"February: 7,"` can be verified without falling back to broad PDF reads, while the compact `sro_card -> collect+slots -> write_file` path remains available for clean ready slots and token cost does not regress materially.

## Implementation Changes

- Clone upstream nanobot into `/data1/lzd/nanobot-sro-v3`.
- Patch `/data/lzd/agent-comp/openclaw_shim.py` so `_get_nanobot_path()` respects `NANOBOT_SOURCE_PATH`.
- Add `SRO_V3` benchmark strategy using `SRO_ENABLED=1` and no `CONTEXT_LENS_STRATEGY`.
- Register SRO tools only when `SRO_ENABLED=1`.
- Add always-on `nanobot/skills/sparse-reading/SKILL.md` for SRO runs.
- Broad `read_file` on supported large objects returns an SRO handoff; small files and narrow paginated reads pass through.
- `exec` outputs are not generically compressed; short script/calculation outputs pass through.

Reader choices:

- CSV uses stdlib `csv`.
- XLSX uses `openpyxl`; runtime dependency gaps are handled by dependency/env setup, not a custom XLSX parser.
- JSON/YAML/XML expose key paths and exact bounded evidence.
- PDF uses extracted text view with page/line anchors where possible.
- TXT/MD/README use the original text directly, without extra intermediate views.
- No embedding, extra model, L3 reranker, code/log reader, learning selector, or training loop in phase 1.

Policy remains conservative:

- Block obvious broad raw dumps: large `cat`, unbounded `pdftotext <pdf> -`, and raw PDF `grep/rg`.
- Detect exact repeated failed commands for the same target only; near-duplicate detection is deferred.
- Redirect to SRO protocol without pretending to answer the task.

Budgets are configurable defaults, not correctness assumptions.

## Evaluation Plan

Primary phase 1 gate:

```text
task_21_openclaw_comprehension
task_18_spreadsheet_summary
```

Compare `BASELINE`, `L2` or `HYBRID`, and `SRO_V3`.

Record score, user/tool/obs tokens, request count, FileCard count, valid HintSpec count, EvidencePack count by mode, artifact follow-up count, unresolved trend, broad-read blocks, exact repeated-failure blocks, raw dump count, native sparse-read usage, unstable native interface usage, and SRO usage by macro mode/object type.

Acceptance:

- Average observation/tool token reduction on target long-input tasks is at least 50%.
- Overall score drop is at most 1 percentage point versus baseline.
- `task_21` must not hit max iterations or repeat-failure loops.
- `task_18` must preserve correctness and avoid wrapping tax on short calculation outputs.

## Test Plan

- Unit tests for FileCard detection, HintSpec validation, artifact continuity, structured readers, text readers, and conservative policy.
- Integration fixture: `sro_card -> scout -> focus -> refine -> verify`.
- Verify broad `read_file` handoff, short command passthrough, and sufficient EvidencePack anchors.
- First PinchBench run only `task_21` and `task_18`; add same-object-type tasks only after inspection.

## P0: Skill Presentation Simplification

Objective:

- Improve model adherence and reduce always-on instruction cost by rewriting only
  `nanobot/skills/sparse-reading/SKILL.md` as a compact protocol guide.
- Preserve the current SRO protocol, reader behavior, closure behavior, benefit
  gate decisions, guard behavior, and tool schemas.

Scope:

- Replace repeated prose with one routing table, one follow-up/stop rule table,
  and one canonical `collect` plus `slots` example.
- Keep the established positive trajectories for long PDF/text multi-question
  tasks and collection audit tasks.
- Defer tool-description and JSON-schema changes to a separate phase after
  this skill-only comparison.

Acceptance criteria:

- The skill removes duplicated prose while retaining explicit instructions for
  `collect` plus `slots`, artifact continuity, `overall_status`, `calc_ready`,
  collection-ready output, and any protocol constraints required by observed
  invalid or inefficient trajectories. Word count is recorded, not gated.
- Sparse-reading unit tests pass with no changes to runtime implementation or
  existing test expectations.
- A controlled `gate` comparison runs the same current source tree with the
  legacy skill and compact skill on DeepSeek-V4-Flash and DeepSeek-V4-Pro for:
  `task_21_openclaw_comprehension`,
  `task_loogle_shortdep_fall_of_outremer_3q_followup`, and
  `task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check`.
- The compact-skill runs preserve score on these positive tasks and do not add
  repeated source-reading or guard-loop trajectories. Token/request changes
  are recorded; a further wording iteration is required if an avoidable
  adherence regression is observed.
