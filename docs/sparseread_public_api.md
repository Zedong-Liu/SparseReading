# SparseRead Public API And Integration Plan

## Positioning

`SparseRead` should be the public project and package name. `SRO` remains the internal protocol name: Sparse Reading Orchestrator, the implementation layer behind SparseRead.

The public claim should be practical:

> SparseRead adds model-transparent sparse reading to existing agents. It wraps an agent, injects a compact reading protocol and benefit gate, and lets the agent read large or multi-file inputs through typed evidence packs instead of broad context dumps.

Do not market it only as paper code. The GitHub project should feel like a usable developer tool:

```bash
pip install sparseread
```

```python
from sparseread import SparseReadAgentWrapper

agent = SparseReadAgentWrapper(agent, mode="auto", workspace=".")
agent.run("Answer questions from this 200-page PDF")
```

The current implementation adds this facade under `nanobot-sro-v3/sparseread/`.
The important design decision is that the wrapper is not the whole product. It
is only the shortest entry point. The stable public surface should be:

- `SparseRead`: runtime/toolkit object that owns the orchestrator and exposes tools.
- `SparseReadAgentWrapper` / `wrap`: three-line convenience facade.
- `sparseread.adapters.*`: framework-specific installers that wire tools, file guards, command policy, and traces.

This is better than a wrapper-only API because sparse reading is not just prompt
compression. It needs concrete tool registration and guard placement. A wrapper
that only forwards `agent.run()` cannot reliably intercept broad file reads or
protect completed EvidencePacks from redundant rereads.

## Recommended User Paths

### 1. Quickstart For Nanobot-Like Agents

For an agent with a nanobot-style tool registry:

```python
from sparseread import wrap

agent = wrap(agent, mode="auto", workspace=".")
agent.run("Audit this folder and write the report.")
```

`wrap()` autodetects nanobot-style registries and installs:

- `sro_card`
- `sro_read`
- read/list/grep SRO guards
- conservative command policy

### 2. Explicit Nanobot Install

This should be the documented path for projects that already construct their
agent and tools explicitly:

```python
from sparseread.adapters.nanobot import install

sparseread = install(agent, mode="auto", workspace=".")
agent.run("Find the root cause across these logs and configs.")
```

This is clearer in production code because it makes SparseRead installation an
explicit setup step and returns the runtime for inspection.

### 3. Generic Tool-Capable Frameworks

For frameworks that accept Python tool objects or OpenAI-style tool schemas:

```python
from sparseread import SparseRead

sr = SparseRead(mode="auto", workspace=".")
agent = create_agent(
    tools=[*existing_tools, *sr.tools()],
)
agent.run("Answer questions from ./report.pdf")
```

If the framework needs schemas:

```python
schemas = sr.tool_schemas()
```

For generic frameworks, this only exposes sparse-reading tools. It does not
automatically patch that framework's native file tools. To make SparseRead
fully reliable, the project needs a framework adapter that wires native file
reads, directory listing, grep/search, and shell policy to the same runtime.

## Design Goals

1. Three-line adoption for existing agent objects.
2. No requirement that users understand SRO internals.
3. Keep wrappers adapter-based, not framework-locked.
4. Expose simple modes first: `auto`, `force`, `native`, `advisory`.
5. Make Benefit Gate visible in logs so users trust why SparseRead intervened or passed through.

## Minimal Architecture

```text
sparseread/
  __init__.py
  wrapper.py
  config.py
  adapters/
    nanobot.py
    langchain.py
    openai_agents.py
    autogen.py
  protocol/
    models.py          # FileCard, HintSpec, EvidencePack
    orchestrator.py    # internal SRO bridge
  readers/
    text.py
    structured.py
    collection.py
  gates/
    benefit.py
```

The public wrapper should not depend on nanobot concepts. Nanobot should become one adapter:

```python
from sparseread.adapters.nanobot import NanobotAdapter
```

## Core API Sketch

```python
from sparseread import SparseReadAgentWrapper, SparseReadConfig

config = SparseReadConfig(
    mode="auto",
    workspace=".",
    readers=["text", "structured", "collection"],
    benefit_gate=True,
)

agent = SparseReadAgentWrapper(agent, config=config)
result = agent.run("Find the material risks in these reports.")
```

Convenience alias:

```python
from sparseread import wrap

agent = wrap(agent, mode="auto")
```

Inspection API for trust/debugging:

```python
trace = agent.sparseread.last_trace
print(trace.decision.mode, trace.decision.reason)
print(trace.evidence_packs)
```

## Adapter Contract

SparseRead needs a small adapter surface:

```python
class AgentAdapter:
    def matches(self, agent) -> bool: ...
    def install(self, agent, sparseread) -> list[str]: ...
```

An adapter should do more than add two tools. For a full integration it should:

- register `sro_card` and `sro_read`;
- route broad native `read_file` / `list_dir` / `grep` through Benefit Gate;
- add command-security policy for broad raw dumps and repeated failed commands;
- expose traces so users can see whether SparseRead intervened or passed through.

If an agent cannot accept tools directly, SparseRead can fall back to
`mode="advisory"`, but this should be documented as less reliable than tool
injection.

## Current Implementation Status

Implemented in the nanobot SRO branch:

- `sparseread.SparseRead`
- `sparseread.SparseReadAgentWrapper`
- `sparseread.SparseReadConfig`
- `sparseread.wrap`
- `sparseread.adapters.nanobot.install`
- package inclusion in the local hatch build
- public API tests for runtime tools, wrapper forwarding, Benefit Gate override,
  and nanobot adapter installation

Validation:

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/ -q
```

## First Release Scope

Ship only what can be supported cleanly:

- `SparseReadAgentWrapper`
- `wrap(...)`
- local filesystem targets
- text/PDF reader
- structured CSV/XLSX reader
- collection reader
- Benefit Gate
- trace/log export
- nanobot adapter

Do not ship benchmark harnesses as the primary user path. Keep benchmarks under `experiments/` or `benchmarks/`.

## README First Screen

The README should start with working usage, not paper abstract:

```python
from sparseread import wrap

agent = wrap(agent, mode="auto")
agent.run("Summarize the evidence across ./reports and write risks.md")
```

Then show:

- Why sparse reading helps.
- When SparseRead stays out of the way.
- Supported frameworks/adapters.
- A small local example with a PDF or folder.
- Link to the paper/report.

## Paper/Project Naming

Use:

- Project/package: `SparseRead`
- Internal protocol: `SRO`
- Paper phrase: “SparseRead: Model-Transparent Sparse Reading for Tool-Using Agents”

This lets the paper keep protocol precision while the GitHub project remains memorable and installable.

## Engineering Next Steps

1. Keep the current facade as the nanobot integration prototype.
2. Extract `nanobot.sparse_reading` into a framework-neutral `sparseread` package.
3. Publish a local editable install path first:

```bash
pip install -e .
```

4. Split final PyPI metadata so users can actually run:

```bash
pip install sparseread
```

5. Add one executable nanobot quickstart and one framework-neutral quickstart.
6. Add adapters for LangChain/OpenAI Agents SDK only after the adapter contract is stable.
7. Only then prepare PyPI release, README polish, examples, and GitHub project presentation.
