# Changelog

## v0.1.3 — 2026-09-28

- 修复 Codex/Pi 在大型工作区将长文件切换为 `full_fidelity` 原生读取时仍扫描父目录、
  使工具调用超时的问题。明确的原生读取否决现在直接跳过父集合检查；reader、BenefitGate
  阈值和证据协议不变。
- 在真实 Codex 项目中验证安装、六个 MCP 工具、定向证据、原文回取及原生回退；用户在
  Codex App 新会话中确认 `sro_preview` 和 `sro_read` 可用。新增 Codex/Pi 回归测试。
- 本地安装产物加入 Git 忽略规则。`v0.1.2` 标签和附件保持不变。

## v0.1.2 — 2026-09-28

- 新增 Codex 项目插件（MCP 六工具、Skill、显式信任的保守 Hook）和 Pi 源码扩展，
  共用已有 core；核心算法、阈值和 readers 不变，仅同步版本元数据。
- 新增独立 wheel runtime、项目级安装与 doctor、缓存刷新、超时/取消/进程清理、
  原生读取回退；不自动授予项目或 Hook 信任。
- 本地真实运行手册、开发记录、移植总结、故障日志和历史 JSONL 回放，覆盖
  多轮取证、精确原文回取、任务切换、会话重启和只转向一次。
- 修复旧 raw_ref/核心协议错误被当作成功工具结果；明确 Unicode 字符范围契约。
- Pi 安装信任拒绝给出可执行指引；新增用户显式选择的 `--pi-approve`，只作用于
  本次项目安装命令，不自动绕过信任。
- Release 增加 ZIP/tar.gz 源码安装包和所有附件的 SHA256SUMS，发布前执行测试。
  原始本地记录不进入仓库/安装包；回放数据不代表真实模型质量/token benchmark。

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
