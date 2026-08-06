# Changelog

## v0.1.0 — 2026-08-06

四框架发布基线：

- 框架无关 core（`sparseread-core` 0.1.0）：production BenefitGate、episode
  controller、denoise、bridge protocol 1.0。
- 四个 adapter：`sparseread-nanobot`、`sparseread-opencode`、
  `sparseread-openclaw`、`sparseread-claude`（MCP + session hooks）。
- 安装器：`--platform opencode|openclaw|claude`；NanoBot 走 Python 依赖。
- 安装文档覆盖四框架，并记录框架行为差异矩阵。
- 发布收口：MIT LICENSE、JS 插件 license 字段、五个 Python 包 uv.lock、
  GitHub Actions CI、`v0.1.0` tag。

说明：release 提交中包含 benchmark 工具与聚合结果（
`local_agent_comp/run_claude_sro_bench.py`、`sro_anthropic_proxy.py`、
`scripts/benchmark_claude_bridge.py`、`SRO_test/.../claude_final_aggregate_*.md|json`），
这些是仓库内开发/验证工具，不进入任何发布包。
