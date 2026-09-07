# Researcher outreach

Do not send from this workspace. Replace [Name], [framework], and [Sender] before anyone presses send.

## A. Agent-framework author

Subject: SparseRead adapter for [framework] — paper + no-key replay

Hi [Name],

We used [framework] as one of the three harnesses in SparseRead (arXiv:2608.22237; NanoBot v0.2.0, OpenCode v1.17.14, OpenClaw v2026.6.11). The artifact is a thin adapter around a shared pre-reading runtime — Read Gate + typed Reader Backends + a stateful Evidence State — not a new agent and not an observation compressor.

The shortest look is `make demo` on https://github.com/Zedong-Liu/SparseReading (REPLAY, no API key) and the paper: https://arxiv.org/pdf/2608.22237. We would value a sanity check on whether the adapter surface matches how [framework] now routes file/open events.

We are not claiming a win on every task. Force mode on a SPARQL / global-statistics task increases tokens by +309.2%. Median token reduction also differs by harness (Table 1: 69.0% / 71.8% / 28.7%). Token volume is not dollar cost.

Thanks,
[Sender]

## B. Context-compression researcher

Subject: Pre-read admission vs post-hoc compression

Hi [Name],

Your work on [ACON / observation masking / related] is one of the baselines we compare against in SparseRead (arXiv:2608.22237, Figure 8). We treat the remaining gap as admission control: decide what of an external object should enter context before the broad read is paid. ACON’s auxiliary calls are included in our cost; CT is last-10 masking after the observation is already in the trajectory.

The paper reports up to 92.9% token-volume reduction on a six-model / five-scenario NanoBot matrix, with quality not uniformly improved (26/30 cells, not 30/30). Figure 8 is n=1 per cell. Token volume is not dollar cost.

If you have time, `make demo` at https://github.com/Zedong-Liu/SparseReading is a no-key replay, and the paper is at https://arxiv.org/pdf/2608.22237. We would welcome a critique of the protocol-vs-compressor distinction, especially where a compressor should instead register as a Reader Backend.

Thanks,
[Sender]

claim_ids: paper-tab1-framework-medians, paper-fig5-t67, paper-abs-token-max, paper-s63-vs-baselines, paper-abs-quality-preserve
