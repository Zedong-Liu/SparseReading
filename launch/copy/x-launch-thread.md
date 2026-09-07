# X launch thread

Reviewer: attach `assets/launch/sparseread-demo.gif` on tweet 2. Handle is unassigned — do not invent one. Do not post from this workspace.

## 1

Agents still dump the whole file before they know what the next decision needs.

On representative high-sparsity tasks, only 6–15% of that reading budget is useful.

SparseRead: Read only the evidence needed for the next decision.

## 2

Flagship replay (storyboard GIF, labeled REPLAY — not a live model).

Five local facts from a ~100k-character history chapter. The answers were always local. Full Read still admitted the whole chapter.

## 3

LooGLE 5Q / DeepSeek-V4-Flash / NanoBot. Single run, Figure 8 — not an average.

369,042 → 50,699 tokens (−86.3%)
17 → 4 requests
score 1.0 / 1.0

## 4

This is not post-hoc compression. The expensive broad read is what we try not to admit.

Object Preview → Read Intent → Read Gate (`force` / `advisory` / `native`) → Reader Backend → Evidence State.

## 5

Six models × five scenarios: up to 92.9% fewer tokens and up to 89.0% less wall time vs Naive.

All 30 NanoBot cells reduce both costs. Quality holds or improves on 26/30 — not 30/30.

Token volume ≠ dollar cost.

## 6

Stronger models still over-read.

Claude Opus 5 saves 59.6–89.0% of tokens and 51.3–78.1% of wall time on sparse-fit scenarios. That range is from the paper; this checkout has no Opus trace to replay.

## 7

Not always-on. Force mode on a SPARQL / global-statistics task increases tokens by +309.2%.

The Read Gate exists to stay out of that regime.

## 8

Paper: https://arxiv.org/pdf/2608.22237
Code: https://github.com/Zedong-Liu/SparseReading

No-key replay: `make demo`

claim_ids: paper-intro-useful-6-15, case-flagship-loogle5q-flash, case-flagship-loogle5q-flash-reduction, paper-abs-token-max, paper-abs-wall-max, paper-s62-30-of-30-cost, paper-abs-quality-preserve, paper-s62-opus-sparsefit, paper-fig5-t67
