# SparseRead 集成到 Claude Code 的优化方案

> 基于 OpenClaw / OpenCode 集成模式的参考分析
> 撰写日期：2026-07-08
> 参考：`闭源AI集成方案分析.md`、`Claude集成方案.md`、`项目理解与参考项目分析.md`

---

## 目录

1. [OpenClaw / OpenCode 集成模式复盘](#1-openclaw--opencode-集成模式复盘)
2. [Claude Code 能力的重新评估](#2-claude-code-能力的重新评估)
3. [新旧方案对比](#3-新旧方案对比)
4. [优化后的集成架构](#4-优化后的集成架构)
5. [四个集成层详解](#5-四个集成层详解)
6. [与 OpenClaw/OpenCode 的 gate 对比](#6-与-openclawopencode-的-gate-对比)
7. [开源 vs 闭源的集成模式本质区别](#7-开源-vs-闭源的集成模式本质区别)
8. [端口/访问约束的规避策略](#8-端口访问约束的规避策略)
9. [实现计划](#9-实现计划)
10. [与 OpenClaw/OpenCode 代码复用策略](#10-与-openclawopencode-代码复用策略)

---

## 1. OpenClaw / OpenCode 集成模式复盘

### 1.1 OpenClaw 的集成手段（最完整）

OpenClaw 的 `integrations/openclaw/plugin/src/index.ts` 展示了开源框架能做到的**全部控制**：

| 能力 | 实现方式 | 关键代码 |
|------|---------|---------|
| **注册自定义工具** | `api.registerTool()` | 原生注册 6 个 SRO 工具（sro_preview/read/card/raw/decide/trace） |
| **阻止工具执行** | `before_tool_call` 返回 `{ block: true }` | `if (gate?.block_native_read === true) return { block: true, blockReason }` |
| **修改工具描述** | 注册时自带 description | `description: "Production entrypoint..."` |
| **注入系统提示** | `before_prompt_build` 返回 `appendSystemContext` | 每次构建 prompt 时动态注入 SRO 使用指南 |
| **执行后追踪** | `after_tool_call` | 记录每次原生工具执行 |
| **LLM 用量追踪** | `llm_output` | 记录 token 消耗 |
| **生命周期** | `agent_end` / `cleanup` | 清理进程和状态 |

### 1.2 OpenCode 的集成手段（次完整）

OpenCode 的 `integrations/opencode/plugin/sparseread.ts`：

| 能力 | 实现方式 | 关键代码 |
|------|---------|---------|
| **注册自定义工具** | `tool({...})` | 原生注册 5 个 SRO 工具 |
| **阻止工具执行** | `tool.execute.before` 返回 `throw new Error()` | `throw new Error('SparseRead enforce: use sro_preview first')` |
| **修改工具输出** | `tool.execute.after` 修改 `output.output` | `output.output += nudgeMessage` |
| **修改工具描述** | `tool.definition` | `output.description += " SparseRead: ..."` |
| **生命周期** | `dispose` | 清理 bridge 进程 |

### 1.3 核心模式：gate + hook 双驱动

两个开源集成的本质模式是：

```
BenefitGate → 平台 Classifier → Gate Profile → Hook 执行
                                                      ↓
                                                 enforce → block 工具
                                                 advisory → nudge 提示
                                                 native  → 透传
```

Gate Profile 包含：
- `block_native_read: bool` — 是否阻止原生读取
- `block_native_search: bool` — 是否阻止 grep 搜索
- `block_native_exec_dump: bool` — 是否阻止 cat/head 等
- `trajectory: str` — 推荐的 SRO 使用路径
- `prompt_style: str` — 提示风格

---

## 2. Claude Code 能力的重新评估

### 2.1 之前 `Claude集成方案.md` 的评估有误

经过实际查阅 Claude Code hooks 文档和社区实践（2025-2026），以下是修正后的能力评估：

| 能力 | 旧评估 | 新评估 | 依据 |
|------|--------|--------|------|
| **阻止工具执行** | ❌ preToolUse 不能 block | ✅ **exit code 2 = block** | MorphLLM 文档 + GitHub issues |
| **注入上下文** | ❌ 只有静态 CLAUDE.md | ✅ `additionalContext` 字段 | Claude Code v2.1.9+ |
| **修改工具输出** | ❌ 无 afterToolUse | ✅ `PostToolUse` + `updatedToolOutput` | 官方 hooks 文档 |
| **修改工具输入** | ✅ | ✅ `updatedInput` | 确认有效 |
| **改 tool_name** | 不确定 | ❌ **不能改** | updatedInput 只影响 tool_input 字段 |
| **注册自定义工具** | ⚠️ 仅 MCP | ✅ 仅 MCP（确认） | MCP 协议是唯一途径 |

### 2.2 修正后的 Claude Code Hook 能力全貌

```
PreToolUse Hook:
  ├─ permissionDecision: "allow" → 放行（可携带 updatedInput）
  ├─ permissionDecision: "deny"  → BLOCK（退出码 2）
  ├─ permissionDecision: "ask"   → 让用户决定
  ├─ permissionDecision: "defer" → 交给下一个 hook
  ├─ updatedInput: {...}         → 修改工具输入参数
  └─ additionalContext: "..."    → 注入额外上下文到 Claude

PostToolUse Hook:
  ├─ 可读取工具输出
  └─ updatedToolOutput: {...}    → 修改工具输出
```

**关键发现**：之前以为不能 block，但实际上使用 `permissionDecision: "deny"` 配合 exit code 2 可以**完全阻止**工具执行。同时 `additionalContext` 可以注入动态上下文，告诉 Claude 为什么被阻止以及应该用什么替代。

### 2.3 与 OpenClaw 的映射关系

| OpenClaw 能力 | Claude Code 等价方案 | 效果 |
|---------------|---------------------|------|
| `return { block: true }` | exit code 2 + `deny` | ✅ 等效 |
| `before_prompt_build` system context | `additionalContext` in PreToolUse | ⚠️ 较弱（只能单次注入） |
| `after_tool_call` | `PostToolUse` + `updatedToolOutput` | ⚠️ 只能改输出，不能做外部记录 |
| `llm_output` | ❌ 无等效方案 | 需要 MCP 工具自汇报 |
| `registerTool()` 原生工具 | MCP 工具 | ⚠️ 工具描述格式不同 |
| `tool.definition` 改描述 | MCP 工具 description | ✅ 等效（但格式受 MCP 限制） |

---

## 3. 新旧方案对比

### 3.1 旧方案（`Claude集成方案.md`）

```
MCP 提供能力 + CLAUDE.md 引导 + Hook 拦截 read_file
                                      ↓
                                只能改参数，不能 block
                                      ↓
                              Claude "可能"忽略 SRO 建议
```

### 3.2 新方案（优化后）

```
MCP 提供能力 + CLAUDE.md 引导 + Hook BLOCK + Hook 注入上下文
                                      ↓
                             exit code 2 = 强制阻止
                                      ↓
                             additionalContext = 告诉 Claude 用 SRO
                                      ↓
                             Claude "被迫"使用 SRO 工具
                                      ↓
                             PostToolUse 可改输出（追加 nudge）
```

### 3.3 关键改进点

| 维度 | 旧方案 | 新方案 |
|------|--------|--------|
| **阻止力** | 只能修改参数 | **可完全阻止（exit code 2）** |
| **动态上下文** | 只有静态 CLAUDE.md | **additionalContext 动态注入** |
| **输出修改** | 无 | **PostToolUse 可改输出 + nudge** |
| **等效 OpenClaw** | ~60% | **~85%** |

---

## 4. 优化后的集成架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        Claude Code                               │
│                                                                  │
│  ┌──────────────────────────┐  ┌──────────────────────────────┐ │
│  │ Layer 1: MCP Tools       │  │ Layer 3: PreToolUse Hook     │ │
│  │ (提供能力)               │  │ (强制保障)                   │ │
│  │                          │  │                              │ │
│  │ sro_preview(path)        │  │ read_file → 检测大小/类型    │ │
│  │ sro_read(target,mode)    │  │  ├─ 大文件 → exit 2 (block)  │ │
│  │ sro_card(path)           │  │  │          + additionalCtx  │ │
│  │ sro_raw(raw_ref)         │  │  ├─ 目录  → exit 2 (block)   │ │
│  │ sro_decide(path)         │  │  │          + additionalCtx  │ │
│  │                          │  │  └─ 小文件 → allow (放行)    │ │
│  └──────────────────────────┘  │                              │ │
│                                 │ Bash(cat/head) → 检测路径    │ │
│  ┌──────────────────────────┐  │  ├─ 大文件 → exit 2 (block)  │ │
│  │ Layer 2: CLAUDE.md       │  │  └─ 其他  → allow (放行)     │ │
│  │ (静态引导)               │  └──────────────────────────────┘ │
│  │                          │                                   │
│  │ "大文件必须走 SRO"       │  ┌──────────────────────────────┐ │
│  │ "PDF 必须走 SRO"         │  │ Layer 4: PostToolUse Hook    │ │
│  │ "代码文件直接读"         │  │ (可选增强)                   │ │
│  └──────────────────────────┘  │                              │ │
│                                 │ read_file 后: 检查是否大文件│ │
│  ┌──────────────────────────┐  │ 大文件输出 → updatedOutput   │ │
│  │ MCP Tool Descriptions    │  │ 添加 nudge                   │ │
│  │ (伪系统提示)             │  └──────────────────────────────┘ │
│  │                          │                                   │
│  │ "Use INSTEAD of read..." │  (核心在 nanobot-sro-v3/)        │
│  │ "PREVIEW large files"    │  ┌──────────────────────────────┐ │
│  └──────────────────────────┘  │ SparseRead Core (不变)       │ │
│                                 │ ├─ benefit_gate.py           │ │
│  ┌──────────────────────────┐  │ ├─ orchestrator.py           │ │
│  │ sparseread/bridge/claude │  │ ├─ readers/*                 │ │
│  │ (Claude 适配层, NEW)     │  │ └─ preview.py                │ │
│  └──────────────────────────┘  └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 四个集成层详解

### Layer 1: MCP Tools（第一优先级—提供能力）

**文件：** `sparseread/bridge/claude_mcp.py`（新建，约 200 行）

**策略：** 复用 `SparseReadBridgeServer`，包装成 MCP 协议

```python
"""SparseRead MCP Server for Claude Code."""
import argparse
from sparseread.bridge.claude import ClaudeBridge

class SparseReadClaudeMCP:
    """MCP wrapper — exposes SRO tools to Claude Code."""
    
    def __init__(self, workspace: str, mode: str = "auto"):
        self.bridge = ClaudeBridge(workspace=workspace, mode=mode)
    
    async def sro_preview(self, path: str = "") -> dict:
        """⚠️ IMPORTANT: PREVIEW large files, PDFs, and directories 
        BEFORE calling read_file. Returns structure, samples, signals, 
        and targeted evidence guidance. INSTEAD of read_file for:
        - PDF files of any size
        - Text files >12KB
        - Directories with 3+ files
        - LOG files >4KB"""
        return self.bridge.preview({"path": path})
    
    async def sro_read(self, target: dict, mode: str = "scout", 
                       hint: dict = None) -> dict:
        """TARGETED sparse evidence for large artifacts. 
        Call AFTER sro_preview when you need specific facts.
        Mode: scout=summary, focus=specific, collect=multi-slot.
        When result is 'ready', WRITE the deliverable — don't reread."""
        return self.bridge.read({"target": target, "mode": mode, 
                                 "hint": hint or {}})
    
    # ... sro_card, sro_raw, sro_decide, sro_status
```

**关键设计**：工具 description 就是"伪系统提示"。Claude 读取 MCP 工具描述时会把这些内容纳入决策。我们在 description 里写清楚"SRO 替代 read_file 的场景"，这比 CLAUDE.md 更直接。

**用户配置：**
```json
// .claude/settings.json 中 MCP 配置
{
  "mcpServers": {
    "sparseread": {
      "command": "uv",
      "args": ["--project", "/path/to/nanobot-sro-v3", 
               "run", "python", "-m", "sparseread.bridge.claude_mcp",
               "--workspace", "."],
      "env": { "SRO_ENABLED": "1" }
    }
  }
}
```

**注意 `uv` 的使用**：参照 `integrations/opencode/README.md` 的做法，使用 `uv run --project nanobot-sro-v3 python` 来确保 Python 3.11+ 和项目依赖。**不需要全局安装 Python 包**。

---

### Layer 2: CLAUDE.md（第二优先级—静态引导）

**策略：** 吸取 OpenClaw 的 `before_prompt_build` 注入内容，写成静态版本

```markdown
# SparseRead Project — File Reading Protocol

## ⚠️ 关键规则：大文件必须先预览

本仓库配置了 SparseRead (SRO) MCP 工具，用于高效读取大文件。

### 何时必须使用 SRO

| 文件类型 | 必须行为 |
|---------|---------|
| PDF 文件（任何大小） | **必须**先用 `sro_preview(path)`，再用 `sro_read` |
| 文本 >12KB | **必须**先用 `sro_preview(path)` |
| 目录（含多个文件） | **必须**先用 `sro_preview(path)` |
| Log 文件 >4KB | **建议**用 `sro_preview` |
| 代码文件 <4KB | 直接 `read_file`（正常操作） |

### SRO 使用流程

1. `sro_preview(path)` → 预览文件结构、样本、信号
2. 如果预览足够 → 直接回答用户问题
3. 如果需要更多证据 → `sro_read(target, mode, hint)` 
4. 如果 `sro_read` 返回 `ready` → 立即写交付物，不要重复读取
5. `sro_raw(raw_ref)` → 只在需要原始内容片段时使用

### 不要这样做 ❌

- 不要对大文件直接 `read_file`（会被 hook 阻止）
- 不要在 ready 后继续调用 `sro_read`（闭包证据已够）
- 不要对代码小文件用 SRO（原生读取更便宜）
```

---

### Layer 3: PreToolUse Hook（关键新增—强制拦截）

**文件：** `sparseread/hooks/claude_hook.py`（新建，约 300 行）

**核心策略：** 利用 **exit code 2 阻止工具** + **`additionalContext` 注入上下文**

```python
"""Claude Code PreToolUse Hook — SRO 强制拦截。

工作流程:
  1. 收到 PreToolUse 事件 (stdin JSON)
  2. 判断工具名和参数
  3. 如果是 read_file/Bash 操作大文件 → exit(2) 阻止 + additionalContext
  4. 小文件/代码文件 → allow 放行

配置 (claude.json):
{
  "preToolUse": {
    "command": "python",
    "args": ["path/to/claude_hook.py", "--workspace", "."]
  }
}
"""

import json, sys, os
from pathlib import Path

# 缓存避免重复判断（hook 进程在会话期间持续运行）
_CACHE = {}

# 快速启发式判断 — 不需要启动 Python SRO 库
def check_file(path: str):
    """文件级快速判断：是否该走 SRO。"""
    if path in _CACHE:
        return _CACHE[path]
    
    p = Path(path)
    if not p.exists():
        return None  # 不存在，透传
    
    suffix = p.suffix.lower()
    try:
        size = p.stat().st_size
    except OSError:
        return None

    # PDF → 强制 SRO
    if suffix == '.pdf':
        _CACHE[path] = 'enforce'
        return 'enforce'
    
    # 小代码/配置文件 → 放行
    if suffix in {'.py', '.rs', '.js', '.ts', '.go', '.c', 
                  '.h', '.sh', '.toml', '.yaml', '.yml', '.json'}:
        if size < 4096:
            _CACHE[path] = 'native'
            return 'native'
    
    # 大文本 → 强制 SRO
    if suffix in {'.md', '.txt', '.rst', '.log', '.csv'} and size > 12288:
        _CACHE[path] = 'enforce'
        return 'enforce'
    
    # 目录 → 看文件数量
    if p.is_dir():
        try:
            count = sum(1 for _ in p.iterdir())
        except OSError:
            count = 0
        if count > 3:
            _CACHE[path] = 'enforce'
            return 'enforce'
    
    return None

def handle_read_file(tool_input: dict) -> dict:
    path = tool_input.get("file_path", "")
    decision = check_file(path)
    
    if decision == 'enforce':
        # BLOCK + 注入上下文
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "SparseRead: file qualifies for sparse reading",
                "additionalContext": (
                    f"sparseread: This file is too large for native read. "
                    f"Use sro_preview(path={json.dumps(path)}) first, "
                    f"then call sro_read(target=..., mode=...) for targeted evidence. "
                    f"sro_preview returns structure, samples, signals, and next-action guidance."
                ),
            }
        }))
        sys.exit(2)  # BLOCK
    
    # 小文件 → 放行
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }))
    sys.exit(0)

def handle_bash(tool_input: dict) -> dict:
    """处理 cat/head/less/more 大文件 → BLOCK。"""
    command = tool_input.get("command", "")
    import re
    
    match = re.match(r'^(cat|head|less|more)\s+(\S+)', command.strip())
    if not match:
        # 非文件读取命令 → 放行
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }))
        sys.exit(0)
    
    path = match.group(2)
    decision = check_file(path)
    
    if decision == 'enforce':
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "SparseRead: use sro_preview instead of cat",
                "additionalContext": (
                    f"sparseread: Use sro_preview(path={json.dumps(path)}) "
                    f"instead of shell commands for large files."
                ),
            }
        }))
        sys.exit(2)
    
    # 放行
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }))
    sys.exit(0)

def main():
    input_data = json.load(sys.stdin)
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    
    if tool_name == "Read":
        handle_read_file(tool_input)
    elif tool_name == "Bash":
        handle_bash(tool_input)
    else:
        # 其他工具放行
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }))
        sys.exit(0)

if __name__ == "__main__":
    main()
```

**配置方式（`.claude/settings.local.json`）：**

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python",
            "args": ["~/.sro/hooks/claude_hook.py", "--workspace", "%CWD%"]
          }
        ]
      }
    ]
  }
}
```

### Layer 4: PostToolUse Hook（可选增强）

**策略：** 当 Claude 绕过了 hook 或者读了不该读的大文件时，在输出后追加 nudge

```python
"""Claude Code PostToolUse Hook — SRO nudge."""
import json, sys

def main():
    input_data = json.load(sys.stdin)
    tool_name = input_data.get("tool_name", "")
    tool_output = input_data.get("tool_output", {})
    
    if tool_name == "Read":
        path = tool_output.get("file_path", "")
        # 如果输出了大量内容 → 加 nudge
        output_text = json.dumps(tool_output)
        if len(output_text) > 5000:  # 粗略判断大文件
            updated = dict(tool_output)
            updated["_sro_nudge"] = (
                "This file was large. Consider using sro_preview for "
                "targeted reading next time."
            )
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "updatedToolOutput": updated,
                }
            }))
            sys.exit(0)
    
    # 透传
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse"}}))

if __name__ == "__main__":
    main()
```

---

## 6. 与 OpenClaw/OpenCode 的 Gate 对比

### 6.1 OpenClaw Gate Profile

```python
# integrations/openclaw 的 gate profile
{
    "mode": "enforce",
    "block_native_read": True,     # 插件可以 BLOCK read
    "block_native_search": True,   # 插件可以 BLOCK grep
    "block_native_exec_dump": True,# 插件可以 BLOCK cat
    "trajectory": "sro_first",
    "prompt_style": "sro_first",   # before_prompt_build 注入
}
```

### 6.2 Claude Code Gate Profile（新设计）

```python
# sparseread/bridge/claude.py 的 gate profile
{
    "mode": "enforce",
    "hook_can_block_read": True,       # PreToolUse exit(2) ✅
    "hook_can_block_bash": True,       # PreToolUse exit(2) ✅  
    "hook_can_inject_context": True,   # additionalContext ✅
    "hook_cannot_block_search": True,  # grep 在 Claude Code 中难以精准拦截 ⚠️
    "trajectory": "sro_first",
    "mcp_description_prompt": True,    # 通过 MCP description 引导
}
```

### 6.3 Gate 决策对比表

| 场景 | OpenClaw classifier | Claude classifier | 差异原因 |
|------|-------------------|-------------------|---------|
| PDF 读取 | `enforce + block_native_read=True` | `enforce + hook_can_block_read=True` | 等效（exit 2 = block） |
| 大文本 >12KB | `enforce + block_native_read=True` | `enforce + hook_can_block_read=True` | 等效 |
| 中等文本 | `advisory` | `advisory + additionalContext` | 略优（可注入上下文） |
| 代码小文件 | `native` | `native` | 一致 |
| grep 搜索 | `block_native_search=True` | `hook_cannot_block_search=True` | ⚠️ grep 拦截难度高 |
| bash cat/head | `block_native_exec_dump=True` | `hook_can_block_bash=True` | 等效（exit 2） |
| 系统提示注入 | `before_prompt_build` 动态注入 | `additionalContext` 按需注入 | ⚠️ 稍弱但可用 |
| 工具输出修改 | ❌ OpenClaw 没做 | `PostToolUse updatedToolOutput` | 可选增强 |
| Token 追踪 | `llm_output` 记录 | ❌ 无法实现 | 唯一硬缺失 |

**总体等效性：约 85%**。核心的 read_file 拦截、bash 命令拦截、动态上下文注入都能做。只有 token 用量追踪是真正的缺失。

---

## 7. 开源 vs 闭源的集成模式本质区别

### 7.1 三个项目的集成能力光谱

```
完全控制 ←──────────────────────────────→ 有限控制
   OpenClaw          OpenCode             Claude Code
   (开源插件)        (开源插件)           (闭源 CLI)
      |                 |                     |
      ▼                 ▼                     ▼
  registerTool()    tool({...})          MCP (仅 tool)
  before_tool_call  tool.execute.before  PreToolUse (stdin JSON)
  before_prompt_bld  (无)                CLAUDE.md + additionalContext
  after_tool_call    tool.execute.after  PostToolUse (可选)
  llm_output         (无)                (无法实现)
  完整生命周期        dispose             (有限)
```

### 7.2 本质区别

| 维度 | 开源（OpenClaw/OpenCode） | 闭源（Claude Code） |
|------|--------------------------|-------------------|
| **工具注册** | 原生 API，任意工具名、任意参数 | MCP 协议受限，工具名/描述/参数都有格式要求 |
| **拦截机制** | 框架层 API 调用（return/throw） | 进程退出码（exit code）+ JSON 响应 |
| **上下文注入** | 直接修改 system prompt | `additionalContext` 字符串追加 |
| **生命周期** | 完整的 agent_start/end/before/after | 只有 PreToolUse/PostToolUse |
| **可见性** | 可追踪所有工具调用 | 只能拦截到特定工具（通过 matcher） |
| **输出修改** | 直接修改返回对象 | `updatedToolOutput` 覆盖 |
| **状态持久** | 插件内可维护复杂状态 | hook 进程存活期间可维护状态 |

### 7.3 关键启示

**1. 闭源项目的集成不能靠"能力"，要靠"协议"**

OpenClaw 可以直接在 Agent 代码中注册工具和钩子。Claude Code 必须通过**标准协议**（MCP + Hook JSON）。这意味着：
- 工具描述要写得更有说服力（Claude 自主选择）
- Hook 的 JSON 协议要严格遵守
- 不能有任何运行时错误（没有调试手段）

**2. MCP 的 description 是"隐形系统提示"**

在 OpenClaw 中，工具 description 只用于文档。但在 Claude Code 中，MCP 工具 description **是 Claude 判断是否使用该工具的核心依据**。所以 description 要写得"有说服力"：
```
❌ "Preview a file with SparseRead"
✅ "PREVIEW large files (PDF, text>12KB, directories) INSTEAD of read_file.
    Returns structure, samples, evidence guidance. Use this for ALL files
    larger than 12KB or any PDF. Small code files can use read_file."
```

**3. Hook 脚本要"轻"**

OpenClaw 的 hook 运行在 Agent 进程中，共享内存和状态。Claude Code 的 hook 是独立子进程，每次触发都要启动。所以我们：
- 用**快速启发式判断**（文件名 + 大小 + 后缀，不启动 SRO 库）
- 缓存结果减少重复启动
- Python 启动慢的问题 → 第一阶段接受，第二阶段编译成 Rust/Go
- 对应 RTK 的做法：RTK 用 Rust 实现 hook 以确保 <10ms 启动

**4. blocking vs redirecting 的选择**

因为 Claude Code 的 `permissionDecision: "deny"` 能阻止工具，我们有两条路径：

| 策略 | 做法 | 效果 | 推荐场景 |
|------|------|------|---------|
| **Block** | exit(2) 阻止 + additionalContext 告诉用 SRO | Claude 被阻止 → 读上下文 → 主动调 MCP | PDF、大文件 |
| **Redirect** | allow + updatedInput 修改参数 | 工具执行但参数变了 | 缩小读数范围 |
| **Hybrid** | deny + additionalContext 含 `sro_preview` 建议 | 最强力 | 有 hook 的生产环境 |

**推荐 Hybrid 策略**：遇到大文件直接 block，Claude 看到 additionalContext 后会调用 MCP 的 SRO 工具。

---

## 8. 端口/访问约束的规避策略

用户担心"一些要用到的端口可能不对用户开放"，以下是各集成方式的端口分析：

### 8.1 端口需求矩阵

| 集成组件 | 需要端口？ | 风险 | 替代方案 |
|---------|-----------|------|---------|
| **MCP stdio 传输** | ❌ 不需要 | 无风险 | ✅ 默认方案 |
| **MCP SSE 传输** | ✅ HTTP 端口 | ⚠️ 可能被防火墙阻止 | ❌ 不使用 |
| **JSONL Bridge** | ❌ 不需要（子进程 stdio） | 无风险 | ✅ OpenClaw/OpenCode 已验证 |
| **PreToolUse Hook** | ❌ 不需要（stdin/stdout JSON） | 无风险 | ✅ |
| **Headroom Proxy** | ✅ HTTP 端口 | ⚠️ 端口不可用 / 需改 API endpoint | ❌ 不采用 |
| **HTTP API 包装** | ✅ 需要监听端口 | ⚠️ | ❌ 不采用 |

### 8.2 约束下的架构选择

```
约束：不能开端口
  ↓
MCP 传输层：只用 stdio（不使用 sse）
  ↓
Hook 通信：stdin/stdout JSON（不使用 HTTP callback）
  ↓
Bridge 进程：子进程 stdio 通信（不使用 TCP/HTTP）
  ↓
结论：全链路无端口需求 ✅
```

### 8.3 与现有集成的一致性

OpenClaw 和 OpenCode 的 bridge 也是通过 stdio JSONL 通信：

```bash
# openclaw/plugin/src/index.ts
this.process = spawn(command, args, {
    stdio: ["pipe", "pipe", "pipe"],  # ← stdio 通信
})

# opencode/plugin/sparseread.ts
this.process = spawn(command, args, {
    stdio: ["pipe", "pipe", "pipe"],  # ← 完全一样
})
```

Claude Code 的 MCP 和 hook 也走 stdio，所以**所有组件都使用 stdio，零端口需求**。

---

## 9. 实现计划

### Phase 1: MCP Server（1-2 天）

**文件：** `sparseread/bridge/claude.py` + `sparseread/bridge/claude_mcp.py`

**复用：**
- `SparseReadBridgeServer`（server.py）— 共享基类
- `classify_claude_gate()` — 新 classifier
- MCP 协议适配

**验证：**
```bash
# 启动 MCP server
uv run --project nanobot-sro-v3 python -m sparseread.bridge.claude_mcp --workspace .

# 在 Claude Code 中手动配置 MCP
# 问：看看这个项目的文档
# 预期：Claude 调用 sro_preview 而不是 read_file
```

### Phase 2: PreToolUse Hook（2-3 天）

**文件：** `sparseread/hooks/claude_hook.py`

**参考：**
- RTK 的 `src/hooks/hook_cmd.rs` — 命令解析 + 重写
- RTK 的 `detect_format()` — 多 Agent 格式识别

**核心逻辑：**
```python
# 关键决策链
read_file("big.pdf") 
→ hook 收到 PreToolUse 事件
→ check_file("big.pdf") → "enforce"
→ exit(2) + additionalContext("Use sro_preview")
→ Claude 被阻止 + 看到 SRO 建议
→ Claude 调用 sro_preview("big.pdf") ← MCP 工具
→ SRO 返回预览
```

### Phase 3: CLAUDE.md 模板 + Install 脚本（1 天）

**参考** OpenClaw 的 `before_prompt_build` 注入内容，写出 CLAUDE.md 模板。

**Install 脚本逻辑：**
```python
def install_claude():
    """配置 Claude Code 使用 SRO。"""
    # 1. 添加 MCP 配置到 .claude/settings.json
    # 2. 添加 PreToolUse hook 配置
    # 3. 创建/追加 CLAUDE.md
    # 4. 验证安装
```

### Phase 4: 评测与 Token 节省量化（2-3 天）

**参考：** OpenClaw 的 `run_openclaw_validation.py`

**测量指标：**
- 无 SRO：Claude Code 直接读所有文件
- 有 SRO：Claude Code 通过 MCP 走 SRO
- 对比：token 消耗、任务完成率、执行步数

---

## 10. 与 OpenClaw/OpenCode 代码复用策略

### 10.1 共享模块（不重复造轮子）

```
nanobot-sro-v3/sparseread/
├── bridge/
│   ├── __init__.py
│   ├── server.py           ← 共享：SparseReadBridgeServer 基类（已存在）
│   ├── openclaw.py         ← 已有：OpenClaw classifier（不动）
│   ├── opencode.py         ← 已有：OpenCode classifier（不动）
│   ├── claude.py           ← 新建：Claude Code classifier
│   └── claude_mcp.py       ← 新建：MCP 协议适配
├── hooks/
│   ├── __init__.py         ← 新建
│   └── claude_hook.py      ← 新建：preToolUse hook
```

### 10.2 Claude Bridge 的复用模式

```python
# sparseread/bridge/claude.py
"""Claude Code SparseRead JSONL bridge + MCP adapter."""

from sparseread.bridge.server import BridgePolicy, SparseReadBridgeServer, serve_bridge

class ClaudeBridge(SparseReadBridgeServer):
    def __init__(self, *, workspace, mode="auto"):
        super().__init__(
            workspace=workspace,
            mode=mode,
            classifier=classify_claude_gate,  # ← 新 classifier
            policy=BridgePolicy(
                platform="ClaudeCode",
                gate_key="claude_gate",
                ready_guard="claude_adapter_ready_once",
                allow_bounded_text_verify=True,
                guard_cards_after_ready=True,
            ),
        )
```

### 10.3 与 OpenCode 的"无原生插件"策略对接

OpenCode 集成可以用原生插件（因为开源）。Claude Code 没有原生插件，所以所有交互都通过 MCP。但 bridge 层的复用是完全一致的：

```
                  ┌──────────────────┐
                  │ SparseRead Core  │
                  │ (benefit_gate,   │
                  │  orchestrator,   │
                  │  readers,        │
                  │  preview)        │
                  └────────┬─────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
┌─────────────────┐ ┌──────────────┐ ┌──────────────┐
│ OpenClaw Bridge │ │ OpenCode Br. │ │ Claude Br.   │
│ (JSONL)         │ │ (JSONL)      │ │ (JSONL+MCP)  │
└────────┬────────┘ └──────┬───────┘ └──────┬───────┘
         │                 │                │
         ▼                 ▼                ▼
┌─────────────────┐ ┌──────────────┐ ┌──────────────┐
│ OpenClaw Plugin │ │ OpenCode Pl. │ │ MCP Server   │
│ (TS, register   │ │ (TS, tool.)  │ │ (MCP 协议)   │
│  + lifecycle)   │ │              │ │              │
└─────────────────┘ └──────────────┘ └──────┬───────┘
                                           │
                                           ▼
                                    ┌──────────────┐
                                    │ Claude Code   │
                                    │ (闭源 CLI)    │
                                    │              │
                                    │ + PreToolUse │
                                    │ + CLAUDE.md  │
                                    └──────────────┘
```

---

## 附录 A：与 OpenClaw/OpenCode 集成代码的直接对照

### A.1 工具注册对比

**OpenClaw（原生注册）：**
```typescript
api.registerTool({
    name: "sro_preview",
    description: "Production SparseRead entrypoint...",
    parameters: Type.Object({...}),
    async execute(id, params, ctx) { ... }
})
```

**Claude Code（MCP 注册）：**
```python
@mcp.tool()
async def sro_preview(self, path: str = "") -> dict:
    """Production SparseRead entrypoint. ..."""
    return self.bridge.preview({"path": path})
```

两者语义等价，只是格式不同。

### A.2 Hook 拦截对比

**OpenClaw（API 调用）：**
```typescript
api.on("before_tool_call", async (event, ctx) => {
    if (gate?.block_native_read === true) {
        return { block: true, blockReason: "..." }
    }
})
```

**Claude Code（进程通信）：**
```python
# 通过 stdin/stdout JSON + exit code
if decision == 'enforce':
    print(json.dumps({
        "hookSpecificOutput": {
            "permissionDecision": "deny",
            "additionalContext": "Use sro_preview..."
        }
    }))
    sys.exit(2)  # ← 等同 block: true
```

两者效果相同，但 Claude Code 通过进程退出码实现。

### A.3 系统提示注入对比

**OpenClaw（动态注入）：**
```typescript
api.on("before_prompt_build", async (event, ctx) => {
    return {
        appendSystemContext: "SparseRead is available..." // 每次动态注入
    }
})
```

**Claude Code（静态 + 动态）：**
```markdown
# CLAUDE.md（静态，每次会话加载）
"本仓库使用 SparseRead 优化文件读取..."
```

```python
# PreToolUse additionalContext（动态，按需注入）
"additionalContext": "sparseread: Use sro_preview..."
```

CLAUDE.md 提供全局引导，`additionalContext` 提供具体场景的精确指引。

---

## 附录 B：已知限制与未来改进

| 限制 | 影响 | 未来改进方向 |
|------|------|-------------|
| Hook Python 启动慢（~100ms） | 每 次工具调用增加延迟 | Rust/Go 编译版（参考 RTK <10ms） |
| 无法追踪 token 用量 | 缺少量化数据 | MCP 工具自汇报 + 外部脚本聚合 |
| 无法追踪非拦截工具 | 不知道 Claude 调用了哪些工具 | 无解（闭源限制） |
| `additionalContext` 长度限制 | 不能注入过多内容 | 保持简洁，指向 CLAUDE.md |
| MCP 工具无优先级排序 | Claude 可能需要多轮学习才能正确选择 | 精心设计工具 description |
| Hook 配置需手动修改 settings.json | 增加用户门槛 | `sro install claude` 一键安装脚本 |
