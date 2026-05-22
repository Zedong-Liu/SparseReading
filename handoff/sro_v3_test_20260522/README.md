# SparseRead SRO v3 测试包

此目录是可以独立分发给同事的测试 handoff 包。包含当前已被接受的所有 SRO 代码变更、批测脚本、精选 runtime fixture 和已有结果 CSV。

## 结构

```text
code/                           代码归档
  sro_v3_accepted_changes.patch  已接受的全部 tracked code diff
  files/                        变更文件快照（补充 patch 未覆盖的新文件）
tests/scripts/                  批测脚本（API 方式跑 benchmark）
tests/runtimes/qwenclawbench/   精选 QwenClawBench runtime fixture
results/sro_experiment_data.csv 当前正式结果表
docs/                           v3_dev.md 和 runbook.md 快照
```

## 使用前提

- Python 3.12+、uv
- 克隆整个 SparseReading 仓库到本地（此包是仓库的子目录）
- 有效的 DeepSeek API key
- QwenClawBench 脚本（仓库已包含在 `SRO_test/` runtime 内，无需单独安装）

## 跑单元测试

在仓库根目录下：

```bash
uv run --project nanobot-sro-v3 pytest \
  nanobot-sro-v3/tests/sparse_reading/test_sro_text_reader.py \
  nanobot-sro-v3/tests/sparse_reading/test_sro_protocol.py \
  nanobot-sro-v3/tests/sparse_reading/test_sparseread_public_api.py \
  -q
```

## 跑 Benchmark（均只需 API key）

以下所有测试在仓库根目录下运行。环境变量示例：
```bash
export API_KEY="sk-..."
export API_BASE_URL="https://api.deepseek.com/v1"
export BENCH_MODEL="deepseek-v4-flash"
export PINCHBENCH_JUDGE_MAX_MSG_CHARS=200000
export TIMEOUT_MULTIPLIER=1
```

### 用 LooGLE 3Q 冒烟测试

```bash
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset smoke_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks task_loogle_shortdep_fall_of_outremer_3q_followup
```

### 用 task_21 PDF 阅读理解测试

```bash
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_task21_test \
  --modes baseline,gate \
  --tasks task_21_openclaw_comprehension
```

### 用 QwenClawBench 原生任务测试

```bash
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_qcb_test \
  --modes baseline,gate \
  --tasks task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check
```

### 批测多个任务

```bash
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_batch_$(date +%Y%m%dT%H%M%S) \
  --modes baseline,gate \
  --tasks \
    task_21_openclaw_comprehension \
    task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check \
    task_loogle_shortdep_fall_of_outremer_5q
```

### Dry-run（先看计划）

```bash
local_agent_comp/run_qcb_trusted_batch.sh \
  --runset my_dry \
  --modes baseline,gate \
  --tasks task_loogle_shortdep_fall_of_outremer_3q_followup \
  --dry-run
```

### 可用 mode

| mode | 含义 |
|---|---|
| `baseline` | SRO 关闭，纯 native 行为 |
| `gate` | 当前 gated SRO 行为 |
| `sro_v3` | 等效 gate |
| `force_sro_without_gate` | 跳过 Benefit Gate，对所有文件强制启用 SRO |
| `no_audit_closure` | SRO 开启但禁用 audit closure |
| `no_command_security_closure` | SRO 开启但禁用 command-security closure |
| `no_collection_closures` | SRO 开启但禁用所有 collection closure |

### 结果目录

每次运行在 `SRO_test/qwenclawbench/<runset>/<mode>/<task>/` 下产生：
- `result.json` — 标准化评估结果
- `task_transcript.jsonl` — agent 全部交互记录
- `config/manifest.json` — 运行参数快照

## 三个 Benchmark 的数据准备

### QwenClawBench（原生任务）

```bash
git clone https://github.com/QwenLM/QwenClawBench.git qwenclawbench_repo
# 任务 .md 和 assets 已在 repo 内，构造 runtime 后即可用本仓库脚本跑
```

本仓库 `SRO_test/qwenclawbench/baseline/` 和 `SRO_test/qwenclawbench/sro_v3/` 下已备好 task_00012、task_00058、task_00059 等精选 runtime。

### PinchBench（task_21 PDF）

不需要额外下载。task_21 的 PDF 和 task 定义已打包在仓库 runtime fixture 中。

### LooGLE（长文档短依赖 QA）

```bash
pip install datasets
python3 -c "
from datasets import load_dataset
ds = load_dataset('bigainlco/LooGLE', trust_remote_code=True)
# 选择一篇文档，筛选 type=='short' 的问题，构造 runtime 后运行
"
```

本仓库已备好 `Fall of Outremer` 的 5Q 和 3Q runtime fixture。

## "Benchmark" 列命名规则

表格中 Benchmark 列格式为 `数据集来源/测试框架`：
- `QwenClawBench` — 来源与框架相同
- `PinchBench/QwenClawBench` — 来自 PinchBench，通过 QwenClawBench 框架运行
- `LooGLE/QwenClawBench` — 来自 LooGLE，重新打包为 QwenClawBench 格式

## 已验证结果（CSV 全部 20 行）

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

## 不要上传

API key、生成的 transcript、历史 runset 输出、本地 Qwen/vLLM 资产、`.venv/`、`__pycache__/`、`.pytest_cache/`。
