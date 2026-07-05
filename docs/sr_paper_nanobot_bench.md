# SparseRead Paper Nanobot Bench Branch

本分支用于论文/刷榜型实验，不是开源生产安装分支。

默认目标是保留最新 SR core，同时把 nanobot 路径收敛回旧的高收益协议：

```text
sro_card(path) -> sro_read(target={artifact_id}, mode, HintSpec) -> write_file
```

生产 single-repo 分支的 `sro_preview` / L0 默认预览仍保留在 core 中，但 paper bench profile 不把它作为默认入口。

## 旧优势状态

旧优势分数主要来自 nanobot SRO v3 的 gate / closure / ready-stop 轨迹，而不是 OpenCode/OpenClaw 插件，也不是后来的 `sro_preview` 生产入口。

关键状态：

- `026d7cf`：P0 compact skill，被用于 P0 当前控制和早期稳定正收益。
- `6ea5400`：P1.5 fix3 activation boundary，当前官方 Pro 结果采用的核心行为状态。
- `42fd78c`：把 P1.5 fix3 Pro 结果写入 `figures/sro_experiment_data.csv` 和图表。

不要继续用本地 `codex/p1-ablation-p0-c` 作为开发基线。它原来只是 P0+C ablation 点，远端分支已经删除，本地 worktree 后来还混入了 unrelated/docs/install 改动。

## 当前分支形态

本分支从 `origin/codex/sr-single-repo-integrations` 开出，因此包含最新 core 和最新测试夹具。bench 运行时使用：

```bash
export SRO_ENABLED=1
export SPARSEREAD_MODE=bench_protocol
```

在 nanobot AgentLoop 中，这会只注册：

```text
sro_card
sro_read
```

不会注册：

```text
sro_preview
sro_raw
```

如果需要 public wrapper 方式：

```python
from sparseread import SparseRead, SparseReadConfig

runtime = SparseRead(SparseReadConfig(mode="bench_protocol", workspace="."))
assert runtime.tool_names == ["sro_card", "sro_read"]
```

## 本地验证

每次修改本分支至少跑：

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_sparseread_public_api.py \
         nanobot-sro-v3/tests/sparse_reading/test_sro_tool_schema.py -q
```

完整 SR 本地回归：

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading -q
```

## 推荐 benchmark 关注点

优先用能复现 SR 原有优势的任务：

- LooGLE long text：`L10Q`、`L5Q`、`L3Q`
- PDF multi-fact：`task_21_openclaw_comprehension`
- audit closure：`task_00012_a_stock_fetcher_system_audit_bug_identification_and_data_integrity_check`
- command-security closure：`task_00086_command_prefix_security_analysis`

每个结果必须同时汇报：

- score
- total tokens
- request count
- SRO tool trajectory
- 是否出现 native broad read fallback

边界/负例任务继续保留，但不要把 native-bypass 的收益写成 reader 收益。
