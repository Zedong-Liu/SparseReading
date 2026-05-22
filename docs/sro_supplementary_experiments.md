# SRO Supplementary Experiments

## Purpose

This document proposes the minimum supplementary experiments needed to make the SRO paper claims defensible. It is based on `v3_plan.md`, `v3_dev.md`, `SRO_report.md`, and the current canonical result table in `figures/sro_experiment_data.csv`.

The current evidence supports a narrow claim:

> SRO is a selective sparse-reading protocol. It helps when a task has high reading sparsity and a compact evidence-to-deliverable closure, and it should stay native or advisory when the task is full-table computation, low-sparsity analysis, or when the model does not comply with ready closure.

The supplementary experiments should not try to show that SRO always wins. They should test the three paper risks directly:

1. Are collection closures task-specific benchmark patches?
2. Does the Benefit Gate provide real value beyond forced SRO or native baselines?
3. Are typed readers replaceable executors, or is the system dependent on one reader implementation?

## Current Evidence Snapshot

The current CSV contains 16 non-catastrophic comparisons across Qwen and DeepSeek:

- SRO wins: `task_21`, `task_00012`, Qwen `task_00059`, Qwen `task_00086`
- Gate/pass cases: `task_00036`, `task_00067`, `task_00073`, DeepSeek `task_00086`, DeepSeek `task_00098`
- Boundary cases: Qwen `task_00098`, DeepSeek `task_00067`

Key risks from the logs:

- `task_00012` audit closure and `task_00086` command-security closure are strong results, but can be criticized as task-shaped.
- `task_00067` and `task_00058` show that forced SRO can add large protocol tax on low-sparse or computation-heavy tasks.
- `task_18` shows that a structured reader can be correct only after adding `calc_ready`, but the token win is not guaranteed.
- DeepSeek `task_00086` shows that closure compliance differs by model; a ready closure is useful only if the model stops reading and writes the deliverable.

## Experiment 1: Closure Generalization

### Question

Do SRO closures generalize by task shape, or are they task-specific rules written for known benchmark instances?

### Hypothesis

A closure is defensible if the same closure family improves or preserves quality and reduces reading cost on held-out tasks with the same abstract shape, without using task IDs, exact filenames, or answer hardcoding.

### Closure Families

Evaluate three closure families separately:

| Closure family | Current positive anchor | Shape definition |
| --- | --- | --- |
| Long-document slot closure | `task_21` | One long report/PDF, multiple independent factual questions, answers must be anchored in the document. |
| Audit/integrity closure | `task_00012` | Small collection with state/output/config/script evidence; deliverable asks for bug, inconsistency, integrity, audit, or important findings. |
| Command/security closure | `task_00086` | Collection with executable command source, security policy/spec, test commands, and required security classification outputs. |

### Controls

For each task, run:

1. `Native baseline`: SRO disabled.
2. `SRO no closure`: SRO tools enabled, but the target closure family disabled while generic FileCard/HintSpec/EvidencePack remains.
3. `SRO closure`: current SRO with the relevant closure family enabled.
4. `Native plus guard only` if cheap to run: SRO guard enabled but no closure EvidencePack, to separate guard benefit from closure content.

### Experiment Switches

The current implementation exposes closure ablation switches for trusted runs:

```bash
SRO_COLLECTION_CLOSURES_ENABLED=0
SRO_DISABLED_CLOSURE_FAMILIES=audit
SRO_DISABLED_CLOSURE_FAMILIES=command_security
SRO_DISABLED_CLOSURE_FAMILIES=diagnosis,panel_did,rule_table_script
```

`local_agent_comp/run_qcb_trusted_batch.sh` also supports these modes:

```bash
--modes baseline,gate,force_sro_without_gate,no_collection_closures,no_audit_closure,no_command_security_closure
```

Use `no_audit_closure` on `task_00012` and held-out audit bundles. Use `no_command_security_closure` on `task_00086` and perturbed command-security bundles. Use `no_collection_closures` only as a broad sanity check because it disables unrelated closure families too.

### Task Selection

Minimum held-out set:

| Family | Include current anchor? | Held-out tasks to add | Selection rule |
| --- | --- | --- | --- |
| Long-document slot closure | Yes, `task_21` | 2 long PDF/report QA tasks from PinchBench or manually converted QwenClawBench assets | Must require at least 5 independent facts; not all answers appear in one nearby paragraph. |
| Audit/integrity closure | Yes, `task_00012` | 2 audit/state-consistency bundles from QwenClawBench | Must contain at least one state/config/output/script cross-check; no task-specific filenames in closure trigger. |
| Command/security closure | Yes, `task_00086` | 1-2 command/policy/security-analysis bundles | Must include policy conflict or classification logic plus required deliverable files. |

If no natural held-out task exists for command-security, use a synthetic variant generated by perturbing `task_00086` assets: rename files, change command strings, change counts, and change required output filenames. The synthetic variant is valid only if the closure code sees no task ID or exact old filename dependency.

Concrete QwenClawBench held-out candidates from local inspection:

| Family | Candidate | Why it is useful |
| --- | --- | --- |
| Audit/general diagnosis | `task_00029_openclaw_runtime_diagnostics_skill_and_health_audit` | Multi-file logs/config/session audit; broad enough to test whether closure finds cross-file runtime facts rather than stock-fetcher fields. |
| Audit/general diagnosis | `task_00031_server_workspace_audit_skill_and_config_change_analysis` | Config-change and workspace audit with `.new`/current diffs, SSL/credential checks, and multiple source types. |
| Audit/general diagnosis | `task_00063_second_pass_quality_audit_of_question_169663` | Quality audit with answer-key/SVG/reference cross-checks; tests whether audit shape generalizes beyond operations. |
| Audit/general diagnosis | `task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site` | Compact script/config/site-inventory audit; useful for off-by-one and sync bug validation. |
| Security/policy assessment | `task_00087_telegram_bot_credential_management_security_review` | Credential/config/log review; tests policy-vs-runtime security reasoning without command-prefix structure. |
| Security/policy assessment | `task_00088_personal_assistant_security_policy_assessment` | Trust-policy and audit-log reconciliation; tests policy conflict handling. |
| Security/policy assessment | `task_00091_security_policy_assessment_for_llm_assistant_input_trust_model` | Two policy versions plus incidents/compliance artifacts; strong held-out policy-resolution task. |
| Security/policy assessment | `task_00095_prompt_injection_defense_framework_with_skill_creation` | Security-framework task; useful but weaker because it also asks for skill creation. |

Negative controls:

| Candidate | Expected behavior |
| --- | --- |
| `task_00008_heartbeat_joke_scheduler_with_conditional_dispatch` | No audit or command-security closure should fire. |
| `task_00036_find_largest_file_in_downloads_directory` | Native/pass; closure should not fire. |
| `task_00067_write_sparql_query_for_product_reviews_containing_iphone` | Native/pass; query/spec task should not become closure-driven. |
| `task_00058_did_regression_on_simulated_panel_data` | Native/pass; full-table computation should not be forced into closure. |

QwenClawBench is sufficient for the first closure-generalization pass because it has multiple held-out audit/security tasks across domains. The remaining limitation is benchmark-style commonality: all tasks share QwenClawBench prompt/rubric conventions. For paper-strength evidence, add at least two synthetic perturbations per risky closure family.

Recommended perturbations:

| Perturbation | Applied to | Purpose |
| --- | --- | --- |
| Rename files/dirs/fields | `task_00012`, `task_00086` | Prove closure triggers by shape, not exact filenames or benchmark vocabulary. |
| Format migration | `task_00012` config YAML to JSON; `task_00086` policy YAML to TOML/JSON | Test format robustness. |
| Cross-domain audit transfer | Synthetic log-monitoring or scraper pipeline using state-list/output-record shape | Test audit closure beyond finance/stock announcements. |
| Policy multiplication | `task_00086` with extra conflicting policy/bulletin files and new pattern IDs | Test dynamic ID extraction and conflict priority. |
| Negative insertion | Add misleading `seen_ids`/`records` files to non-audit task | Measure false-positive closure rate. |

Implementation status as of 2026-05-20:

- Audit closure now detects state/output shape through generic state-list and record-list fields instead of requiring only `seen_ids`, `announcementId`, or `announcements_*.json`.
- Command-security closure now detects renamed policy/prefix/test/conflict bundles and extracts rule IDs dynamically instead of hardcoding `KI-007`, `INJ-004`, `LEGACY-R003`, or `SAB-2025-001`.
- Unit coverage includes renamed audit and renamed command-security fixtures. This is not a replacement for benchmark-level validation, but it removes the most obvious task-ID/filename dependency before running held-out tasks.

### Metrics

Primary:

- Score delta versus native baseline.
- Total tokens and request count.
- SRO calls by mode.
- Closure-use rate: whether a `collect` EvidencePack with the closure anchor was produced.
- Stop-after-ready rate: whether the agent writes the deliverable within 2 assistant turns after a ready closure.

Secondary:

- Raw source rereads after ready closure.
- Missing required output files.
- Judge failure reason category: missing evidence, wrong reasoning, incomplete deliverable, repeated verification, or tool failure.

### Support Criteria

Closure generalization is supported if:

- Held-out tasks show no average score regression greater than 1 percentage point versus native.
- At least two held-out tasks reduce total tokens or request count by 25% or more.
- The closure fires from shape-level signals, not task IDs or exact benchmark names.
- Stop-after-ready rate is higher with closure than without closure.

### Refutation Criteria

The closure claim is weakened if:

- Gains appear only on the original anchor tasks.
- Disabling the closure has no measurable effect.
- Held-out tasks trigger closure but miss key facts that native reads find.
- The closure requires exact filenames or benchmark-specific strings to work.

## Experiment 2: Benefit Gate Ablation

### Question

Does Benefit Gate actually improve the system, or are the results explainable by either always using SRO or never using SRO?

### Hypothesis

The gate is useful if it preserves SRO wins on high-sparse tasks while avoiding protocol tax on low-benefit tasks.

### Conditions

Run the same task set under:

1. `Native`: SRO disabled.
2. `Force SRO`: all supported objects route through SRO where possible.
3. `Current Gate`: current `force_sro/native/advisory` policy.
4. `Oracle Gate`: post-hoc hand-labeled upper bound based on task taxonomy, used only for analysis, not as a deployed method.

### Task Selection

Use a balanced taxonomy rather than a convenience set:

| Taxonomy | Positive or negative expectation | Current examples |
| --- | --- | --- |
| Long-document multi-fact QA | SRO positive | `task_21` |
| Structured full-table computation | Native or boundary | `task_18`, `task_00058` |
| Multi-file audit/integrity | SRO positive | `task_00012` |
| Query/spec generation | Native positive | `task_00067` |
| Security/rules closure | Model-dependent | `task_00086` |
| Mixed diagnosis | Boundary/advisory | `task_00098` |
| Large financial/report analysis | Native/advisory | `task_00073` |
| Simple file property task | Native/pass | `task_00036` |

Minimum executable set:

- Qwen: `task_21`, `task_00012`, `task_00067`, `task_00086`, `task_00098`
- DeepSeek: `task_21`, `task_00012`, `task_00067`, `task_00086`, `task_00098`

This is enough to cover positive, negative, and model-compliance cases without rerunning the full suite.

### Metrics

Primary:

- Score.
- Total tokens.
- Request count.
- SRO activation decision: `force_sro`, `native`, or `advisory`.
- Regret versus oracle: extra tokens and score loss caused by the current gate decision.

Secondary:

- Number of broad-read blocks.
- Number of ready-closure rereads.
- Native tool fallback count.
- Catastrophic failure flag: no deliverable, timeout, zero score, or invalid output.

### Support Criteria

Benefit Gate is supported if:

- `Current Gate` is close to `Oracle Gate` on average token cost while preserving score.
- `Current Gate` beats `Force SRO` on known negative shapes such as `task_00067` and computation-heavy tasks.
- `Current Gate` keeps SRO active on known positive shapes such as `task_21` and `task_00012`.
- Model-profile gating is needed only for closure-compliance shapes, not as a broad per-model switch.

### Refutation Criteria

The Benefit Gate claim is weakened if:

- `Force SRO` matches or beats current gate on both tokens and score.
- `Native` matches current gate on positives, making SRO unnecessary.
- The current gate requires many task-specific exceptions.
- Gate decisions are unstable across harmless file renames or directory layout changes.

## Experiment 3: Typed Reader Replaceability

### Question

Are typed readers replaceable executors behind the same protocol, or are paper results tied to one reader implementation?

### Hypothesis

If FileCard, HintSpec routing, artifact continuity, and EvidencePack feedback are the system core, then swapping a reader implementation should preserve the agent-facing protocol and most task behavior, with predictable tradeoffs in evidence quality or token cost.

### Reader Swap Targets

Run replaceability at the reader-family level:

| Reader family | Current implementation | Replacement candidate |
| --- | --- | --- |
| Text/PDF | Current `TextReader` with extracted text, heading-aware chunks, `collect+slots` | A simpler lexical reader: fixed-size chunks, BM25/keyword scoring, same EvidencePack schema. |
| Structured CSV/XLSX | Current `StructuredReader` with schema/sample/full-table and `calc_ready` TSV | Native script reader: bounded Python/csv/openpyxl extraction that returns the same `calc_ready` and EvidencePack fields. |
| Collection | Current `CollectionReader` with audit/diagnosis/security closures | Closure-disabled generic collection reader that returns source-keyed excerpts only. |

Do not add new agent-facing tools. The replacement must implement the same `sro_card` / `sro_read` contract.

### Controls

For each reader family:

1. `Current reader`.
2. `Replacement reader`.
3. `Reader removed/native fallback`.

### Task Selection

| Reader family | Tasks |
| --- | --- |
| Text/PDF | `task_21` plus at least one held-out long PDF/report QA task. |
| Structured | `task_18`, `task_00059`, and one full-table negative task such as `task_00058`. |
| Collection | `task_00012`, `task_00086`, `task_00098`. |

### Metrics

Primary:

- Protocol compatibility: same tool names, valid FileCard, valid HintSpec, valid EvidencePack.
- Score delta versus current reader.
- Token delta versus current reader.
- Artifact continuity errors: path rediscovery, missing artifact id, invalid refine/verify.
- Evidence sufficiency: unresolved slots, missing `calc_ready`, missing required closure fields.

Secondary:

- Reader implementation complexity: lines of code touched and added dependencies.
- Failure localization: whether failures are reader evidence errors or downstream deliverable errors.

### Support Criteria

Reader replaceability is supported if:

- Replacement readers can run without changing prompts, tools, or benchmark harness.
- At least one family replacement preserves score within 1 percentage point on its primary task while changing token cost in the expected direction.
- Collection closure removal degrades closure tasks while leaving the protocol intact, showing that the protocol and closure content are separable.
- Structured native-script reader can reproduce `calc_ready` behavior, showing that `calc_ready` is a protocol payload rather than tied to one parser.

### Refutation Criteria

The replaceability claim is weakened if:

- Tool prompts or agent-facing schemas must change for each reader.
- Swapped readers frequently break artifact continuity.
- Current results depend on hidden reader-specific fields not documented in EvidencePack.
- Replacement readers cannot produce comparable evidence without task-specific code.

## Experiment 4: Robustness To Renaming And Perturbation

### Question

Do SRO gate and closure decisions depend on brittle file names, task IDs, or benchmark-specific phrasing?

### Perturbations

For `task_00012`, `task_00086`, and `task_21`:

- Rename task directory and non-essential files.
- Shuffle file order in directory listings.
- Rewrite task prompt with synonymous wording while preserving required outputs.
- For structured tasks, reorder rows where row order is not semantically required.
- For closure tasks, change numeric values in assets and expected outputs where the judge can be updated or a manual oracle can score.

### Metrics

- Gate decision stability.
- Closure trigger stability.
- Score and token delta versus unperturbed run.
- Exact answer/value changes reflected correctly in output.

### Support Criteria

Robustness is supported if harmless renames and prompt paraphrases do not change the gate decision or closure family, and value perturbations propagate to the final answer.

### Refutation Criteria

The mechanism is brittle if it only works with original benchmark names, original file order, or original numeric values.

## Minimum Executable Priority

The experiments should be run in this order. Stop after each tier if the claim is already refuted, because later tiers depend on earlier validity.

### P0: Gate Ablation On Existing Tasks

Goal: prove the Benefit Gate is not cosmetic.

Run `Native`, `Force SRO`, and `Current Gate` on:

- Qwen: `task_21`, `task_00012`, `task_00067`, `task_00086`
- DeepSeek: `task_21`, `task_00012`, `task_00067`, `task_00086`

Why first:

- Uses existing tasks and metrics.
- Directly addresses the strongest paper risk.
- Produces a clean table for the main paper or appendix.

Success bar:

- Current Gate must match SRO positives on `task_21` and `task_00012`.
- Current Gate must avoid Force-SRO overhead on `task_00067`.
- Current Gate must explain the Qwen/DeepSeek split on `task_00086` as closure compliance, not arbitrary tuning.

### P1: Closure Held-Out And Perturbation

Goal: show closure is shape-level.

Run:

- One held-out long PDF/report QA task.
- One held-out audit/integrity bundle.
- One perturbed `task_00086` command-security variant.

Conditions:

- Current SRO with closure.
- SRO with the relevant closure disabled.
- Native baseline.

Success bar:

- Closure-enabled SRO must beat closure-disabled SRO on at least two of three tasks in either score or trajectory length.
- Perturbed task must not require original task IDs or exact original filenames.

### P2: Typed Reader Swap Smoke

Goal: show the protocol is not identical to one reader implementation.

Run one smoke per family:

- Text/PDF: `task_21` with simple lexical reader.
- Structured: `task_18` or `task_00059` with native-script `calc_ready` reader.
- Collection: `task_00012` with closure-disabled generic collection reader.

Success bar:

- All replacement readers must preserve the same tool schema and produce valid EvidencePack objects.
- At least one replacement should preserve correctness close to current reader.
- Closure-disabled collection should degrade `task_00012`, proving closure content matters while the protocol remains runnable.

### P3: Broader Taxonomy Replication

Goal: turn the appendix from a pilot into a stable result set.

Run the balanced taxonomy across Qwen and DeepSeek:

- `task_21`
- `task_00012`
- `task_00036`
- `task_00058` or `task_18`
- `task_00059`
- `task_00067`
- `task_00073`
- `task_00086`
- `task_00098`

Success bar:

- Report per-taxonomy results, not only averages.
- Keep catastrophic failures in an appendix table even if excluded from main plots.
- Use `figures/sro_experiment_data.csv` as the canonical data source after runs are validated.

## Reporting Template

Each supplementary experiment should record one row per model/task/condition:

| Field | Meaning |
| --- | --- |
| `model` | Qwen, DeepSeek, or other tested model. |
| `task_id` | Benchmark task ID or synthetic variant ID. |
| `taxonomy` | Long-doc QA, audit, structured computation, query/spec, security closure, diagnosis, or boundary. |
| `condition` | Native, Force SRO, Current Gate, Oracle Gate, closure-disabled, reader-swap, etc. |
| `gate_decision` | `force_sro`, `native`, `advisory`, or `disabled`. |
| `reader_family` | text, structured, collection, native. |
| `closure_family` | slot, audit, diagnosis, command-security, none. |
| `score` | Judge score. |
| `total_tokens` | Total token count from validated result JSON. |
| `request_count` | Number of model requests. |
| `sro_calls` | Count of `sro_card` and `sro_read` calls. |
| `ready_to_write_turns` | Turns from ready EvidencePack to first deliverable write. |
| `failure_category` | None, evidence miss, wrong reasoning, incomplete deliverable, timeout, tool failure, repeated verification. |
| `result_path` | Validated result JSON path. |
| `transcript_path` | Validated transcript path. |

## Interpretation Rules

- Do not average away taxonomy. Report long-doc QA, audit, structured computation, query/spec, security closure, and diagnosis separately.
- Do not count native/gate-pass runs as SRO wins. They support the Benefit Gate claim, not the reader claim.
- Do not hide catastrophic failures. Keep them out of main plots only if the plot is explicitly a non-catastrophic comparison, and list them in an appendix table.
- Treat model-specific closure compliance as an empirical capability flag. Avoid adding broad per-model branches unless the failure repeats across several closure families.
- Prefer small held-out or perturbed tasks over adding more benchmark-anchor reruns. The criticism to answer is generalization, not just variance.
