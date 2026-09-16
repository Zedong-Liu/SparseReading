# Changelog

## v0.1.1 — 2026-09-16

安装与发布收口：

- 安装器在改写 workspace/profile 前统一检查宿主 CLI 和 Python 3.11+，并能在默认
  Python 过旧时发现 uv 管理的兼容解释器。
- 测试环境集中到根目录 `pyproject.toml`，release fixture 和完整回归不再依赖隐含的
  `pyyaml`、`mcp` 等手工安装步骤。
- 配置文件改为原子写入；已有非法 JSON 会明确中止，不再静默覆盖用户配置。
- OpenClaw 插件安装与启用改为失败即停，并清理临时 npm tarball；runtime 可通过
  `--reader-extras none` 跳过 PDF/XLSX 可选依赖。
- OpenCode 插件锁定已修复安全版本的 `toml` 传递依赖，生产依赖审计不再报告高危项。
- core、四个 Python adapter 和两个 JavaScript 插件的版本统一为 `0.1.1`，新增 tag
  触发的 GitHub Release 构建流程。

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
`benchmarks/run_claude_sro_bench.py`、`sro_anthropic_proxy.py`、
`scripts/benchmark_claude_bridge.py`、`benchmarks/qwenclawbench/claude_final_aggregate_*.md|json`），
这些是仓库内开发/验证工具，不进入任何发布包。
