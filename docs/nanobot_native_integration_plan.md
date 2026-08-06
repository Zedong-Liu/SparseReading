# NanoBot 原生安装集成改写方案

日期：2026-08-06
目标：把 NanoBot 集成从“改宿主源码”改成“官方扩展面上的原生适配层”，
效果与现有宿主 patch 集成对齐；以 14-task DeepSeek-V4-Flash 实测验收。

## 1. 从另外三个框架学到的共同模式

三个成功框架都不是改宿主，而是用官方扩展面做“薄适配层”：

| 框架 | 官方扩展面 | SRO 适配层做的事 |
| --- | --- | --- |
| OpenCode | TS 插件 API | 注册 SRO 工具；包装原生 read schema；检测截断输出并 nudge/替换 |
| OpenClaw | npm 插件 + typed hooks + pluginConfig | `registerTool` 注册工具；PreToolUse/PostToolUse hooks 做 gate；bridge 协议 1.0 |
| Claude Code | MCP + PreToolUse/PostToolUse session hook | MCP 8 工具；hook 内 deny/advisory/native + one-time-block + 大输出 nudge |

共同点：
1. 工具注册走框架官方入口；
2. 原生工具调用在“执行前”被钩子决策/改写（等价 PreToolUse）；
3. gate 决策全部来自共享 core（BenefitGate + episode），适配层只做执行面翻译；
4. 模型可见引导走插件自带文本（OpenClaw skill / Claude CLAUDE.md / OpenCode schema+nudge）。

NanoBot 其实有同样的官方扩展面，我们没用：
- `nanobot.tools` entry point（`nanobot/agent/tools/loader.py:68`）——官方工具插件入口；
- `AgentHook`（`nanobot/agent/hook.py`）——`before_iteration` / `before_execute_tools` /
  `after_iteration` / `finalize_content`；
- 执行语义已验证：`runner.py:331` 调 `hook.before_execute_tools(context)`，随后
  `runner.py:338` 用 `response.tool_calls` 执行；而 `context.response` 就是同一个
  对象，hook 改写 `response.tool_calls` 即可在原生工具执行前拦截/替换。

## 2. 现状与目标

现状（改宿主）：
- `nanobot-sro-v3/nanobot/agent/tools/filesystem.py` 内嵌 `_sro` 字段：
  `bind_episode` / `episode_hint_probe` / `should_handoff_read/list` /
  `handoff_message` / `record_output_write`；
- `nanobot-sro-v3/nanobot/agent/loop.py:1416` 加 `finish_episode`；
- `exec` 工具挂 `sparseread.core.policy.SparseCommandPolicy`；
- 模型引导依赖宿主内 `skills/sparse-reading/SKILL.md`。

目标（原生）：
- `sparseread-nanobot` 只通过 `AgentHook` + `nanobot.tools` 扩展面接入；
- 不依赖宿主源码里的任何 SRO 字段/调用；
- 用户侧：`pip install nanobot-ai sparseread-core sparseread-nanobot`，
  `AgentLoop(hooks=[SparseReadHook(orchestrator)])` 或 `wrap(agent)` 自动挂载。

## 3. 改写步骤（文件级）

### 3.1 新增 `integrations/nanobot/python/src/sparseread_nanobot/hook.py`

`SparseReadHook(AgentHook)`，内部持有 `SparseRead` runtime / orchestrator：

- `before_execute_tools(context)`：
  - 遍历 `context.response.tool_calls`；
  - `read_file / list_dir / grep`：
    - 解析 path（与当前 filesystem.py 相同的参数语义）+ 可选 `episode_hint`；
    - 走共享 `BenefitGate.decide(info, context)`：
      - `force_sro` → 把该调用改写为 `sro_preview(path, episode_hint?)`（等价当前
        `handoff_message` 的语义）；
      - `advisory / native` → 放行；
    - ready/collection-parent 语义保持 core 不变（core 的 ready guard 在
      `sro_preview/sro_read` 内生效）。
  - `exec / bash / shell`：
    - 用共享 `SparseCommandPolicy.guard(command, cwd)` 判断；
    - 命中拦截 → 改写为内部 `sro_guard` 工具调用，返回 policy 的错误消息
      （等价当前 exec 挂 policy 的行为）。
  - `write_file / edit_file / apply_patch`：
    - 解析目标 path → `orchestrator.record_output_write(path)`（write provenance，
      等价当前 filesystem.py）。
- `before_iteration(context)`：会话首次注入 SRO 使用引导（内容来自包内
  `skills/sparse-reading/SKILL.md` 文本，替代宿主 skill 文件）。
- `after_iteration(context)` / `finalize_content(context, content)`：
  - 检测 turn 结束（`context.stop_reason` 非空等）→
    `orchestrator.finish_episode(conversation_id)`（替代 loop.py patch；
    conversation_id 由 hook 配置，默认 `"default"`，14-task bench 每任务独立进程）。

### 3.2 改写 `adapter.py`

- 保留：注册 SRO 工具（`tools.register`，与 `nanobot.tools` entry point 双通道）；
- 删除/降级：`tool._sro = ...`、`exec_tool.sro_policy = ...` 的依赖分支；
- 新增：`agent._extra_hooks.append(SparseReadHook(...))`（AgentLoop 构造参数
  `hooks=[...]` 的运行时等价物），或通过 `wrap()` 传入。

### 3.3 包内容

- `sparseread-nanobot` 内置 `skills/sparse-reading/SKILL.md`（hook 注入文本来源）；
- `pyproject.toml` 增加 `[project.entry-points."nanobot.tools"]`（可选，注册
  SRO 工具为官方插件）。

### 3.4 宿主处理

- 先做“adapter 独立”：保留 vendored host 不动，但 hook 路径不触碰 `_sro` 字段，
  使 host patch 变为惰性；
- 14-task 验证通过后，再把 `nanobot-sro-v3` 清理为纯上游依赖或移出发布仓库
  （vendored nanobot 自旧版本就带 SRO 钩子，回退不等于上游，需单独处理）。

## 4. 效果对齐矩阵

| 行为 | 旧（宿主 patch） | 新（hook） |
| --- | --- | --- |
| read_file 大文件 handoff | filesystem.py `should_handoff_read` → `handoff_message` | hook 改写为 `sro_preview` |
| list_dir 集合 handoff | filesystem.py `should_handoff_list` | hook 改写为 `sro_preview` |
| grep/exec 大文件 dump 拦截 | exec 挂 `SparseCommandPolicy` | hook 用同一 policy → `sro_guard` 返回 |
| episode_hint 绑定/探测 | filesystem.py `bind_episode` / `episode_hint_probe` | hook 解析参数 → 直接交 `sro_preview`（core 处理 hint/probe） |
| write provenance | filesystem.py `record_output_write` | hook 记录 write 参数 |
| episode 收尾 | loop.py `finish_episode` | hook `after_iteration/finalize_content` |
| 模型引导 | 宿主 SKILL.md | hook 注入系统消息（文本来自包内 SKILL.md） |
| gate/ready/collection-parent | 共享 core | 共享 core（不变） |

## 5. 测试与 14-task 验收

1. 单元测试：
   - hook 对 read/exec/write 的改写（force/advisory/native、episode_hint、guard 消息）；
   - adapter 在“宿主工具无 `_sro`”的 mock 下仍能安装成功（证明不再依赖宿主 patch）；
   - finish_episode 触发。
2. 宿主测试改写：`test_sro_protocol.py` / `test_sparseread_public_api.py` /
   `test_nanobot_v020_adapter.py` 改为 `AgentLoop(hooks=[SparseReadHook(...)])` 形态。
3. 14-task 实测（DeepSeek-V4-Flash via Paratera）：
   - `benchmarks/run_qcb_trusted_batch.sh`，SRO_ENABLED=0/1，14 task ×
     baseline/gate = 28 case；
   - 对齐基准：`nanobot_v020_flash_unified14_20260731` + `structured_fix` 的
     paired 汇总（Token red、Score Δ、Time save）；
   - 差异大 → 定位（gate 决策、hook 改写、episode 状态）并迭代；
   - 验收门槛：Score Δ 不回退、Token/Time 收益不低于原宿主集成。

## 6. 风险与决策点

- `before_execute_tools` 改写 `response.tool_calls` 的执行语义：代码已确认
  runner 用同一对象执行，落地前先做 5 分钟 spike 验证；
- `AgentHookContext` 没有 `session_key`：v1 用 hook 配置的 conversation_id；
  真实多会话可后续向上游提需求（官方 hook 增加 session 字段）；
- vendored nanobot 自带旧 SRO 钩子（`6eb5fe0^` 之前已有 `_sro`）：回退宿主
  不等于恢复上游，需要单独确认目标上游版本；
- 工作量：实现约 0.5-1 天，14-task DS-Flash 约 2-4 小时。
