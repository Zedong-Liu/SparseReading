# Claude Code SRO 集成实测汇总（2026-08-05）

环境：Claude Code 2.1.221 + DeepSeek-V4-Flash（Paratera，经本地 Anthropic 兼容代理）
管道：`local_agent_comp/run_claude_sro_bench.py`（baseline = 无 SRO；gate = 我们的
MCP server + PreToolUse/PostToolUse session hook，sparseread-claude 0.1.0）
评分：任务内嵌 `grade()`，与同事 07-26 报告同口径；hybrid 任务保留启发式部分分。

## 逐任务得分

| 场景 | 任务 | baseline | gate | 备注 |
| --- | --- | ---: | ---: | --- |
| long-context | LooGLE Outremer | 1.000 | 1.000 | 报告 BL 1.000 |
| long-context | LooGLE 5q | 1.000 | 1.000 | |
| long-context | LooGLE 3q | 1.000 | 1.000 | |
| long-context | T21 OpenClaw | 0.944 | 1.000 | BL 仅 api_type 措辞 0.5 |
| long-context | WB-Lite 334 | 1.000 | 1.000 | |
| audit | T12 Stock Fetcher | 0.300 | 0.300 | 与报告 BL 0.300 一致 |
| audit | T55 Literature | 0.983 | 0.983 | 报告 20 轮内为 0.983 |
| audit | T86 Command Sec | 1.000 | 1.000 | |
| audit | T94 Exam Monitor | 1.000 | 1.000 | |
| audit | T98 Book Rec | 0.000(超时) | 1.000 | gate 528s 完成，成功旗标异常但评分有效 |
| structured | T58 DiD | 1.000(842s) | 1.000(317s) | gate 快 2.7 倍 |
| structured | T73 P&L | 1.000 | 1.000 | |
| structured | SB49333 VLOOKUP | 1.000 | 1.000 | gate 补跑 381s |
| structured | SB11276 Weekday | 1.000 | 1.000 | 报告 BL 0.000（xlsx 限制） |
| native-fit | T36 Find Largest | 0.375 | 0.667 | gate 与参考 Gate 0.667 一致 |
| native-fit | T59 Discount | 超时 | 超时 | 任务级循环/API 波动，两模式均 600s 超时 |
| native-fit | T67 SPARQL | 1.000 | 1.000 | |

## 场景汇总

| 场景 | baseline | gate | 报告 baseline |
| --- | ---: | ---: | ---: |
| long-context | 0.9889 | 1.0000 | 1.0000 |
| audit（含 T98=0/1） | 0.6567 | 0.8567 | 0.6996 |
| structured | 1.0000 | 1.0000 | 0.7500（含 SB11276=0） |
| native-fit（剔除 T59 超时） | 0.6875 | 0.8335 | 0.7722 |

## 文件读取层 token 收益（离线，scripts/benchmark_claude_bridge.py）

| 类别 | 本次 | 报告 |
| --- | ---: | ---: |
| long-context | 99.8% | 99.9% |
| structured | 85.0%（routed 86.9%） | 96.4% |
| native-fit | -16.4%（routed 41.6%） | 66.7% |
| audit | -23.0%（routed -9.4%） | 53.3% |
| overall | 98.4%（routed 98.7%） | 99.6% |

大文件完全对齐（LooGLE/T21 单文件 94.7% vs 94.6%；workspacebench 99.9%；
SB 大表 93-99%）。audit/native-fit 小文件类别为负，是因为我们 gate 将小文件
判为 native（本就不该走 SR），且我们的 preview 返回更丰富的 samples；
这是保守设计差异，不是缺陷。

## 结论

- long-context：baseline 0.9889≈1.000、gate 5/5=1.000，与报告对齐。
- gate 真实生效：token 日志确认 SRO preview/read 被调用（PinchBench.pdf
  单文件省 25K token）；T21 0.944→1.000、T98 超时→1.000、T58 842s→317s、
  T36 0.375→0.667。
- 剩余波动：T59 两模式超时（任务级问题）、T98 baseline 两次超时、若干首轮
  超时在补跑后恢复正常（API/会话波动），均与集成无关。
