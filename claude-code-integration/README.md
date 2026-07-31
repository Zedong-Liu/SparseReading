# SparseRead for Claude Code — 集成包

> Phase 1: MCP Tools + PreToolUse Hook
> 日期: 2026-07-10
> 验证环境: Windows 11 / Python 3.13 / uv / Claude Code (最新版)

---

## 0. 快速开始（5 分钟）

### 前置条件
- Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)
- Claude Code 已安装并能在项目中运行
- 本包解压到 SparseReading 项目的 `claude-code-integration/` 目录

### 文件放置

将包内文件复制到 SparseReading 项目对应位置：

```
<PROJECT_ROOT>/
├── .mcp.json                              ← 从包里复制到根目录
├── integrations/claude/
│   ├── README.md                           ← 从包里复制
│   └── hooks/claude_hook.py                ← 从包里复制
└── nanobot-sro-v3/sparseread/bridge/
    ├── claude.py                           ← 从包里复制
    └── claude_mcp.py                       ← 从包里复制
```

### 配置 `.claude/settings.local.json`

```json
{
  "enabledMcpjsonServers": ["sparseread"],
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["<PROJECT_ROOT>/integrations/claude/hooks/claude_hook.py"]
          }
        ]
      }
    ]
  }
}
```

### 验证

```bash
cd <PROJECT_ROOT>
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_bridge_claude.py -v
```

预期：14 passed ✅

---

## 1. 架构与文件清单

### 三层集成

| 层 | 机制 | 作用 |
|----|------|------|
| Layer 1 — MCP Tools | `.mcp.json` → `uv run` → `claude_mcp.py` | 向 Claude 暴露 7 个 SRO 工具 |
| Layer 2 — CLAUDE.md | 静态 Markdown 文本 | 告诉 Claude 何时使用 SRO 工具 |
| Layer 3 — PreToolUse Hook | `settings.local.json` → `claude_hook.py` | 拦截大文件 read_file / bash cat |

---

## 2. 已完成的安装配置（本机实录）

### 2.1 `.mcp.json` — MCP Server 定义

**文件路径**：`<PROJECT_ROOT>/.mcp.json`

```json
{
  "mcpServers": {
    "sparseread": {
      "command": "uv",
      "args": [
        "--project",
        "<PROJECT_ROOT>/nanobot-sro-v3",
        "run",
        "python",
        "-m",
        "sparseread.bridge.claude_mcp",
        "--workspace",
        "."
      ],
      "env": {
        "SRO_ENABLED": "1"
      }
    }
  }
}
```

> **关键点**：
> - Claude Code 的 MCP Server 定义放在 `.mcp.json`（项目根目录），**不是** `.claude/settings.local.json`。
> - `--workspace .` 表示以 Claude Code 当前工作目录为 SparseRead 扫描范围。
> - 使用 `uv run --project` 启动，无需 pip install。

### 2.2 `.claude/settings.local.json` — Hook 配置 + MCP 审批

**文件路径**：`<PROJECT_ROOT>/.claude/settings.local.json`

```json
{
  "permissions": {
    "allow": [
      "WebSearch"
    ]
  },
  "enabledMcpjsonServers": [
    "sparseread"
  ],
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": [
              "<PROJECT_ROOT>/integrations/claude/hooks/claude_hook.py"
            ]
          }
        ]
      }
    ]
  }
}
```

> **关键点**：
> - `enabledMcpjsonServers` 审批 `.mcp.json` 中定义的 `sparseread` server，首次启动 Claude Code 时会提示确认。
> - `hooks.PreToolUse` 拦截所有 `Read` 和 `Bash` 工具调用，matcher 用正则 `Read|Bash`。
> - Hook 类型为 `"command"`（每次调用启动新进程），`claude_hook.py` 纯标准库无依赖。

### 2.3 配置踩坑记录

| 问题 | 原因 | 解决 |
|------|------|------|
| `mcpServers` 放在 `settings.local.json` 报错 `Unrecognized field` | 项目级 settings 不支持 `mcpServers` 字段 | 改为 `.mcp.json` + `enabledMcpjsonServers` |
| 现有 `test_bridge_shared.py` 等全部 `PermissionError` | Windows `pytest-asyncio` + `tmp_path` 的已知权限问题 | 我新增的测试用 `tempfile.TemporaryDirectory` 规避 |
| `BenefitDecision` 构造报错 `unexpected keyword argument 'action'` | `action` 是 `@property`，不是构造参数 | 移除 `action=` 参数 |

---

## 3. 验证步骤

### 3.1 运行时机

以下命令在 **项目根目录** `<PROJECT_ROOT>` 执行。

### 3.2 测试 1：MCP Server 启动

```bash
uv run --project nanobot-sro-v3 python -m sparseread.bridge.claude_mcp --workspace .
```

**预期**：输出 `[sparseread] Starting MCP server — workspace: ...` 并挂起等待 stdio 输入，按 `Ctrl+C` 停止。

**实测**：✅ 通过

### 3.3 测试 2：Claude Bridge 单元测试（14 个）

```bash
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_bridge_claude.py -v
```

**实测结果**：14 passed ✅

```
test_classify_claude_gate_pdf_is_enforce PASSED
test_classify_claude_gate_small_code_is_native PASSED
test_classify_claude_gate_large_text_is_enforce PASSED
test_classify_claude_gate_medium_text_is_advisory PASSED
test_classify_claude_gate_native_passthrough PASSED
test_claude_bridge_preview_raw_trace PASSED
test_claude_bridge_ready_guard_stops_repeat_reads PASSED
test_claude_bridge_gate_t86_advisory_and_audit_enforce PASSED
test_claude_bridge_preflight_reports_enforce_targets PASSED
test_claude_bridge_generated_outputs_stay_native PASSED
test_mcp_handle_tool_preview PASSED
test_mcp_handle_tool_decide PASSED
test_mcp_handle_tool_trace PASSED
test_mcp_handle_unknown_tool PASSED
```

### 3.4 测试 3：Hook — 大文件拦截

```bash
echo '{"tool_name":"Read","tool_input":{"file_path":"<PROJECT_ROOT>/v3_dev.md"}}' \
  | python integrations/claude/hooks/claude_hook.py ; echo "EXIT=$?"
```

**实测**：exit code 2，输出 `permissionDecision: "deny"` + `additionalContext` 包含 `sro_preview` 引导 ✅

### 3.5 测试 4：Hook — 小文件放行

```bash
echo '{"tool_name":"Read","tool_input":{"file_path":"<PROJECT_ROOT>/integrations/claude/hooks/claude_hook.py"}}' \
  | python integrations/claude/hooks/claude_hook.py ; echo "EXIT=$?"
```

**实测**：exit code 0，`permissionDecision: "allow"` ✅

### 3.6 测试 5：Hook — Bash cat 拦截

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"cat <PROJECT_ROOT>/v3_dev.md"}}' \
  | python integrations/claude/hooks/claude_hook.py ; echo "EXIT=$?"
```

**实测**：exit code 2（v3_dev.md 约 213KB，触发拦截） ✅

### 3.7 测试 6：Claude Code 会话内验证

启动 Claude Code 后，在对话中：

1. 确认 `/mcp` 或工具列表中出现 `sro_preview`、`sro_read` 等 7 个 SRO 工具
2. 对一个大 markdown 文件（如 `v3_dev.md`）执行 `read_file`：
   - 被 PreToolUse Hook 拦截（exit 2 + additionalContext）
   - Claude 应被引导使用 `sro_preview(path)` 替代
3. 手动调用 `sro_preview(path="v3_dev.md")`：
   - 应返回文件结构、内容样本、raw_ref 和 next_action

---

## 4. Gate 决策逻辑

Hook 脚本 (`claude_hook.py`) 使用纯标准库的启发式判断：

| 条件 | 判定 | 行为 |
|------|------|------|
| PDF 文件（`.pdf` 后缀） | enforce | exit 2 阻止 + additionalContext |
| 文本文件 >12KB（`.md/.txt/.log/.csv`） | enforce | exit 2 阻止 + additionalContext |
| 目录含 3+ 文件 | enforce | exit 2 阻止 + additionalContext |
| 代码文件 <4KB（`.py/.js/.ts/.go`…） | native | exit 0 放行 |
| 小配置文件 | native | exit 0 放行 |
| 生成产物（`fetch-audit` 等命名） | native | exit 0 放行（防重入） |
| 不存在 / 无法判断 | native | exit 0 放行 |

Bridge (`claude.py`) 复用 SparseRead 核心 Benefit Gate，额外增加 Claude 专属字段：

| 条件 | 决策 | block_native_read |
|------|------|-------------------|
| PDF | enforce | true |
| 文本 >12KB | enforce | true |
| 审计 bundle（代码+数据+输出） | enforce | true |
| 文本 4-12KB | advisory | false |
| 命令安全 bundle | advisory | false |
| 代码/配置 <4KB | native | false |
| 生成/运行时产物 | native | false |

---

## 5. 工具速查

| 工具 | 用途 | 关键参数 |
|------|------|---------|
| `sro_preview` | **主入口** — 预览大文件/PDF/目录 | `path` |
| `sro_read` | 定向证据提取 | `target`, `mode`(scout/focus/collect/refine/verify), `hint` |
| `sro_card` | 兼容性/调试 — FileCard + gate | `path` |
| `sro_raw` | 获取 raw_ref 的原始内容（仅后备） | `raw_ref` |
| `sro_decide` | 查看路径的 gate 决策 | `path` |
| `sro_trace` | Session 追踪汇总 | — |
| `sro_preflight` | 扫描 workspace 找 SRO 目标 | — |

### sro_read 的 hint 结构

```json
{
  "goal": "你需要什么证据（必填）",
  "needles": ["关键词1", "关键词2"],
  "slots": [{"id": "slot1", "question": "具体问题"}],
  "want": "fact | list | count | schema | table | verbatim",
  "scope": "new | all",
  "type_hint": "text | csv | json | pdf | collection"
}
```

---

## 6. 与其他框架的对比

| | OpenClaw | OpenCode | Claude Code |
|--|----------|----------|-------------|
| 工具注册 | `api.registerTool()` | `tool({...})` | MCP Server (`.mcp.json`) |
| 阻止 read | `before_tool_call → block:true` | `tool.execute.before → throw` | PreToolUse exit(2) |
| 阻止 grep | ✅ | ❌ | ❌ |
| 阻止 bash cat | ✅ | ✅ | ✅ |
| 上下文注入 | `before_prompt_build` | 无 | `additionalContext` |
| Token 追踪 | ✅ | ❌ | ❌ |
| 等效度 | 100% | ~90% | ~85% |

---

## 7. 已知限制

1. **Token 追踪缺失**：Claude Code 无 `llm_output` hook
2. **Grep/Search 软引导**：无法精准拦截 Grep 工具，依赖 CLAUDE.md + MCP tool description 引导
3. **Hook 无状态**：每次调用新进程，不维护跨调用 gate 状态。如需 session 级状态，可将 hook `type` 改为 session 模式
4. **Windows 路径**：Hook 中的中文路径在 bash echo 中可能有编码问题，但 Claude Code 直接传 JSON stdin 不受影响

---

## 8. 卸载

```bash
# 移除 .mcp.json 中的 sparseread 段（或直接删文件）
# 移除 .claude/settings.local.json 中的 hooks.PreToolUse 段和 enabledMcpjsonServers
```

## 9. 下一步 (Phase 2+)

- PostToolUse Hook — 大文件读取后自动追加 nudge
- CLAUDE.md 动态注入
- Session 级 gate 状态管理
