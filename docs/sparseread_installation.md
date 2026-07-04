# SparseRead 安装指南

这份文档描述当前源码安装版 SparseRead 的可用形态。默认场景是：

- 用户本机已经安装 OpenCode 或 OpenClaw CLI；
- SparseRead 从本仓库源码 checkout 后加装到框架；
- OpenCode/OpenClaw 通过本地 TypeScript 插件启动 Python bridge；
- Python bridge 复用 `nanobot-sro-v3/` 里的 SparseRead core。

这还不是 PyPI/npm/插件市场的一键发行版。当前目标是让开源用户能从源码稳定安装、验证、使用，并且所有平台使用同一套 core 能力。

## 当前生产入口

生产路径从 `sro_preview` 开始：

```text
sro_preview(path) -> L0 默认预览，内含 FileCard，不需要 HintSpec
sro_read(target, mode, hint) -> 有明确目标时再读取定向证据
```

`sro_card` 仍会注册，但只用于 benchmark 和旧脚本兼容。新用户、新插件和新文档都应该把 `sro_preview` 作为第一入口。

## 环境要求

- Python 3.11+
- `uv`
- Node.js 22.22.2+，或 Node.js 24.15.0+
- Git
- 已安装的 OpenCode CLI 或 OpenClaw CLI
- 一个 SparseRead 仓库 checkout

OpenClaw 插件声明的 host 版本要求是 `openclaw >= 2026.5.17`。当前本地验证过的 OpenClaw/OpenCode 接入是源码插件接入，不代表已经完成官方插件市场发布。

## Fresh Machine 安装

从源码开始：

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git sparse-reading
cd sparse-reading
```

先验证 Python core 能正常启动：

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading -q
```

如果只想做 bridge 级冒烟测试，可以直接调用 `sro_preview`：

```bash
uv run --project nanobot-sro-v3 python - <<'PY'
from pathlib import Path
from sparseread import SparseRead

root = Path(".sro_smoke")
root.mkdir(exist_ok=True)
target = root / "report.md"
target.write_text("# Report\n\nROOT_CAUSE: cache timeout\n" * 200)

sr = SparseRead(workspace=root, mode="force")
preview = sr.orchestrator.preview(target)
print(preview["entrypoint"], preview["file_card"]["type"])
PY
```

预期输出应包含 `sro_preview`。

## 通用 Bridge 环境变量

OpenCode 和 OpenClaw 都通过同一个 Python bridge 访问 SparseRead core。建议在启动框架前设置：

```bash
export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
export SPARSEREAD_POLICY=advisory
```

含义：

- `SPARSEREAD_PROJECT_ROOT`：SparseRead 仓库根目录，bridge 进程从这里启动。
- `SPARSEREAD_BRIDGE_COMMAND`：启动 Python bridge 的命令。用 JSON array 是为了避免路径和空格解析问题。
- `SPARSEREAD_MODE=auto`：让 core 的 Benefit Gate 判断是否值得 SparseRead。
- `SPARSEREAD_POLICY=advisory`：默认只提示/建议，不强拦截原生读取。需要高置信长文档或 PDF 场景时再改为 `enforce`。

## 安装到 OpenCode

假设你已经能在目标 workspace 里运行 `opencode`。

先构建/类型检查插件：

```bash
cd opencode_pilot/plugin
npm install
npm run build
cd ../..
```

把插件放到 OpenCode workspace：

```bash
mkdir -p .opencode/plugins
cp opencode_pilot/plugin/sparseread.ts .opencode/plugins/sparseread.ts
```

从同一个 workspace 启动 OpenCode：

```bash
export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
export SPARSEREAD_POLICY=advisory

opencode
```

OpenCode 插件会暴露：

- `sro_preview`
- `sro_read`
- `sro_card`，仅兼容旧路径
- `sro_trace`

本地 bridge 冒烟测试：

```bash
uv run --project nanobot-sro-v3 python -m sparseread.bridge.opencode \
  --workspace . --mode force
```

然后输入 JSONL：

```json
{"id":"1","method":"preview","params":{"path":"README.md"}}
{"id":"2","method":"trace","params":{}}
{"id":"3","method":"shutdown","params":{}}
```

第一行响应里应看到 `entrypoint: "sro_preview"` 和 `file_card`。

## 安装到 OpenClaw

假设你已经能运行 `openclaw`，并且版本满足 `>= 2026.5.17`。

构建插件：

```bash
cd openclaw_pilot/plugin
npm install
npm run build
cd ../..
```

以本地 link 方式安装：

```bash
openclaw plugins install --link openclaw_pilot/plugin
```

启动 OpenClaw 前设置同一组 bridge 环境变量：

```bash
export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
export SPARSEREAD_POLICY=advisory

openclaw
```

OpenClaw 插件会暴露：

- `sro_preview`
- `sro_read`
- `sro_decide`
- `sro_trace`
- `sro_card`，仅兼容旧路径

本地 bridge 冒烟测试：

```bash
uv run --project nanobot-sro-v3 python -m sparseread.bridge.openclaw \
  --workspace . --mode force
```

然后输入 JSONL：

```json
{"id":"1","method":"preview","params":{"path":"README.md"}}
{"id":"2","method":"trace","params":{}}
{"id":"3","method":"shutdown","params":{}}
```

第一行响应里应看到 `entrypoint: "sro_preview"` 和 `file_card`。

## 安装到 nanobot

nanobot 路径是 core 原生集成，不需要 TypeScript 插件。启用环境变量：

```bash
export SRO_ENABLED=1
```

对于 nanobot-style agent，可以显式安装 adapter：

```python
from sparseread.adapters.nanobot import install

runtime = install(agent, mode="auto", workspace=".")
```

adapter 会注册 `sro_preview`、`sro_read` 和兼容用的 `sro_card`，并把大文件读取、目录读取、搜索和高风险 raw dump 命令接到同一个 SparseRead runtime。

## 日常使用建议

默认使用顺序：

```text
1. 大文件、长 markdown、PDF、日志或多文件证据包 -> sro_preview(path)
2. 从 preview 里形成明确问题 -> sro_read(..., hint=HintSpec)
3. EvidencePack 已经 ready -> 写交付物，不要重复读同一证据
4. 小文件、脚本、配置改动、全表计算 -> 原生工具通常更便宜
```

不要把 SparseRead 当成“所有内容都压缩”的代理。它的优势是让 agent 少读无关材料，并在需要时用可回溯的 evidence pack 补证据。

## 故障排查

如果插件启动失败，先检查：

```bash
which uv
which node
which opencode || true
which openclaw || true
```

再单独跑 bridge smoke：

```bash
uv run --project nanobot-sro-v3 python -m sparseread.bridge.opencode --workspace . --mode force
uv run --project nanobot-sro-v3 python -m sparseread.bridge.openclaw --workspace . --mode force
```

如果 JSONL smoke 正常，但框架里没有工具，问题通常在插件加载或 CLI profile 配置；如果 JSONL smoke 失败，问题通常在 Python 依赖、`SPARSEREAD_PROJECT_ROOT` 或 `SPARSEREAD_BRIDGE_COMMAND`。

## Benchmark 兼容

旧 benchmark 和历史脚本可能还统计 `sro_card -> sro_read`。这条路径保留，不会马上删除。

生产文档、框架插件和新用户教程只讲 `sro_preview -> sro_read`。这样无 HintSpec 的 L0 默认预览能成为真实使用入口，而不是 benchmark 专用路径。
