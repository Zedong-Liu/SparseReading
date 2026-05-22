---
name: sro-results-visualizer
description: Use when updating SRO benchmark result summaries or regenerating SRO/gate figures after new tests. Guides agents to update the CSV data store, regenerate accuracy/token/trajectory plots, and record caveats. Do not rerun benchmarks unless explicitly requested.
---

# SRO Results Visualizer

## Canonical data store

All experiment data lives in one CSV:

```
figures/sro_experiment_data.csv
```

The plotting script reads from this CSV automatically. To add/update results, edit only the CSV.

## Workflow

1. Read `v3_dev.md` for recent context.
2. Edit `figures/sro_experiment_data.csv`:
   - Add new rows for new tasks.
   - Update existing rows when baselines or SRO/gate results change.
   - Columns: `model, task_id, short_name, group, benchmark, baseline_score, sro_score, baseline_tokens, sro_tokens, baseline_req, sro_req, baseline_turns, sro_turns, note`
3. Regenerate figures:

```bash
python3 figures/plot_sro_gate_results_v2.py
python3 -m py_compile figures/plot_sro_gate_results_v2.py
```

4. Verify generated files:
   - `figures/sro_gate_v2_accuracy_token_trajectory.png/.svg`
   - `figures/sro_gate_v2_benefit_map.png/.svg`
   - `figures/sro_gate_v2_outcome_board.png/.svg`
   - `figures/README_v2.md`
5. Append a short note to `v3_dev.md` with what changed.

## Plotting policy (do not change unless asked)

- Compare accuracy, total token cost, trajectory length (requests; turns only when available).
- One color per model (Qwen = blue, DeepSeek = orange). Do not color by outcome group.
- Keep group labels (`SRO win`, `Gate/pass`, `Boundary`) in the CSV/README but not as chart colors.
- Exclude catastrophic zero-deliverable failures (`task_00020`, `task_00089`).
- Keep boundary cases that are informative without dominating scale.

## Data rules

- Prefer the latest clean comparable run.
- If using an older baseline against a newer SRO/gate run, mark it in `note`.
- Group semantics:
  - `SRO win`: SRO evidence path reduced reading/trajectory while preserving or improving score.
  - `Gate/pass`: gate avoided SRO or kept path near native; useful as no-negative evidence.
  - `Boundary`: informative limit case, mixed quality, no clear compression, or native trajectory variance.
