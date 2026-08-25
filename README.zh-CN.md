# SparseRead

<div align="center">

**少读一点，解决更多。**

SparseRead 是一个免训练、面向工具调用型 Agent 的阅读层。在大范围读取之前，
先控制哪些证据真正进入模型上下文，同时保留来源锚点、细化、验证和原生回退能力。

[![CI](https://github.com/Zedong-Liu/SparseReading/actions/workflows/ci.yml/badge.svg)](https://github.com/Zedong-Liu/SparseReading/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[论文](https://arxiv.org/abs/2608.22237) · [English](README.md)

</div>

Agent 很擅长推理，但默认的阅读动作往往仍然是：

```text
全部读入 -> 全部放进上下文 -> 再开始推理
```

对于长报告、PDF、workspace、日志、表格和多文件审计，这种方式成本很高。
SparseRead 在 Agent 原生工具前增加一个小型控制面：

```text
artifact -> Read Gate -> Reader Backend -> EvidencePack -> refine / verify / stop
```

Agent 仍然决定自己需要什么；SparseRead 让这次读取有边界、有来源、可继续细化，
也能在原生访问更合适时回退。

## 结果概览

当前论文评测覆盖 125 个任务、5 类工作场景和 6 个主流模型：Claude Opus 5、
Qwen3.6-Plus、DeepSeek-V4-Flash、DeepSeek-V4-Pro、GLM-5.1、Kimi-K2.5。

| 指标 | 论文报告结果 |
|---|---:|
| 最高 token 降幅 | **92.9%** |
| 最高端到端时间降幅 | **89.0%** |
| token 和时间同时下降的模型–场景单元 | **30 / 30** |
| 得分保持或提升的单元 | **26 / 30** |
| 稀疏阅读场景中得分保持或提升的单元 | **22 / 24** |

收益并不依赖单一模型：六个模型都出现在完整评测矩阵中，并在论文报告中获得了
收益。指标定义、基线和完整结果请见[论文](https://arxiv.org/abs/2608.22237)。

### 跨框架支持

论文的端到端可迁移性表格评测了 NanoBot、OpenCode、OpenClaw 三个框架；当前单仓库
发布基线新增了 Claude Code 第四个 adapter：

| 框架 | Adapter | token 中位降幅 | 时间中位降幅 | 论文状态 |
|---|---|---:|---:|---|
| [NanoBot](integrations/nanobot/) | `sparseread-nanobot` | **69.0%** | **64.4%** | 已评测 |
| [OpenCode](integrations/opencode/) | `sparseread-opencode` | **71.8%** | **64.9%** | 已评测 |
| [OpenClaw](integrations/openclaw/) | `sparseread-openclaw` | **28.7%** | **28.2%** | 已评测 |
| [Claude Code](integrations/claude/) | `sparseread-claude` | — | — | 本发布已支持 |

Claude Code 使用 MCP 加 `PreToolUse`/`PostToolUse` session hooks，不使用 npm 插件。
当前本地集成验证报告见
[`benchmarks/qwenclawbench/claude_final_aggregate_20260805.md`](benchmarks/qwenclawbench/claude_final_aggregate_20260805.md)。
该报告尚未纳入论文中的三框架表格。

## 安装

当前发布基线是源码安装版。安装器会为所选框架构建受管 runtime，安装后的集成不会在
运行时导入本 checkout 的源码。

环境要求：Python 3.11+、[uv](https://docs.astral.sh/uv/)、OpenCode/OpenClaw 所需的
Node.js 22+，以及目标 Agent CLI。

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git
cd SparseReading

# 先验证 core、adapter、bridge protocol 和 release fixture
PYTHONPATH="packages/sparseread-core/src:integrations/nanobot/python/src:integrations/opencode/python/src:integrations/openclaw/python/src:integrations/claude/python/src" \
  uv run --with pytest --with pytest-asyncio pytest tests/test_release_fixtures.py -q
```

选择一个框架：

```bash
# OpenCode：安装到已有 workspace
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-workspace /path/to/your/project \
  --doctor

# OpenClaw：安装到当前 profile
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --doctor

# Claude Code：向 workspace 写入 MCP 和 session hooks
python3 scripts/install_sparseread.py \
  --platform claude \
  --claude-workspace /path/to/your/project \
  --doctor
```

NanoBot 作为 Python 依赖安装 `sparseread-core` 和 `sparseread-nanobot`，见
[NanoBot adapter 说明](integrations/nanobot/python/README.md)。完整平台矩阵见
[`docs/sparseread_installation.md`](docs/sparseread_installation.md)；英文简版见
[`docs/installation.md`](docs/installation.md)。

安装完成后，用户不需要手动调用 `sro_preview` 或填写 `HintSpec`。只要在任务中说明
使用 SparseRead，例如：

```text
请使用 SparseRead 阅读这个大报告，只提取回答问题所需的证据；证据足够后停止读取。
```

### 快速体验

仓库内置了一个长 Markdown fixture：

```bash
opencode run "Use SparseRead to inspect tests/fixtures/quick_test/incident-report.md and report ROOT_CAUSE, MITIGATION_OWNER, and FINAL_DEADLINE."
```

安装对应 adapter 后，OpenClaw、Claude Code、NanoBot 会话也可以使用同类请求。

## 工作方式

- **Read Gate**：根据 artifact 形态和任务成本选择 `auto`、`native` 或 `advisory`。
  小文件、全表计算等低收益任务保持原生工具。
- **Reader Backends**：为文本/PDF、结构化数据和多文件集合提供有类型、有限制的读取。
- **EvidencePack**：返回带来源锚点的紧凑证据、未解决需求和下一步建议。
- **有状态协议**：支持 preview、定向读取、细化、验证、显式原文回退和停止。

生产入口由框架暴露，用户通常不需要直接调用：

```text
sro_preview(path) -> 有限预览 + FileCard
sro_read(target, mode, hint) -> EvidencePack
sro_raw(raw_ref) -> 显式原文回退
```

## 仓库结构

```text
packages/sparseread-core/       框架无关 core 和测试
integrations/<framework>/       NanoBot、OpenCode、OpenClaw、Claude Code adapter
scripts/install_sparseread.py   源码安装器和 doctor
tests/                          发布、bridge、gate、安装器测试
benchmarks/                     可复现实验 runner 和精选 fixture
docs/                           安装、架构和设计说明
```

Core 与 adapter 分离：adapter 只负责宿主框架的 bridge、生命周期和安装面，不复制阅读
协议。

## 开发与测试

单独运行 core 测试：

```bash
uv run --project packages/sparseread-core --with pytest --with pytest-asyncio \
  pytest packages/sparseread-core/tests -q
```

运行完整发布测试：

```bash
PYTHONPATH="packages/sparseread-core/src:integrations/nanobot/python/src:integrations/opencode/python/src:integrations/openclaw/python/src:integrations/claude/python/src" \
  uv run --with pytest --with pytest-asyncio pytest -q
```

更多架构约束见[发布架构](docs/release_architecture.md)。benchmark 和历史结果用于复现，
不会被任何发布包导入。

## 当前边界

- 当前基线是 `v0.1.0`，支持从源码安装。
- PyPI、npm 和各框架官方 marketplace 的一键发布尚未接通；目前支持路径是源码安装器。
- Claude Code 已通过 MCP 和 session hooks 支持；Windows 上仍需按主机 CLI 和权限环境
  单独验证 MCP 通路。
- SparseRead 是选择性基础设施：小文件、精确全表计算和其他低稀疏任务应继续使用原生访问。

## 贡献

提交 PR 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。欢迎 bug 报告和聚焦于框架适配的
反馈。

## 引用

```bibtex
@article{liu2026readless,
  title   = {Read Less, Solve More: Token-Efficient Sparse Reading for AI Agents},
  author  = {Liu, Zedong and Wu, Jiaan and Ma, Xinyang and Xu, Le and Wang, Kai and Hu, Yuanchao and Tao, Dingwen and Tan, Guangming},
  journal = {arXiv preprint arXiv:2608.22237},
  year    = {2026}
}
```

## 许可证

SparseRead 使用 [MIT License](LICENSE) 发布。
