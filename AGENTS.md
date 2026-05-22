# AGENTS.md

This file is the operational entry point for coding agents working in this repository.

## Project Status

- Repository root: `/Users/captainliu/sparse-reading`
- Current state: preparation workspace for a future refactor
- Source project location: fill in `remote.md` before starting implementation
- Execution runbook: `runbook.md`

## Agent Operating Rules

1. Read `v3_plan.md`, `remote.md`, `docker.md`, and `runbook.md` before making code changes.
2. Do not change production code until the current phase in `v3_plan.md` has an explicit objective and acceptance criteria.
3. Preserve user changes. Never revert local edits unless the user explicitly asks.
4. Keep changes scoped to the active task.
5. Prefer small, reviewable commits once the real repository is connected.
7. Record environment-specific commands in `runbook.md` instead of relying on chat history, and write phase development outcomes to `v3_dev.md`.
8. When updating SRO benchmark figures or adding new test results to plots, use `skills/sro-results-visualizer/SKILL.md`.


## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
