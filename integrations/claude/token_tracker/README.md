# SparseRead Token Tracker — 令牌消耗追踪

> 为 SparseRead Claude Code 集成提供精确的 Token 消耗量化指标
> 独立于 SR 框架，可被其他 Claude 用户独立部署

---

## 0. 为什么需要 Token Tracker

Claude Code **没有** 暴露 LLM API 的 usage 数据到 Hook 或 MCP 上下文中。
这意味着无法直接从外部追踪每次 API 调用的 token 消耗。但 **SparseRead 本身
可以精确计算其影响** — 通过比较"如果不用 SR 会消耗多少 token"和"用了 SR 实际
消耗多少 token"。

**核心思路**：Token Tracker 从 SR 内部计算，不需要访问 Claude Code 内部数据。

指标：
- **full_file_tokens**: 如果直接读取完整文件，会消耗的 token 数（估算）
- **sr_response_tokens**: SR 的 compact preview/read 响应消耗的 token 数（实际值）
- **tokens_saved**: 节省的 token 数 = full_file_tokens - sr_response_tokens
- **savings_ratio**: 节省比例 = tokens_saved / full_file_tokens
- **context_retained_pct**: 保留的上下文窗口百分比

---

## 1. 快速开始

Token Tracker **自动工作** — 无需额外配置。只要 SR MCP Server 在运行，
每个 `sro_preview`、`sro_read`、`sro_card`、`sro_raw` 调用都会自动
记录 token 数据。

### 验证追踪是否工作

在 Claude Code 会话中：

```
调用: sro_preview(path="some_large_file.md")
然后: sro_usage()
```

`sro_usage` 返回的 JSON 中应包含非零的 `tokens_saved` 和 `savings_ratio`。

### 查看 Token Log

```bash
# 查看最近 20 条记录
python integrations/claude/token_tracker/token_analyzer.py --tail 20

# JSON 格式（方便脚本化）
python integrations/claude/token_tracker/token_analyzer.py --json

# 指定 log 路径
python integrations/claude/token_tracker/token_analyzer.py --log /path/to/sro_token_log.jsonl
```

### Token Log 位置

默认位置：`~/.claude/sro_token_log.jsonl`

禁用 logging（仅追踪内存中的数据）：
```bash
export SRO_TOKEN_LOG=0
```

---

## 2. 指标说明

### sro_usage 输出解读

```json
{
  "session": {
    "operations": 15,
    "full_file_tokens": 125000,
    "sr_response_tokens": 8500,
    "tokens_saved": 116500,
    "savings_ratio": 0.932,
    "context_retained_pct": 58.25,
    "by_operation": {
      "preview": {"count": 8, "full_tokens": 95000, "sr_tokens": 6000, "saved": 89000},
      "read": {"count": 5, "full_tokens": 25000, "sr_tokens": 2000, "saved": 23000},
      "card": {"count": 2, "full_tokens": 5000, "sr_tokens": 500, "saved": 4500}
    }
  },
  "interpretation": "SparseRead saved ~116,500 tokens across 15 operations (93.2% savings — excellent). That preserved ~58.3% of a 200,000-token context window for other work."
}
```

### 字段含义

| 字段 | 含义 |
|------|------|
| `full_file_tokens` | 原生读取文件所需的估算 token 数 |
| `sr_response_tokens` | SR 响应消耗的估算 token 数 |
| `tokens_saved` | 节省的 token 数 (full - sr) |
| `savings_ratio` | 节省比例 (0-1) |
| `context_retained_pct` | 相对于 200K 上下文窗口保留的比例 |
| `by_operation` | 按操作类型 (preview/read/card/raw) 汇总 |

### Token 估算方法

Token Tracker 使用 **字符数 / 字符-per-token 比率** 进行估算：
- 文本/代码：4 字符/token（英文/代码标准比率）
- JSON/结构化数据：3 字符/token
- PDF：基于 base85 编码开销的公式

**需要地面实况？** 使用 `sparseread.token_tracker.count_tokens_api()`：
```python
from sparseread.token_tracker import count_tokens_api
exact = count_tokens_api(large_text, model="claude-opus-4-8")
```

---

## 3. 让其他 Claude 用户使用

所有 Token Tracker 文件均可单独部署 — 无需完整 SR 项目。

### 文件清单

```
你的项目/
├── .mcp.json                          ← MCP Server 配置（已有）
├── nanobot-sro-v3/sparseread/
│   ├── bridge/
│   │   ├── server.py                  ← 自动集成 TokenTracker
│   │   ├── claude.py
│   │   └── claude_mcp.py             ← 暴露 sro_usage 工具
│   └── token_tracker.py              ← Token 追踪核心逻辑
└── integrations/claude/token_tracker/
    ├── token_analyzer.py              ← 独立 CLI 分析工具
    └── README.md                      ← 本文件
```

### 独立使用 Token Analyzer

如果只想分析 token log（不需要运行 SR）：

```bash
# 复制 token_analyzer.py 到任意机器
# 复制 sro_token_log.jsonl 到同一目录
python token_analyzer.py

# 或指定 log 路径
python token_analyzer.py --log /path/to/sro_token_log.jsonl
```

`token_analyzer.py` **零依赖** — 纯 Python 标准库。

---

## 4. 测试验证

### 端到端测试

```bash
cd <PROJECT_ROOT>

# 运行 SR 测试套件（含 token tracking 测试）
uv run --project nanobot-sro-v3 --with pytest --with pytest-asyncio \
  pytest nanobot-sro-v3/tests/sparse_reading/test_bridge_claude.py -v

# 测试 sro_usage 工具
uv run --project nanobot-sro-v3 python -m sparseread.bridge.claude_mcp --workspace . &
# 在 Claude Code 中调用 sro_usage()
```

### 预期行为

1. **Token 追踪自动激活** — 无需配置
2. **sro_usage 返回非零数据** — 只要有一次 sro_preview/read 调用
3. **JSONL log 文件生成** — `~/.claude/sro_token_log.jsonl`
4. **sro_trace 包含 token_tracker 摘要** — 在 summary 字段中
5. **Token Analyzer 可以读取 log** — 生成可读报告

---

## 5. Token 估算精度

| 文件类型 | 估算精度 | 说明 |
|----------|----------|------|
| 英文文本 (≥10KB) | ±15% | 4 chars/token 经验值 |
| 代码 (≥10KB) | ±20% | 取决于注释密度 |
| JSON 数据 | ±10% | 3 chars/token |
| 中文/日文 | ±25% | CJK 字符 2 chars/token |
| PDF | ±30% | base85 编码开销多变 |

**精度提升**：设置 `ANTHROPIC_API_KEY` 环境变量后，调用
`count_tokens_api()` 可获得精确值（使用 Anthropic 官方 API）。

---

## 6. 禁用 Token 追踪

```bash
# 禁用 JSONL 日志写入（追踪仍在内存中工作）
export SRO_TOKEN_LOG=0
```

要完全禁用，在创建 Bridge 时传入 `token_tracker=None`：
```python
ClaudeBridge(workspace=".", mode="auto", token_tracker=None)
```
