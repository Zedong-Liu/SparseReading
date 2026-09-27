# Codex / Pi 本地真实记录回放

日期：2026-09-28。发布版本：v0.1.2。宿主：Codex CLI 0.158.0-alpha.2、
Pi 0.87.1、Node 24.15.0、Python 3.12。

## 方法与隐私

从用户已有工作区选取运行手册、开发日志、移植总结、历史故障日志和会话 JSONL，
复制到隔离临时项目。在 Codex 实际 app-server 加载的 MCP 服务，以及 Pi SDK
实际加载的已安装源码扩展中回放同一组工具请求，不直接调用源码中的 reader。
两端 Python 都来自安装器生成的 wheel runtime。

没有调用外部模型；记录正文、完整工具 trace、问题期望值和个人路径不进入
公开仓库或发布附件。公开 JSON 仅有不含隐私的场景标签、大小、耗时和通过标志。
原工作区只读，不在原工作区安装插件或写测试缓存。

这是确定性的宿主工具/证据回放，不是自主 agent 端到端回答质量评测。定向查询
和一次 follow-up 在回放前固定；原文核对范围由本地源文件计算，属于诊断 oracle，
不是 agent 自主找到锚点的成绩。下表字符比例不能等同于模型 token 或总耗时收益。

## 场景结果

两端路由、最终证据命中和原文回取均通过，输出证据大小一致。

| 真实用户场景 | 原文件大小（字节） | 返回证据（字符） | 占原文字符比例 | 结果 |
| --- | ---: | ---: | ---: | --- |
| 从运行手册查 API 使用政策 | 45,807 | 3,103 | 6.81% | 单次 focus 找到目标信息；原文一致 |
| 从长开发记录定位超时原因及修复 | 333,432 | 6,176 | 1.89% | 首次 focus 不完整；预先定义的 refine 后找全；原文一致 |
| 从移植总结找旧插件缓存问题 | 8,578 | 2,002 | 23.34% | 找到刷新要求；原文一致 |
| 从故障日志找失败、重试与告警结果 | 4,612 | 2,670 | 57.92% | 三项故障证据找到；原文一致 |
| 精确读取历史会话 JSONL | 91,095 | 不适用 | 不适用 | 核心不支持该 reader，正确 native；Pi 原生 read 可执行 |

各 sparse 场景包含 preview → focus →（需要时 refine）→ bounded raw verification。
四个已绑定对象和 23 个读取事件均出现在各自 `sro_trace` 中。JSONL 没有被包装
成稀疏读取成功；native 场景的 `evidence_pass/raw_pass` 是 null。
逐项耗时见 [Codex 汇总](../results/host-replay-v012-codex.json) 和
[Pi 汇总](../results/host-replay-v012-pi.json)。这些是一次本机样本，包含 lazy bridge
启动成本，没有重复样本或统计显著性结论。

## 额外恢复与安全场景

- 两端：全量保真需求 native；切换回新定向任务恢复 force_sro，没有旧模式泄漏。
- 两端：大文件 broad read 只转向一次；重试及有界读取保持原生；项目外路径回退。
- Codex：通过已安装 Node launcher 的标准 Hook 输入输出核对 deny/重试/有界路径。
  这是 Hook 进程契约测试，不是 UI 信任流程或模型生成命令的端到端测试。
- Pi：实际扩展 native tool_call 回调与 SDK 原生 read 执行通过；JSONL 不拦截；
  session_start 关闭旧桥接、清空 artifacts；旧 raw_ref 失败，重新 preview 后可读。
- 两端：旧 raw_ref 返回真正的失败工具信号，不能被当作成功取证。

## 发现与修复

1. **旧引用被标成成功**：核心在 raw.error 中返回协议失败；原 Codex MCP
   isError=False，Pi 也可能返回成功内容。先写失败回归，再在 HostBridge/宿主
   包装层映射为工具错误，并保留重新 preview / native 指引。核心实现不变。
2. **原文范围描述错误**：Codex 声称 byte range，但核心实际按 Unicode 字符
   切片。修正两端工具说明，补中文/emoji 切片回归；明确 exclusive end、50000
   默认上限与 truncated。未修改核心切片行为。
3. **Pi 重装信任拒绝缺少安装器指引**：真实重装触发 Project is not trusted。
   新增清楚的审核后重跑说明和显式 `--pi-approve`，不自动传 --approve。隔离
   测试 home 中的项目级批准重装与 doctor 通过，不修改个人永久信任。
4. **长日志首次 focus 漏项**：保留失败记录，不改 reader 或阈值来迎合样本。
   通过原协议的 refine 找回证据；文档明确 focus 不是完整答案、原文核对仍重要。

## 复现

先按安装文档把两端安装到隔离项目，准备私有 cases JSON（不提交），字段：
id（无隐私标签）、path（项目内路径）、goal、needles、expected、route；需要
多轮时可添加预先定义的 followup_hint。expected 只供本地验证，不传给 reader。

```bash
uv run python scripts/replay_host_records.py --host codex \
  --workspace /absolute/test-project --cases /absolute/private-cases.json \
  --output /absolute/codex-metrics.json

# 测试依赖只安装在源码环境，不需要装进用户的 Pi 扩展包。
npm --prefix integrations/pi/package ci --ignore-scripts
uv run python scripts/replay_host_records.py --host pi \
  --workspace /absolute/test-project --cases /absolute/private-cases.json \
  --output /absolute/pi-metrics.json
```

Codex 需在隔离 CODEX_HOME 中显式信任测试项目；Pi 测试目录与个人 agent home
分离。当前恢复检查要求用例中包含 force_sro 文件和一个 native JSONL 文件。
只复现发布后安装时，下载 source-installer 即可；回放脚本/测试依赖在完整源码
中，不在精简用户安装包中。

## 边界

没有真实 Windows agent 宿主、没有旧版宿主兼容验证，也没有付费模型 A/B。
JSONL 不支持稀疏 reader 是当前核心边界，不为本次框架适配新增 reader。
OpenClaw 开发 SDK 的 npm audit 报告不为零；生产依赖 `--omit=dev` 审计为零。
本次没有修改这个既有框架的算法或依赖集合。
