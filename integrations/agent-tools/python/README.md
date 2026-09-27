# SparseRead agent tools

Framework-layer MCP (Codex) and JSONL (Pi) transports around `sparseread-core`.
No readers, gate thresholds, or algorithms are copied here. Install through
`scripts/install_sparseread.py --platform codex|pi --workspace /your/project`.

Launch MCP: `python -m sparseread_agent_tools.mcp --host codex --workspace .`.
Launch JSONL: `python -m sparseread_agent_tools.bridge --host pi --workspace .`.
Protocol version is the unchanged core protocol `1.0`.
