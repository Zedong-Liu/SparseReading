# SparseRead 安装指南

这份文档描述当前 single-repo 分支的默认源码安装形态。SparseRead 现在是
四框架共享一个 core（`packages/sparseread-core`），每个框架的桥接面不同：

- NanoBot：作为普通 Python 依赖安装（`sparseread-core` + `sparseread-nanobot`），
  由 NanoBot 框架内部加载 adapter；
- OpenCode：通过 TS 插件 + Python bridge 安装到 workspace；
- OpenClaw：通过 npm 插件包 + Python bridge 安装到 OpenClaw profile；
- Claude Code：通过 MCP stdio server + PreToolUse/PostToolUse session hook
  安装（Claude Code 没有 npm 插件系统，`.mcp.json` 和
  `.claude/settings.local.json` 就是它的“插件形态”）。

这还不是 PyPI/npm/官方插件市场的一键发行版。当前目标是让开源用户能从源码
稳定安装、验证、使用，并且四个框架使用同一套 core 能力。

当前源码安装目标支持 macOS、Linux、Windows。Windows 默认推荐路径是 **PowerShell 原生安装**，不推荐把日常产品安装建立在 WSL shell wrapper 之上。仓库里的 PinchBench/QwenClawBench benchmark runtime 仍包含 POSIX `/tmp`、shell wrapper 等假设，不作为 Windows 日常安装验证的一部分；Windows 用户优先使用本指南中的 release fixture、doctor 和快速体验测试。

## 当前生产入口

这是框架和 agent 内部看到的工具形态，不是要求用户手动输入的命令。生产路径从 `sro_preview` 开始：

```text
sro_preview(path) -> L0 默认预览，内含 FileCard，不需要 HintSpec
sro_read(target, mode, hint) -> 有明确目标时再读取定向证据
sro_raw(raw_ref) -> 明确需要原文时的回溯入口
```

`sro_card` 仍会注册，但只用于 benchmark 和旧脚本兼容。新插件和新框架集成都应该把 `sro_preview` 作为内部第一入口。

## 模型可见的 SparseRead 指南在哪里

当前四框架不是完全相同的 skill 文件形态：

- nanobot 内置 skill：`nanobot-sro-v3/nanobot/skills/sparse-reading/SKILL.md`。
- OpenClaw 插件随带 skill：`integrations/openclaw/plugin/skills/sparse-reading/SKILL.md`。
- OpenCode 当前没有独立 `SKILL.md`。它通过插件注册 `sro_preview`、`sro_read` 等工具，并在大文件/截断输出场景给模型 nudge。日常使用时，用户应该在任务里要求 agent 自动使用 SparseRead。
- Claude Code 使用安装器写入 workspace 的 `CLAUDE.md`（模板位于
  `integrations/claude/CLAUDE.md`），配合 MCP 工具描述一起给模型提供使用协议。

所以，用户文档不应该写成工具调用教程；工具调用顺序是给模型和插件看的。

## 环境要求

- Python 3.11+
- `uv`
- Node.js 22.22.2+，或 Node.js 24.15.0+
- `npm`
- Git
- 已安装的 OpenCode CLI、OpenClaw CLI 或 Claude Code CLI（按目标框架选择）

Windows 上如果 `npm`、`openclaw` 等入口实际是 `.cmd/.exe/.bat`，安装脚本会自动解析到对应入口，不需要手动修改命令名。

当前源码安装验证使用过：

- OpenCode `1.17.14`
- OpenClaw `2026.6.11`
- Claude Code `2.1.221`

OpenClaw 插件声明的 host 版本要求是 `openclaw >= 2026.5.17`。更旧版本只有在保留相同 plugin/tool API 时才可能可用。

用户安装时只需要选择一个 SparseRead 模式：

- `auto`：默认模式。SparseRead 会在高收益任务上自动接管大文件、PDF、日志和审计证据包的 broad read/search/dump；小文件、脚本、配置、全表计算等低收益任务保持原生工具。
- `advisory`：只注册 SparseRead 工具和提示，不拦截原生读取，依靠模型自然选择是否使用 SparseRead。

不传 `--sparseread-mode` 时就是 `auto`。OpenClaw `2026.6.11` 的 `auto` 会自动配置必要的 lifecycle hook 权限；如果 `doctor` 报告 hook 未加载，优先检查 OpenClaw 插件权限或 profile 配置。只有明确想关闭拦截时，才使用 `--sparseread-mode advisory`。

## 支持矩阵

| 场景 | macOS / Linux | Windows PowerShell |
|---|---|---|
| OpenCode 源码安装 | ✅ 支持 | ✅ 支持 |
| OpenClaw 源码安装 | ✅ 支持 | ✅ 支持 |
| Claude Code 源码安装 | ✅ 支持 | ⚠️ MCP 连接需单独验证（见下文） |
| NanoBot 源码/依赖安装 | ✅ 支持 | ✅ 支持 |
| quick test / doctor | ✅ 支持 | ✅ 支持 |
| benchmark shell runtime | ✅ 可用 | ⚠️ 不作为默认安装路径 |

## Fresh Machine 安装

从源码开始：

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git
cd SparseReading
```

先跑 release fixture，确认 core 和两个 bridge 都能启动：

```bash
uv run --project nanobot-sro-v3 --extra dev --with pytest --with pytest-asyncio \
  pytest tests/test_release_fixtures.py -q
```

完整本地回归：

```bash
uv run --project nanobot-sro-v3 --extra dev --with pytest --with pytest-asyncio \
  pytest -q
```

Windows PowerShell 可直接使用：

```powershell
uv run --project nanobot-sro-v3 --extra dev --with pytest --with pytest-asyncio pytest tests/test_release_fixtures.py -q
```

## 安装到 OpenCode

假设你已经能在目标 workspace 里运行 `opencode`。

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-workspace /path/to/your/project \
  --doctor
```

安装脚本会写入：

```text
/path/to/your/project/.opencode/plugins/sparseread.js
/path/to/your/project/.opencode/sparseread.json
/path/to/your/project/.sparseread/runtime/opencode/
```

启动 OpenCode：

```bash
cd /path/to/your/project
opencode run "Use SparseRead to inspect the large report and answer the question"
```

Windows PowerShell：

```powershell
py scripts/install_sparseread.py --platform opencode --opencode-workspace D:\path\to\your\project --doctor
Set-Location D:\path\to\your\project
opencode run "请自动使用 SparseRead 阅读长报告并回答问题"
```

如果 `opencode` 不在 PATH，显式指定可执行文件路径：

```bash
python3 scripts/install_sparseread.py \
  --platform opencode \
  --opencode-cmd /absolute/path/to/opencode \
  --opencode-workspace /path/to/your/project \
  --doctor
```

OpenCode 插件会暴露：

```text
sro_preview, sro_raw, sro_card, sro_read, sro_trace
```

注意：OpenCode 对 workspace 外路径的默认权限更严格。第一次验证安装时，推荐把测试文件放在当前 workspace 内，或者直接把本仓库根目录作为 workspace 来运行 quick test，而不是从另一个 workspace 读取本仓库外的绝对路径文件。

## 安装到 OpenClaw

假设你已经能运行 `openclaw`。

```bash
python3 scripts/install_sparseread.py \
  --platform openclaw \
  --doctor
```

安装脚本会：

- 构建 `integrations/openclaw/plugin`；
- 将插件打成 npm tarball 后执行普通 `openclaw plugins install`（不是 source link）；
- 启用 `sparseread-openclaw`；
- 在 OpenClaw profile 下创建 wheel-only 的受管 Python runtime，并写入 bridge 配置。

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

第一次在新机器上使用 OpenClaw 前，先确保你的目标 profile 本身已经能正常启动普通 agent 会话；SparseRead 只负责插件和 bridge，不替你修 OpenClaw 自己的 provider/model 配置。

Windows PowerShell：

```powershell
py scripts/install_sparseread.py --platform openclaw --doctor
```

## 安装到 NanoBot

NanoBot 不需要 installer。`sparseread-core` 与 `sparseread-nanobot` 是普通
Python 依赖，`nanobot-sro-v3/` 里的 `nanobot/sparse_reading` 是向下兼容的
转发层，实际实现来自共享 core。

源码形态（monorepo 内开发/测试）：

```bash
uv pip install --python nanobot-sro-v3/.venv/bin/python \
  -e packages/sparseread-core \
  -e integrations/nanobot/python
```

Windows PowerShell：

```powershell
uv pip install --python nanobot-sro-v3\.venv\Scripts\python.exe `
  -e packages\sparseread-core `
  -e integrations\nanobot\python
```

发布形态（PyPI 就绪后）：

```bash
pip install "sparseread-core>=0.1,<0.2" "sparseread-nanobot>=0.1,<0.2"
```

NanoBot 的 agent loop 会在文件访问路径上自动接入 SRO handoff；用户不需要
手动配置 hook 或 MCP。模型可见指南是内置 skill：
`nanobot-sro-v3/nanobot/skills/sparse-reading/SKILL.md`。

## 安装到 Claude Code

假设你已经能运行 `claude`：

```bash
python3 scripts/install_sparseread.py \
  --platform claude \
  --claude-workspace /path/to/your/project \
  --doctor
```

Windows PowerShell：

```powershell
py scripts/install_sparseread.py --platform claude --claude-workspace D:\path\to\project --doctor
```

安装脚本会：

- 构建 `sparseread-core` + `sparseread-claude` wheel，创建受管 Python runtime
  `~/.sparseread/claude/`；
- 合并写入 `/path/to/your/project/.mcp.json`（MCP stdio server，
  暴露 `sro_preview/sro_read/sro_card/sro_raw/sro_decide/sro_trace/
  sro_preflight/sro_usage` 8 个工具）；
- 合并写入 `/path/to/your/project/.claude/settings.local.json`
  （PreToolUse/PostToolUse session hook，拦截大文件 Read/Bash 并注入 SRO
  上下文，大输出后追加 nudge）；
- 如果 workspace 还没有 `CLAUDE.md`，写入 SRO 使用协议模板。

安装后重启 Claude Code（或新开会话）。验证：

```bash
cd /path/to/your/project
claude "请自动使用 SparseRead 阅读长报告并回答问题"
```

模型路由：正常使用自己的 Anthropic 账号即可。若要通过第三方 Anthropic
兼容端点（如 Paratera 的 DeepSeek 模型）跑基准，可设置
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`；仓库提供
`benchmarks/sro_anthropic_proxy.py` 解决第三方网关缺少
`GET /v1/models/{id}` 校验端点的问题，但这不是日常安装的一部分。

Windows 注意：同事在 Windows 11 上曾遇到 MCP stdio/SSE 连接问题；本仓库
未在 Windows 上复验，发布前需要在目标机器上单独验证 `.mcp.json` 连接。

## 同时安装多个框架

如果两个 CLI 都已经安装：

```bash
python3 scripts/install_sparseread.py \
  --platform both \
  --opencode-workspace /path/to/your/project \
  --doctor
```

如需关闭拦截、只让模型自然选择 SparseRead：

```bash
python3 scripts/install_sparseread.py \
  --platform both \
  --opencode-workspace /path/to/your/project \
  --sparseread-mode advisory \
  --doctor
```

`--platform both` 目前等价于 OpenCode + OpenClaw。Claude Code 需要单独再执行
一次 `--platform claude`；NanoBot 走 Python 依赖安装，与 CLI 安装互不影响。

## 框架行为差异（有意设计）

四个框架共享同一个 core gate（`force_sro/native/advisory` + episode hint），
但桥接面能力不同，因此存在以下有意差异，不是缺陷：

| 框架 | enforce 下限 | 决策码白名单 | allow_bounded_text_verify | guard_cards_after_ready | native passthrough 搜索放行 |
| --- | --- | --- | --- | --- | --- |
| NanoBot | core 默认（4KB 文档） | 无 adapter 白名单 | 不适用（直接工具注册） | 不适用 | 不适用 |
| OpenCode | core 默认 | 无 adapter 白名单 | ✅ True | ❌ False | ❌ |
| OpenClaw | core 默认 | 无 adapter 白名单 | ❌ False | ✅ True | ✅ True |
| Claude Code | 文本 12KB（`CLAUDE_TEXT_ENFORCE_BYTES`） | `long_document`/`long_document_selective`/`collection_long_document`/`multi_file_evidence`/`structured_analysis_plan`；白名单外 force_sro 降级 advisory | ❌ False | ✅ True | ❌ |

含义：同一 core 的 `force_sro` 决策在 Claude 上可能因 12KB 下限或决策码不在
白名单而变成 advisory；OpenClaw 的 native passthrough 会额外放行搜索类字段；
OpenCode 允许有界的 ready 后 verify。发布文档以此矩阵为准，避免用户在不同
框架上观察到不一致行为时误判为 bug。

## Doctor 检查

只检查本机命令和 bridge，不改框架配置：

```bash
python3 scripts/install_sparseread.py --platform opencode --doctor-only
python3 scripts/install_sparseread.py --platform openclaw --doctor-only
python3 scripts/install_sparseread.py --platform claude --doctor-only
```

Windows PowerShell：

```powershell
py scripts/install_sparseread.py --platform opencode --doctor-only
py scripts/install_sparseread.py --platform openclaw --doctor-only
py scripts/install_sparseread.py --platform claude --doctor-only
```

doctor 会做两层检查：

- bridge smoke：先校验 bridge protocol `1.0`，再用临时 markdown fixture 验证 `sro_preview` 能返回 FileCard 和 L0 预览；
- 已安装集成检查：
  - OpenCode 验证 `.opencode/plugins/sparseread.js`、`.opencode/sparseread.json` 和受管 Python runtime；
  - OpenClaw 验证 `plugins inspect --runtime --json` 中的 SparseRead 工具面；默认 `auto` 下还会检查拦截 hook 已注册，`advisory` 下会检查没有 native tool 拦截 hook。
  - Claude Code 验证 `sparseread_claude.bridge` 的 protocol smoke（与
    OpenCode/OpenClaw 同一套 bridge 校验），并检查受管 Python runtime 存在。

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
tests/fixtures/quick_test/incident-report.md
```

OpenCode 安装后，推荐直接在本仓库根目录运行：

```bash
cd /absolute/path/to/SparseReading
opencode run "请自动使用 SparseRead 阅读 tests/fixtures/quick_test/incident-report.md，只提取必要证据，并回答 ROOT_CAUSE、MITIGATION_OWNER、FINAL_DEADLINE 分别是什么。不要让我手动调用工具。"
```

如果你已经在别的 workspace 里工作，先把 `tests/fixtures/quick_test/incident-report.md` 复制进去再测。OpenClaw、Claude Code 或 nanobot 会话中发送同类自然语言请求即可。Claude Code 的快速体验：

```bash
cd /path/to/your/project
claude "请自动使用 SparseRead 阅读 tests/fixtures/quick_test/incident-report.md，只提取必要证据，并回答 ROOT_CAUSE、MITIGATION_OWNER、FINAL_DEADLINE 分别是什么。"
```

预期答案应包含：

```text
ROOT_CAUSE: cache invalidation used customer_id instead of tenant_id.
MITIGATION_OWNER: Mira Chen, Data Platform on-call.
FINAL_DEADLINE: 2026-07-18 09:30 UTC.
```

## 版本发布前固定测试

每次版本更新都至少跑：

```bash
uv run --project nanobot-sro-v3 --extra dev --with pytest --with pytest-asyncio \
  pytest tests/test_release_fixtures.py -q
```

这 6 个 fixture 覆盖：

1. 长 markdown key-value 字段；
2. 日志 level preview 和 raw selector；
3. CSV schema/sample/signals；
4. JSON schema/sample/signals；
5. YAML schema/sample/signals；
6. XML root/schema/sample preview。

每个 fixture 都会同时经过 `OpenCodeBridge` 和 `OpenClawBridge`，所以它检查的
不只是 reader，也包括两个框架 bridge 的 core 功能一致性。四个框架的
发行边界由 `test_release_package_boundaries.py` 覆盖：core 无框架导入，
每个 adapter 独立发行且只依赖 core。

## Benchmark 兼容

历史 benchmark 和旧脚本可能还统计 `sro_card -> sro_read`。这条路径保留，并可通过 `SPARSEREAD_MODE=bench_protocol` 或 `SparseReadConfig(mode="bench_protocol")` 使用。

生产安装默认就是 `--sparseread-mode auto`。用户侧用自然语言要求 agent 自动使用 SparseRead；框架内部仍以 `sro_preview` 作为第一入口。需要只提示不拦截时，安装时改用 `--sparseread-mode advisory`。
OpenCode benchmark runner 里的 `plugin_auto` 才对应这个生产安装形态；`plugin_nudge` 和 `plugin_replace_truncation_experimental` 只是兼容/调试对照行，不是用户安装后的默认模式。
