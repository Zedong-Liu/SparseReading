# SparseRead

**你的 Agent 正在用「读取一切」的方式燃烧 token。**

现代 Agent 能浏览文件、审查仓库、分析 PDF，甚至操作整个工作区。但在需要信息时，很多 Agent 依然遵循同一种原始策略：

```text
先全部读一遍，再慢慢想
```

在 workspace 还小的时候，这能工作。

但 workspace 一膨胀，问题就来了：

每个 PDF 都是一个 token 黑洞。
每张表格都是一次膨胀观察。
每个仓库都是反复的文件扫描。
每次失败搜索都是另一笔昂贵的工具调用。

长上下文帮 agent 塞进更多文本。
SparseRead 帮 agent 避开那些它本不需要的文本。

SparseRead 是面向工具调用型 agent 的证据引导式阅读层。它拦截昂贵的文件读取，构建轻量级的文件卡片，让 agent 按需索取任务相关证据，并返回有据可循的 EvidencePack——而不是把整份文件倒进上下文。

目标很简单：

**读得更少，token 更省，证据不变。**

---

SparseRead（SRO v3）是一个面向工具调用型 agent 的 sparse-reading 协议。通过确定性的 Benefit Gate 和按类型分派的 reader，将大文件、长文本、PDF 以及审计类文件集合路由为紧凑的 evidence pack，替代反复的 broad read。

当前生产接口：

```text
sro_preview(path) -> L0 默认预览（内含 FileCard，不需要 HintSpec）
sro_read(target, mode=scout|focus|collect|refine|verify, hint=HintSpec) -> EvidencePack
```

`sro_card(path)` 仍保留给 benchmark 和旧脚本使用；新用户和新框架集成应从
`sro_preview` 开始。三平台安装和验证步骤见
[`docs/sparseread_installation.md`](docs/sparseread_installation.md)。

源代码位于 `nanobot-sro-v3/`。推荐以外层仓库根目录作为 benchmark workspace，因为测试脚本也依赖 `local_agent_comp/`、`local_bin/` 和 `SRO_test/qwenclawbench/` 中的 runtime 夹具。

## 快速开始（源码安装）

当前默认安装形态是：用户本机已有 OpenCode 或 OpenClaw CLI，然后从本仓库源码加装 SparseRead 插件。完整 fresh-machine 步骤见
[`docs/sparseread_installation.md`](docs/sparseread_installation.md)。

```bash
git clone https://github.com/Zedong-Liu/SparseReading.git sparse-reading
cd sparse-reading

uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading -q
```

OpenCode：

```bash
(cd opencode_pilot/plugin && npm install && npm run build)
mkdir -p .opencode/plugins
cp opencode_pilot/plugin/sparseread.ts .opencode/plugins/sparseread.ts

export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
export SPARSEREAD_POLICY=advisory
opencode
```

OpenClaw：

```bash
(cd openclaw_pilot/plugin && npm install && npm run build)
openclaw plugins install --link openclaw_pilot/plugin

export SPARSEREAD_PROJECT_ROOT="$PWD"
export SPARSEREAD_BRIDGE_COMMAND='["uv","run","--project","'"$PWD"'/nanobot-sro-v3","python"]'
export SPARSEREAD_MODE=auto
export SPARSEREAD_POLICY=advisory
openclaw
```

插件加载后，生产入口是 `sro_preview(path)`；只有形成明确目标后再用 `sro_read(...)` 提取定向证据。`sro_card` 仍保留给 benchmark 和旧脚本兼容。

需要跑 benchmark 时，继续看下面的 benchmark 章节；同事测试包说明见 `handoff/sro_v3_test_20260522/README.md`。

## 三个 Benchmark 的测试方法

表格中 "Benchmark" 列的命名规则：`数据集来源 / 测试框架`。

- `QwenClawBench` — 数据集来源和测试框架相同。
- `PinchBench/QwenClawBench` — task_21 数据来自 PinchBench 项目，但通过 QwenClawBench 的 benchmark.py 基础设施运行。
- `LooGLE/QwenClawBench` — 数据来自 LooGLE（HuggingFace），重新打包为 QwenClawBench 兼容格式后运行。

下面分别说明每种 benchmark 的数据准备和测试流程。均只需远端 API key，不需要本地 GPU。

### 1. QwenClawBench（原生任务）

QwenClawBench 是面向 agent 的工具调用 benchmark，约 100 个任务，覆盖代码审计、文件操作、配置诊断、数据完整性检查等。我们在其上验证了 SRO 的 audit closure、command-security closure、selection closure 等。

**数据准备：**

```bash
git clone https://github.com/QwenLM/QwenClawBench.git qwenclawbench_repo
# 任务定义：data/qwenclawbench-v1.1-100/tasks/*.md
# workspace 文件：data/qwenclawbench-v1.1-100/assets/<task_id>/
```

**构造 runtime：**

每个任务需要 `runtime/{scripts/, tasks/, assets/}`。本仓库 `SRO_test/qwenclawbench/baseline/` 和 `SRO_test/qwenclawbench/sro_v3/` 下已备好一批精选 runtime。新增任务时：

```bash
TASK="task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check"
SRC="qwenclawbench_repo/data/qwenclawbench-v1.1-100"
RUNTIME_ROOT="SRO_test/qwenclawbench/baseline/$TASK/runtime"

mkdir -p "$RUNTIME_ROOT"/{scripts,tasks,assets}
cp qwenclawbench_repo/scripts/*.py "$RUNTIME_ROOT/scripts/"
cp "$SRC/tasks/$TASK.md" "$RUNTIME_ROOT/tasks/"
cp -a "$SRC/assets/$TASK/." "$RUNTIME_ROOT/assets/"
```

SRO mode 将 `baseline` 替换为 `sro_v3`，runtime 内容一致，运行时通过 `SRO_ENABLED=1` 启用。

**运行测试：**

```bash
export API_KEY="你的 DeepSeek API key"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export BENCH_MODEL="deepseek-v4-flash"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export TIMEOUT_MULTIPLIER=1

# baseline + gate 对比
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_test_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check

# 先 dry-run 确认路径
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_dry \
  --modes baseline,gate \
  --tasks task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
  --dry-run
```

结果写入 `SRO_test/qwenclawbench/<runset>/<mode>/<task>/result.json`。

### 2. PinchBench（task_21 PDF 阅读理解）

task_21 要求 agent 从一份 OpenClaw 技能生态分析 PDF 中提取 8 个结构化答案。它来自 PinchBench 项目，通过 QwenClawBench 的 benchmark.py 运行。

**数据准备：**

不需要单独下载。task_21 的 PDF 和 task 定义已打包在本仓库：

- `SRO_test/qwenclawbench/baseline/task_21_openclaw_comprehension/runtime/assets/OpenClaw Agent Use Cases and Gap Analysis for PinchBench.pdf`
- `SRO_test/qwenclawbench/baseline/task_21_openclaw_comprehension/runtime/tasks/task_21_openclaw_comprehension.md`

**运行测试：**

```bash
export API_KEY="你的 DeepSeek API key"
export API_BASE_URL="https://llmapi.paratera.com/v1"
export BENCH_MODEL="deepseek-v4-flash"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export TIMEOUT_MULTIPLIER=1

local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_task21_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_21_openclaw_comprehension
```

当前 Phase 3 中 token 节省最显著的任务之一（DeepSeek 上 baseline 715k → gate 349k token，节省 51.1%），也暴露了 verify guard 对低质量 slot candidate 过于保守的问题。

### 3. LooGLE（长文档短依赖 QA）

LooGLE 是长上下文 benchmark，约 800 篇文档（单篇可达 100k+ 字符），每篇有 short dependency 和 long dependency 两类问题。我们选择了短依赖子集——agent 只需 `read_file` 一次获取全文，然后 `sro_read collect` 按 slot 提取局部证据，非常适合验证 SRO text reader 在单文件长文本上的压缩效果。

**数据准备：**

```bash
pip install datasets
python3 -c "
from datasets import load_dataset
ds = load_dataset('bigainlco/LooGLE', trust_remote_code=True)
# ds 有 train/validation/test 三个 split
# 每行：title, context（全文）, qa_pairs（list of {Q, A, type}）
# type == 'short' 的是短依赖问题
"
```

**选取任务文档和问题：**

从数据集中选一篇文档（如 `Fall of Outremer`），从 qa_pairs 中筛选 `type == 'short'` 的问题，取 3-10 个。将全文保存为 `document.txt`，编写 QwenClawBench 兼容的 task `.md`（参考 `SRO_test/qwenclawbench/baseline/task_loogle_shortdep_fall_of_outremer_3q_followup/runtime/tasks/` 下的范例）。

**构造 runtime 并测试：**

```bash
TASK="task_loogle_shortdep_my_doc"
mkdir -p "SRO_test/qwenclawbench/baseline/$TASK/runtime"/{scripts,tasks,assets}
cp qwenclawbench_repo/scripts/*.py "SRO_test/qwenclawbench/baseline/$TASK/runtime/scripts/"
cp document.txt "SRO_test/qwenclawbench/baseline/$TASK/runtime/assets/"
cp my_loogle_task.md "SRO_test/qwenclawbench/baseline/$TASK/runtime/tasks/$TASK.md"
# sro_v3 runtime 同理，目录名换为 sro_v3

local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_loogle_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks "$TASK"
```

本仓库已备好的 LooGLE runtime（可直接用）：

- `SRO_test/qwenclawbench/baseline/task_loogle_shortdep_fall_of_outremer_5q/` — 5 问题，满分
- `SRO_test/qwenclawbench/baseline/task_loogle_shortdep_fall_of_outremer_3q_followup/` — 3 问题，需 readerfix 满分

## 并行批处理

`run_qcb_trusted_batch.sh` 支持并行执行多个 task，通过 `PARALLEL_JOBS` 环境变量控制并发数（默认 1，即串行）。

### 用法

```bash
# 3 个 task 并发执行 baseline + gate 对比
PARALLEL_JOBS=3 \
  local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_parallel_test_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_00036_find_largest_file_in_downloads_directory \
          task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
          task_21_openclaw_comprehension
```

### 并发安全

每个 task/mode 组合自动获得独立的 agent 和 judge 目录，不会互相干扰：

- **Task agent**: `~/.openclaw/agents/bench-{model}-{mode}-{task}/`
- **Judge agent**: `~/.openclaw/agents/bench-judge-{model}-{mode}-{task}/`

无需担心 session 文件冲突或 `cleanup_agent_sessions` 交叉删除。

### 并行度建议

- API 限流不明时从 `PARALLEL_JOBS=2` 起步
- 纯 automated grading 任务可放心开到 4-6
- 含 LLM judge（hybrid）的任务建议 ≤ 3，避免远端 judge API 限流
- `--dry-run` 先确认路径无误再正式跑


## 统一测试集（14 task）

以下 14 个任务为 SRO/gate 当前验证通过的统一测试集，供同事对齐。详细分数和 token 数据见 `figures/sro_experiment_data.csv`。

| 序号 | Task ID | 简称 | 来源 | 类型 |
|---:|---|---|---|---|
| 1 | task_loogle_shortdep_fall_of_outremer | L10Q LooGLE | LooGLE | 长文本问答（10 问） |
| 2 | task_loogle_shortdep_fall_of_outremer_5q | L5Q LooGLE | LooGLE | 长文本问答（5 问） |
| 3 | task_loogle_shortdep_fall_of_outremer_3q_followup | L3Q LooGLE | LooGLE | 长文本问答（3 问） |
| 4 | task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check | T12 stock audit | QwenClawBench | 代码审计 |
| 5 | task_21_openclaw_comprehension | T21 openclaw | PinchBench | PDF 文档理解 |
| 6 | task_00036_find_largest_file_in_downloads_directory | T36 file size | QwenClawBench | 文件系统操作 |
| 7 | task_00055_literature_retrieval_bot_error_diagnosis_and_config_fix | T55 literature bot | QwenClawBench | 配置诊断 |
| 8 | task_00058_did_regression_on_simulated_panel_data | T58 DiD | QwenClawBench | 计量经济分析 |
| 9 | task_00059_user_discount_calculator | T59 discount | QwenClawBench | 规则编码 |
| 10 | task_00067_write_sparql_query_for_product_reviews_containing_iphone | T67 SPARQL | QwenClawBench | 本体查询 |
| 11 | task_00073_2026_new_issuance_p_l_decomposition_and_year_over_year_analysis | T73 P&L | QwenClawBench | 数据分析 |
| 12 | task_00086_command_prefix_security_analysis | T86 cmd sec | QwenClawBench | 安全审计 |
| 13 | task_00094_exam_monitor_system_audit_cron_sync_bug_rate_limit_gap_and_site | T94 exam | QwenClawBench | 系统审计 |
| 14 | task_00098_diagnose_scheduled_book_recommendation_failure | T98 book rec | QwenClawBench | 故障诊断 |



## 仓库目录

```text
nanobot-sro-v3/                  SRO 源码项目
local_agent_comp/                OpenClaw shim 与本地 benchmark 运行脚本
local_bin/                       本地命令 wrapper
SRO_test/qwenclawbench/          精选 benchmark runtime fixture（不含历史结果）
figures/sro_experiment_data.csv  正式结果表
handoff/sro_v3_test_20260522/    同事测试 handoff 包
```

不要提交 API key、生成的 transcript、历史 runset、本地 Qwen/vLLM 资产、缓存及 virtual environment。
