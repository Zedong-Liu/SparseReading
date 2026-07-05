# SparseRead 安装指南

这份文档描述当前 single-repo 分支的默认源码安装形态。默认场景是：

- 用户本机已经安装 OpenCode 或 OpenClaw CLI；
- SparseRead 从本仓库源码 checkout 后加装到框架；
- OpenCode/OpenClaw 通过本地插件启动 Python bridge；
- Python bridge 复用 `nanobot-sro-v3/` 里的 SparseRead core。

这还不是 PyPI/npm/官方插件市场的一键发行版。当前目标是让开源用户能从源码稳定安装、验证、使用，并且三个平台使用同一套 core 能力。

## 当前生产入口

这是框架和 agent 内部看到的工具形态，不是要求用户手动输入的命令。生产路径从 `sro_preview` 开始：

```text
sro_preview(path) -> L0 默认预览，内含 FileCard，不需要 HintSpec
sro_read(target, mode, hint) -> 有明确目标时再读取定向证据
sro_raw(raw_ref) -> 明确需要原文时的回溯入口
```

`sro_card` 仍会注册，但只用于 benchmark 和旧脚本兼容。新插件和新框架集成都应该把 `sro_preview` 作为内部第一入口。

## 模型可见的 SparseRead 指南在哪里

当前三平台不是完全相同的 skill 文件形态：

- nanobot 内置 skill：`nanobot-sro-v3/nanobot/skills/sparse-reading/SKILL.md`。
- OpenClaw 插件随带 skill：`integrations/openclaw/plugin/skills/sparse-reading/SKILL.md`；pilot 展示路径是 `openclaw_pilot/plugin/skills/sparse-reading/SKILL.md`。
- OpenCode 当前没有独立 `SKILL.md`。它通过插件注册 `sro_preview`、`sro_read` 等工具，并在大文件/截断输出场景给模型 nudge。日常使用时，用户应该在任务里要求 agent 自动使用 SparseRead。

所以，用户文档不应该写成工具调用教程；工具调用顺序是给模型和插件看的。

## 环境要求

- Python 3.11+
- `uv`
- Node.js 22.22.2+，或 Node.js 24.15.0+
- `npm`
- Git
- 已安装的 OpenCode CLI 或 OpenClaw CLI

当前源码安装验证使用过：

- OpenCode `1.17.13`
- OpenClaw `2026.6.11`

OpenClaw 插件声明的 host 版本要求是 `openclaw >= 2026.5.17`。更旧版本只有在保留相同 plugin/tool API 时才可能可用。

## Fresh Machine 安装

从源码开始：

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git
cd SparseReading
```

先跑 release fixture，确认 core 和两个 bridge 都能启动：

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_release_fixtures.py -q
```

完整本地回归：

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading -q
```

## 安装到 OpenCode

假设你已经能在目标 workspace 里运行 `opencode`。

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-workspace /path/to/your/project \
  --policy auto \
  --mode auto \
  --doctor
```

安装脚本会写入：

```text
/path/to/your/project/.opencode/plugins/sparseread.ts
/path/to/your/project/.opencode/sparseread.env
```

启动 OpenCode：

```bash
cd /path/to/your/project
source .opencode/sparseread.env
opencode run "Use SparseRead to inspect the large report and answer the question"
```

如果你的 CLI 名称不是 `opencode`，显式指定：

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-cmd opencode-ai \
  --opencode-workspace /path/to/your/project \
  --doctor
```

OpenCode 插件会暴露：

```text
sro_preview, sro_raw, sro_card, sro_read, sro_trace
```

## 安装到 OpenClaw

假设你已经能运行 `openclaw`。

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --policy auto \
  --mode auto \
  --doctor
```

安装脚本会：

- 构建 `integrations/openclaw/plugin`；
- 执行 `openclaw plugins install --link integrations/openclaw/plugin`；
- 启用 `sparseread-openclaw`；
- 写入 repo-backed SparseRead bridge 配置。

检查插件是否加载：

```bash
openclaw plugins inspect sparseread-openclaw --runtime --json
```

预期工具面：

```text
sro_preview, sro_raw, sro_card, sro_read, sro_decide, sro_trace
```

如果使用命名 profile：

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --openclaw-profile work \
  --doctor

openclaw --profile work plugins inspect sparseread-openclaw --runtime --json
```

OpenClaw 的 provider/model/key 仍由 OpenClaw 自己配置。SparseRead 不安装或管理模型密钥。

## 同时安装两个框架

如果两个 CLI 都已经安装：

```bash
python3 scripts/install_sparseread.py \
  --platform both \
  --opencode-workspace /path/to/your/project \
  --policy auto \
  --mode auto \
  --doctor
```

## Doctor 检查

只检查本机命令和 bridge，不改框架配置：

```bash
python3 scripts/install_sparseread.py --platform opencode --doctor-only
python3 scripts/install_sparseread.py --platform openclaw --doctor-only
```

doctor 会用一个临时 markdown fixture 启动对应 Python bridge，验证 `sro_preview` 能返回 FileCard 和 L0 预览。

## 日常使用建议

用户不需要手动调用 `sro_preview`、`sro_read` 或填写 `HintSpec`。正确用法是在任务里告诉 agent 自动使用 SparseRead，例如：

```text
请自动使用 SparseRead 阅读这个大文件/证据包，只提取完成任务所需的证据；证据足够后直接写结果，不要反复全文读取。
```

适合明确要求 agent 使用 SparseRead 的场景：

- 大文件、长 markdown、PDF、长日志；
- 多文件证据包、审计材料、诊断材料；
- 问题本身只需要文件中的少量证据，而不是完整重算或逐行处理。

通常不需要特别要求 SparseRead 的场景：

- 小文件、脚本、配置改动；
- 全表计算、精确逐行统计、需要运行代码得到答案的任务；
- 你已经知道要改哪几行代码的普通开发任务。

agent 内部会按生产协议工作：先做 L0 preview；如果 preview 不够，再围绕明确问题读取 EvidencePack；证据已经 ready 时直接写交付物；只有明确需要原文时才回溯 raw 内容。用户只需要描述任务和目标，不需要自己组织这些工具调用。

不要把 SparseRead 当成“所有内容都压缩”的代理。它的优势是让 agent 少读无关材料，并在需要时用可回溯的 evidence pack 补证据。

## 快速体验测试

本仓库提供了一个长 markdown fixture：

```text
examples/sparseread_quick_test/incident-report.md
```

OpenCode 安装后，在目标 workspace 里运行：

```bash
source .opencode/sparseread.env
opencode run "请自动使用 SparseRead 阅读 $SPARSEREAD_PROJECT_ROOT/examples/sparseread_quick_test/incident-report.md，只提取必要证据，并回答 ROOT_CAUSE、MITIGATION_OWNER、FINAL_DEADLINE 分别是什么。不要让我手动调用工具。"
```

OpenClaw 或 nanobot 会话中发送同类自然语言请求即可，把路径换成你本机 checkout 下的完整路径。预期答案应包含：

```text
ROOT_CAUSE: cache invalidation used customer_id instead of tenant_id.
MITIGATION_OWNER: Mira Chen, Data Platform on-call.
FINAL_DEADLINE: 2026-07-18 09:30 UTC.
```

## 版本发布前固定测试

每次版本更新都至少跑：

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_release_fixtures.py -q
```

这 6 个 fixture 覆盖：

1. 长 markdown key-value 字段；
2. 日志 level preview 和 raw selector；
3. CSV schema/sample/signals；
4. JSON schema/sample/signals；
5. YAML schema/sample/signals；
6. XML root/schema/sample preview。

每个 fixture 都会同时经过 `OpenCodeBridge` 和 `OpenClawBridge`，所以它检查的不只是 reader，也包括两个框架 bridge 的 core 功能一致性。

## Benchmark 兼容

历史 benchmark 和旧脚本可能还统计 `sro_card -> sro_read`。这条路径保留，并可通过 `SPARSEREAD_MODE=bench_protocol` 或 `SparseReadConfig(mode="bench_protocol")` 使用。

生产安装应使用 `mode=auto`，并从 `sro_preview` 开始。
