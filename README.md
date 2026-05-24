# SparseReading

SparseRead（SRO v3）是一个面向工具调用型 agent 的 sparse-reading 协议。通过确定性的 Benefit Gate 和按类型分派的 reader，将大文件、长文本、PDF 以及审计类文件集合路由为紧凑的 evidence pack，替代反复的 broad read。

当前协议接口：

```text
sro_card(path) -> FileCard
sro_read(target, mode=scout|focus|refine|verify, hint=HintSpec) -> EvidencePack
```

源代码位于 `nanobot-sro-v3/`。推荐以外层仓库根目录作为 benchmark workspace，因为测试脚本也依赖 `local_agent_comp/`、`local_bin/` 和 `SRO_test/qwenclawbench/` 中的 runtime 夹具。

## 快速开始

运行核心 SRO 单元测试：

```bash
uv run --project nanobot-sro-v3 pytest \
  nanobot-sro-v3/tests/sparse_reading/test_sro_text_reader.py \
  nanobot-sro-v3/tests/sparse_reading/test_sro_protocol.py \
  nanobot-sro-v3/tests/sparse_reading/test_sparseread_public_api.py \
  -q
```

用 DeepSeek API 跑一个 benchmark 冒烟测试：

```bash
export API_KEY="..."
export API_BASE_URL="https://llmapi.paratera.com/v1"
export BENCH_MODEL="deepseek-v4-flash"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export TIMEOUT_MULTIPLIER=1

local_agent_comp/run_qcb_trusted_batch.sh \
  --runset smoke_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_loogle_shortdep_fall_of_outremer_3q_followup
```

同事测试包说明详见 `handoff/sro_v3_test_20260522/README.md`。

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


## 已验证结果（CSV 全部 20 行）

以下为 `figures/sro_experiment_data.csv` 中当前保存的所有验证结果。`SRO win` 表示 SRO reader/closure 路径直接贡献了收益。`Gate/pass` 表示 Benefit Gate 保持或改善了行为（部分通过 native bypass，非 SRO 工具直接收益）。`Boundary` 行保留但附有注意事项。

| 模型 | 任务 | 简称 | 判定 | Benchmark | Baseline | SRO/Gate | Token baseline → SRO | 缩减 | 备注 |
|---|---|---|---|---|---:|---:|---:|---:|---|
| Qwen | `task_21` | T21 OpenClaw PDF | SRO win | PinchBench/QwenClawBench | 0.944 | 1.0 | 72,865 → 34,154 | 53.1% | slots collect |
| Qwen | `task_00012` | T12 股票审计 | SRO win | QwenClawBench | 0.358 | 1.0 | 124,843 → 39,085 | 68.7% | audit closure |
| Qwen | `task_00036` | T36 文件大小 | Gate/pass | QwenClawBench | 0.6875 | 0.6875 | 51,881 → 44,265 | 14.7% | native gate |
| Qwen | `task_00059` | T59 折扣计算 | SRO win | QwenClawBench | 0.5 | 0.533 | 343,507 → 104,449 | 69.6% | selection+script |
| Qwen | `task_00067` | T67 SPARQL | Gate/pass | QwenClawBench | 0.75 | 0.875 | 89,871 → 89,999 | −0.1% | gate fix; 接近 baseline |
| Qwen | `task_00073` | T73 P&L 分析 | Gate/pass | QwenClawBench | 0.883 | 0.904 | 336,436 → 259,955 | 22.7% | gate pass |
| Qwen | `task_00086` | T86 命令安全 | SRO win | QwenClawBench | 0.309 | 0.954 | 140,514 → 90,695 | 35.5% | command-security closure |
| Qwen | `task_00098` | T98 书籍推荐 | Boundary | QwenClawBench | 0.917 | 1.0 | 186,005 → 143,502 | 22.9% | closure 辅助 |
| DeepSeek | `task_21` | T21 OpenClaw PDF | SRO win | PinchBench/QwenClawBench | 1.0 | 1.0 | 714,716 → 349,224 | 51.1% | Phase3 slots collect + native fallback |
| DeepSeek | `task_00012` | T12 股票审计 | SRO win | QwenClawBench | 0.7917 | 0.9688 | 253,685 → 110,056 | 56.6% | Phase3 audit closure; score +0.177 |
| DeepSeek | `task_00036` | T36 文件大小 | Gate/pass | QwenClawBench | 0.6875 | 0.6875 | 51,881 → 44,265 | 14.7% | native gate |
| DeepSeek | `task_00059` | T59 折扣计算 | Gate/pass | QwenClawBench | 0.708 | 0.833 | 575,574 → 173,156 | 69.9% | runtime fix retest |
| DeepSeek | `task_00067` | T67 SPARQL | Boundary | QwenClawBench | 0.6208 | 0.5583 | 167,609 → 148,837 | 11.2% | native bypass; judge 方差 |
| DeepSeek | `task_00058` | T58 DID 回归 | Gate/pass | QwenClawBench | 1.0 | 1.0 | 447,300 → 375,432 | 16.1% | native bypass; 非 SRO 工具收益 |
| DeepSeek | `task_00073` | T73 P&L 分析 | Gate/pass | QwenClawBench | 0.854 | 0.854 | 318,177 → 213,807 | 32.8% | gate pass |
| DeepSeek | `task_00086` | T86 命令安全 | Gate/pass | QwenClawBench | 0.6 | 0.954 | 1,152,253 → 859,009 | 25.4% | profile gate; no SRO |
| DeepSeek | `task_00098` | T98 书籍推荐 | Gate/pass | QwenClawBench | 0.896 | 0.867 | 467,170 → 312,598 | 33.1% | gate native; token 降低 |
| DeepSeek | `task_loogle_shortdep_fall_of_outremer_5q` | LooGLE Outremer 5Q | SRO win | LooGLE/QwenClawBench | 1.0 | 1.0 | 177,141 → 61,285 | 65.4% | readerfix v2; 100k 字符单行文档 |
| DeepSeek | `task_loogle_shortdep_fall_of_outremer_3q_followup` | LooGLE Outremer 3Q | SRO win | LooGLE/QwenClawBench | 1.0 | 1.0 | 155,688 → 40,300 | 74.1% | readerfix; gate vs 不变 native baseline |
| Qwen | `task_loogle_shortdep_fall_of_outremer_3q_followup` | LooGLE Outremer 3Q | SRO win | LooGLE/QwenClawBench | 0.0 | 1.0 | 621,281 → 27,511 | 95.6% | readerfix; baseline 耗尽 50 次 tool call |

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
