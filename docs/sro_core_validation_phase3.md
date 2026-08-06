# SRO Core Validation And Phase 3 Plan

## Decision

Do not spend more time on SparseRead public API ergonomics until the core paper
claims are cleaner.

The current blockers are:

1. Closure value is visible, but closure generalization is not yet proven.
2. `task_00086` is not a clean benchmark result because the gate run receives
   full LLM-judge credit but fails all automated file-output checks.

No other experiment is a hard blocker before larger-model validation. Once the
two blockers above are addressed, move to Phase 3: run canonical SRO, with no
ablation switches, on a small representative task set using DeepSeek-V4-Pro.

## Blocker 1: Closure Generalization

### Claim To Validate

The audit and command-security closures should be shape-level sparse-reading
closures, not task-specific answer generators.

### Minimum Evidence

Run at least one held-out or perturbed case per risky closure family:

- Audit/integrity closure:
  - anchor: `task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check`
  - held-out candidates: `task_00029`, `task_00031`, `task_00063`, `task_00094`
  - acceptable synthetic fallback: perturb `task_00012` filenames, state keys,
    output record fields, and task wording without changing the grading target.

- Command-security closure:
  - anchor: `task_00086_command_prefix_security_analysis`
  - held-out candidates: `task_00087`, `task_00088`, `task_00091`, `task_00095`
  - acceptable synthetic fallback: perturb `task_00086` file names, policy file
    format, pattern ids, command strings, and output wording without changing
    the core security classification target.

### Support Criteria

- The closure triggers without task id or exact original filename dependence.
- Closure-enabled SRO preserves or improves task completion versus closure-disabled SRO.
- The closure-disabled run fails for missing/insufficient digest, incomplete
  deliverable, or excessive rereading, not because of unrelated runtime failure.
- Negative controls do not accidentally trigger audit/security closures.

## Blocker 2: Fix `task_00086`

### Observed Problem

Latest result:

- `gate`: score `0.400`, tokens `683,196`, requests `20`
- `no_command_security_closure`: score `0.000`, tokens `551,719`, requests `18`

The gate run received LLM judge `1.0` on all three qualitative dimensions, but
all automated checks were `0.0` because `security_analysis_report.md` and
`command_classifications.json` were not detected in the benchmark workspace.

Local inspection did not find those files under the gate workspace or runset.
This means the issue is not simply an automated grader false negative; the
deliverables did not land where the grader checks them.

Root cause found on 2026-05-21:

- The benefit gate had a model-specific DeepSeek bypass that sent
  command-security bundles down the native path instead of the compact closure.
- The DeepSeek run then read the source files directly, consumed large context,
  and reached output-limit continuation before the files were written.
- The final attempted `write_file` appeared as DeepSeek DSML text in the
  assistant message, not as an executed structured tool call.

### Fix Direction

Patch direction:

- Force command-security bundles through the compact closure for all models,
  including DeepSeek.
- Recover DeepSeek DSML tool-call text into real tool calls when no structured
  `tool_calls` are returned.
- Keep the closure deliverable instruction short and require relative output
  paths: `command_classifications.json` and `security_analysis_report.md`.

The fix should be validated with canonical `gate` mode on `task_00086`, not with
an ablation switch.

Success:

- `automated.output_report_exists == 1.0`
- `automated.output_json_exists == 1.0`
- score should reflect both automated and LLM quality, not only LLM judge.

## Phase 3: Larger-Model Canonical SRO

Run only canonical SRO:

- mode: `gate`
- no `SRO_DISABLED_CLOSURE_FAMILIES`
- no `SRO_COLLECTION_CLOSURES_ENABLED=0`
- no `SRO_BENEFIT_GATE_OVERRIDE`

Use DeepSeek-V4-Pro. With native DeepSeek API keys, the valid model id is
lowercase:

```bash
API_KEY="$DEEPSEEK_API_KEY" \
API_BASE_URL="https://api.deepseek.com/v1" \
BENCH_MODEL=deepseek-v4-pro \
TIMEOUT_MULTIPLIER=2 \
PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000 \
benchmarks/run_qcb_trusted_batch.sh \
  --runset deepseek_v4_pro_sro_phase3_$(date +%Y%m%dT%H%M%S) \
  --modes gate \
  --tasks \
    task_21_openclaw_comprehension \
    task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
    task_00086_command_prefix_security_analysis \
    task_00067_write_sparql_query_for_product_reviews_containing_iphone
```

Rationale for the four tasks:

- `task_21`: long-document multi-fact sparse reading.
- `task_00012`: audit/integrity closure.
- `task_00086`: command-security closure, only after the output-file issue is fixed.
- `task_00067`: negative/control task where Benefit Gate should avoid SRO tax.

If runtime budget allows a fifth task, add:

- `task_00058_did_regression_on_simulated_panel_data`: computation-heavy native/pass case.

Report:

- score
- total tokens
- request count
- result path
- whether SRO tools/closures were actually used
- whether required output files were created
- any timeout/output-limit failure
