# Codex / Pi 验收记录

日期：2026-09-27。基线：`e1e0f36`。工作分支：`codex/codex-pi-adapters`。

## 已验证

| 检查 | 结果 |
| --- | --- |
| Python 全量测试（含已有 adapters） | 225 passed |
| Pi typecheck / build / 全量测试，启用真实 runtime smoke | Node22.23.3 18 passed，无跳过；此前 Node24 17 passed |
| Codex manifest validator / skill validator / Node syntax | 通过 |
| Codex CLI 0.158.0-alpha.2 实际 app-server | 发现插件、skill、hook；加载六个 MCP 工具；成功调用 sro_trace |
| Codex 同一隔离 home 重装 auto → advisory | 新本地版本加载，未沿用旧缓存 |
| Pi 0.87.1 官方 install --local 与 SDK loader | 源码资源包无 node_modules 仍可加载；六工具注册；preview → raw 成功 |
| wheel / npm 包边界 | host transport wheel 构建通过；Pi 包只含 src/README/package metadata |
| 核心改动检查 | packages/sparseread-core 无差异 |

两个实现子 agent 和独立审核 agent 均使用 `gpt-6-luna / max`。实现与审核角色
分离；发现问题由主 agent 修复。审核结论见 [独立审核报告](codex-pi-review.md)。

## 验证中修复的问题

- Codex MCP 参数中的 plugin-root 占位符在实际宿主未解析：安装器改为解析后的
  Node 与 managed launcher 路径。
- Codex 按同一版本复用旧缓存：本地安装附加 build 标识，重装/切换模式可更新。
- Pi 流式 JSONL 在中文/emoji 字符中间分块时损坏内容：先写逐字节回归测试，
  复现乱码，再用每个子进程独立的 UTF-8 decoder 修复。
- Pi 子进程 stdin 断管可能产生未处理 error：先复现 EPIPE，再加入 stream
  错误处理，让待处理调用失败并保留原生恢复路径。
- Pi 从项目子目录启动时，原生 read 的相对路径必须以宿主 cwd 解析：先复现
  错位，再将原生候选路径转成绝对路径；SRO 工具保持明确的 workspace-relative
  参数契约。
- Node22 下等待中的 cleanup/request deadline 不应 unref：实测出现事件循环
  提前结束、未完成调用的测试被取消；保留 timer reference 后全量通过。
- 独立审核复现安装目标符号链接导致项目外覆盖（P2）：新增双方框架的父目录、
  插件根目录、子目录及配置目录回归，安装器在构建前拒绝这些链接。
- 两端重复转向可能阻止完整读取：每个会话/目标只转向一次，重试可原生继续。

## 未声称已验证

没有调用付费模型、没有新的质量/token/延迟 benchmark；不声称达到论文中其他
框架的收益。没有修改用户个人配置、没有自动授予项目/插件/hook 信任。
实际宿主验证运行于 macOS、Node24/Node22.23.3、Python3.12；已配置 Linux/Node22 CI 检查，
但本阶段尚未在远端执行该 CI；
本地尚未运行真实 Windows 宿主，也未对旧版 Codex/Pi 做兼容性承诺。
Codex hook 信任的 UI 流程保留为用户操作；测试只验证定义发现与标准输入输出。
本阶段未发布 npm/PyPI 包或更新既有 GitHub release。
