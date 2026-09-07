# Hacker News

Title: SparseRead: admit evidence before the agent dumps the file

A tool-using agent still opens the whole file, PDF, or repo before it has named the evidence the next step needs. Long context, KV-cache tricks, and post-hoc compressors all act after that read is already paid.

SparseRead is a training-free, model-transparent pre-reading layer. It sits in front of native file / PDF / repo access and turns a broad object read into bounded, iterative evidence acquisition:

Object Preview → Read Intent → Read Gate (`force` / `advisory` / `native`) → agent-selected `{scout, focus, collect, refine, verify}` → Evidence State → stop, refine, or fall back to native tools.

It is not another observation compressor. ACON (Kang et al. 2026) is a model-based zero-shot compressor: the broad read is already paid, then an auxiliary model shrinks what is in the trajectory; those auxiliary calls are included in cost. Complexity Trap observation masking (Lindenbauer et al. 2025) is training-free last-10 masking after observations have entered context. SparseRead tries not to admit that read.

Paper (arXiv:2608.22237, *Read Less, Solve More*): six models, five scenarios, 125 general tasks plus a materials-chemistry scenario, three harnesses (NanoBot v0.2.0, OpenCode v1.17.14, OpenClaw v2026.6.11). Across the NanoBot matrix, token volume drops by up to 92.9% and wall time by up to 89.0% vs Naive. All 30 model–scenario cells reduce both costs. Quality is preserved or improved on 26/30 cells (four cells sit between −0.10 and −0.02). Token volume is not dollar cost; we did not publish a $/token serving study.

Versus ACON and Complexity Trap on the six Figure 8 settings (DeepSeek-V4-Flash / DeepSeek-V4-Pro / Qwen3.6-Plus × long-context reading and multi-file audit), SparseRead has the lowest token volume and wall time in every setting and is the only method whose score never falls below Naive. Token reduction vs Naive on those six settings is 63.4–86.3%. The paper states the corresponding wall-time reductions are a 1.8–3.6× completion-time speedup. Figure 8 is one run per cell.

Flagship, single run, Figure 8: LooGLE 5Q / DeepSeek-V4-Flash — five local facts in a ~100k-character history chapter. Naive 369,042 tokens / 17 requests / 97.8s vs SparseRead 50,699 / 4 / 39.4s; score 1.0 for both (−86.3% tokens). Do not treat that cell as an average.

Force-mode boundary (not gated SparseRead): forced sparse reading on a SPARQL / global-statistics task increases tokens by +309.2%. Companion rule-coding is +210.5%. The Read Gate exists because Sparse Reading is regime-dependent.

What this checkout can reproduce, with no API key:

- `make demo` — REPLAY of the recorded flagship case
- `make reproduce CASE=flagship|strong_model|regime_boundary`

What it cannot: the 125-task / 30-cell Figure 7 source table; Claude Opus 5 or Kimi-K2.5 traces (Opus range is paper-only); Claw-Eval and materials-chemistry fixtures; a live rerun of the public matrix.

Code: https://github.com/Zedong-Liu/SparseReading
Paper: https://arxiv.org/pdf/2608.22237

Open questions we would rather hear from this community than paper over: how a learned Read Gate would compare to the deterministic `force` / `advisory` / `native` controller; whether a specialized compressor should register as a Reader Backend instead of sitting after the broad read; how prefix-cache reuse interacts with serving stacks we did not measure as dollar cost.

claim_ids: paper-abs-token-max, paper-abs-wall-max, paper-s62-30-of-30-cost, paper-abs-quality-preserve, paper-s63-vs-baselines, paper-s63-token-range, paper-s63-speedup, case-flagship-loogle5q-flash, case-flagship-loogle5q-flash-reduction, paper-fig5-t67, paper-fig5-t59
