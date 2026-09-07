# LinkedIn

A production agent still opens the whole paper, repo, or table before anyone has named the evidence the next step needs. That over-reading burns token volume and wall time, and it can dilute the signal the decision actually needed.

Long context, KV-cache tricks, and post-hoc compressors all act after the object is already in the trajectory. SparseRead moves the control point earlier. It is a training-free, model-transparent pre-reading layer: a regime-aware Read Gate (`force` / `advisory` / `native`), replaceable Reader Backends, and a stateful Evidence State with explicit refine / verify / stop / fallback. No model finetuning. No auxiliary compressor.

On the paper’s NanoBot matrix — six models, five scenarios — SparseRead reduces token volume by up to 92.9% and wall time by up to 89.0% vs Naive. All 30 cells reduce both costs. Quality is preserved or improved on 26 of 30 cells, not 30/30. Token volume is not dollar cost.

The same protocol is a harness-level adapter, not a new agent. Across three frameworks and five models (Table 1; Claude Opus 5 is not in this table), median token reductions are 69.0% (NanoBot), 71.8% (OpenCode), and 28.7% (OpenClaw). Portability is real; the saving is not uniform.

This is not only a weak-model patch. Claude Opus 5, the strongest model evaluated, still saves 59.6–89.0% of tokens and 51.3–78.1% of wall time on sparse-fit scenarios.

It does not replace native tools on every request. Force mode on a low-sparsity SPARQL / global-statistics task increases tokens by +309.2%. Knowing when to step aside is part of the design.

Paper: https://arxiv.org/pdf/2608.22237
Code / no-key replay: https://github.com/Zedong-Liu/SparseReading (`make demo`)

A longer-term question — not a result we measured — is whether future agent training should treat “what to read, how much, and when to stop” as a first-class skill next to tool use.

claim_ids: paper-abs-token-max, paper-abs-wall-max, paper-s62-30-of-30-cost, paper-abs-quality-preserve, paper-tab1-framework-medians, paper-s62-opus-sparsefit, paper-fig5-t67
