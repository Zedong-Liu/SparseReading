# Hugging Face summary

**Read only the evidence needed for the next decision.**

SparseRead is a training-free, model-transparent pre-reading layer that turns broad object access into bounded, iterative evidence acquisition. It intercepts native file / PDF / collection reads, shows an Object Preview, takes a Read Intent, routes through a Read Gate (`force` / `advisory` / `native`), and returns an anchored Evidence State instead of dumping the artifact. It is not post-hoc summarization, not KV-cache compression, and it does not replace native tools on every request.

Paper: [Read Less, Solve More: Token-Efficient Sparse Reading for AI Agents](https://arxiv.org/pdf/2608.22237) (arXiv:2608.22237).

## Results (qualifiers attached)

- Across six frontier models and five workload scenarios, SparseRead reduces token volume by **up to 92.9%** and wall time by **up to 89.0%** vs Naive (maxima over 30 NanoBot model–scenario cells, not averages).
- All 30 cells reduce both token volume and wall time. Quality is preserved or improved on **26/30** cells, not 30/30 (four cells sit between −0.10 and −0.02).
- Token volume ≠ dollar cost. No published $/token serving figure.
- Figure 8 vs ACON (zero-shot compressor; auxiliary calls included) and Complexity Trap last-10 observation masking, six settings, one run per cell: SparseRead has the lowest tokens and wall time in every setting and is the only method whose score never falls below Naive. Token reduction vs Naive: **63.4–86.3%**. The paper states the corresponding wall-time reductions are a **1.8–3.6×** completion-time speedup.
- Flagship **single run** (Figure 8): LooGLE 5Q / DeepSeek-V4-Flash / NanoBot — 369,042 → 50,699 tokens (−86.3%), 17 → 4 requests, 97.8s → 39.4s, score 1.0 / 1.0.
- Claude Opus 5 on sparse-fit scenarios: **59.6–89.0%** tokens, **51.3–78.1%** wall time. Local checkout has no Opus trajectory; quote the paper range only.
- Force-mode boundary (not gated SparseRead): SPARQL / global statistics **+309.2%** tokens; companion rule-coding **+210.5%**.

## Code

https://github.com/Zedong-Liu/SparseReading

## Reproduce (this checkout)

`make demo` is a **replay** of recorded paper cases. It does not call a model.

```bash
make demo
make reproduce CASE=flagship
make reproduce CASE=strong_model
make reproduce CASE=regime_boundary
```

Live smoke (`make demo-live MODEL=...`) needs your API key and is not a paper result.

This checkout cannot rerun the 125-task / 30-cell matrix, Opus 5 traces, or the materials-chemistry / Claw-Eval fixtures. `make reproduce-suite` currently only checks the three recorded cases.

claim_ids: paper-abs-token-max, paper-abs-wall-max, paper-s62-30-of-30-cost, paper-abs-quality-preserve, paper-s63-vs-baselines, paper-s63-token-range, paper-s63-speedup, case-flagship-loogle5q-flash, case-flagship-loogle5q-flash-reduction, paper-s62-opus-sparsefit, paper-fig5-t67, paper-fig5-t59
