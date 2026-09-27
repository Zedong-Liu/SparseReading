# Codex / Pi 适配设计与使用

## 结论与边界

两端复用同一个 `sparseread-core`，只新增框架层。核心目录、reader、gate
阈值、EvidencePack、episode 和 raw_ref 协议都不修改；不引入第二套摘要算法。
现有 Claude Code 集成也不改动。新增适配不是论文效果的复测，不能直接沿用
其他框架的 token/耗时收益作为 Codex/Pi 的实测结论。

| 层 | Codex | Pi |
| --- | --- | --- |
| 安装形态 | 本地项目 marketplace + 标准插件 | `pi-package` + 官方项目安装命令 |
| 工具 transport | 常驻 stdio MCP | 懒启动、常驻 Python JSONL 子进程 |
| 主入口 | `sro_preview`，随后定向 `sro_read` | 同名工具、同一核心协议 |
| 原生读取适配 | PreToolUse，保守识别单文件整读 | tool_call，识别无 offset/limit 的 read |
| 失败恢复 | 不拦截；重复读取可以原生继续 | 桥接错误原生放行，工具错误明确提示 |
| 信任边界 | 项目信任与 hook 信任分别确认 | 用户审核后批准项目扩展 |

### 为什么选择这些接口

[Codex 官方插件文档](https://developers.openai.com/plugins/build/plugins)
支持兼容 manifest、插件内 MCP、skill，以及项目 marketplace。项目启用配置
只在受信任项目生效。插件会进入宿主缓存，因此启动器从**安装后的插件根目录**
读取 `.sparseread-runtime.json`，绝不能从源码父目录寻找 Python 包。

[Codex hooks 文档](https://learn.chatgpt.com/docs/hooks)支持 PreToolUse 返回
deny/advisory context，但安装/启用插件不等于信任其 hook。只拦截可明确识别的
整文件读取，不修改 shell command、不授予权限、不接管全部执行输出；有范围的
sed/head/tail、搜索、管道/复合命令均走原生。此 hook 不是安全沙箱，也不是所有
文件访问的全面拦截器。

[Pi 扩展文档](https://pi.dev/docs/latest/extensions)提供工具注册、tool_call、
会话事件和生命周期清理；当前实现按 `@earendil-works/pi-coding-agent` API
验证，不沿用旧 `@mariozechner` 包名。按照
[Pi 包文档](https://pi.dev/docs/latest/packages)声明 TS 扩展资源和宿主 peer
dependencies，避免打包第二份 agent/runtime。安装器使用官方 `pi install --local`
合并项目资源配置，而不覆盖用户 settings。

## 一次安装

前提：Python 3.11+、uv、Node.js，以及对应 CLI。PDF/XLSX reader 默认安装；
纯文本可加 `--reader-extras none`。只需下载一次仓库，然后任选一端：

本次宿主验证版本：Codex CLI `0.158.0-alpha.2`，Pi `0.87.1`。旧 Codex 必须
具备插件/hook 接口；旧 Pi SDK 不应假设支持当前 `typebox` 和扩展 API。未验证
旧版本兼容性，建议先更新到具备这些接口的版本。

```sh
git clone --branch v0.1.2 https://github.com/Zedong-Liu/SparseReading.git
cd SparseReading

uv run --python 3.12 python scripts/install_sparseread.py \
  --platform codex --workspace /absolute/path/to/project --doctor

uv run --python 3.12 python scripts/install_sparseread.py \
  --platform pi --workspace /absolute/path/to/project --doctor
```

两个扩展随 `v0.1.2` 发布。也可以从 Release 下载
`sparseread-source-installer-v0.1.2.zip` 或 `.tar.gz`，解压到 `sparseread-v0.1.2`
后运行相同命令。先用发布的 `SHA256SUMS` 核对下载内容。
`v0.1.1` 的旧安装器不支持这两个新 platform。

若 Pi 重装时报 `Project is not trusted`，先审核目标项目的文件和扩展，然后
显式加 `--pi-approve` 重跑安装命令。它仅为本次 Pi 安装命令提供项目批准，
不自动改变永久信任、不开启全局绕过；后续宿主启动仍可能要求审核。
安装器不会自动添加这个参数。

安装到目标项目的 `.sparseread/<host>/`：独立 wheel runtime、插件和绝对路径配置。
Codex 合并 `.agents/plugins/marketplace.json`，追加 `.codex/config.toml` 对应
插件表，不改用户个人配置、不覆写其他条目。已有 disabled 设置保留。
Pi 调用本地包安装命令，注册 `.sparseread/pi/sparseread-pi`。
新增目录应放入目标项目 `.gitignore`；runtime 不适合提交或复制到其他机器。
移动项目/更换电脑后重新安装即可。失败构建不会替换上一次可用 runtime；旧
runtime generation 保留用于回退，不会自动删除用户文件。

Codex MCP 配置不能当作 shell 使用：安装器写入已解析的 Node/启动器路径。
Codex 还会按版本缓存插件；每次本地安装附加 `+local.<id>` 构建标识，确保重装
或切换 advisory 时不会沿用旧缓存。这个标识不改变公开发布版本，不自动发布
npm/PyPI，也不修改已发布的 GitHub release。

## 真实记录读取注意事项

`focus` 返回的是相关证据，不承诺回答所有问题；长开发记录的宽泛查询可能
漏掉修复细节。沿同一 `artifact_id` 做定向 `refine`，或按 anchor/selector
原文核对。`sro_raw.range` 是从 0 开始的 Unicode 字符偏移，end 不包含在结果中，
不是字节或行号；默认最多 50000 字符，务必检查 `truncated`。
文件 selector 匹配原文行，目录 selector 选择子文件；旧会话的 raw_ref 必须
重新 preview。JSONL 在当前核心中不受稀疏 reader 支持，保持原生工具路径。

见 [本地真实记录回放报告](codex-pi-real-records.md) 和
[v0.1.2 独立复核](codex-pi-v012-review.md)。

Codex：在目标项目重启，确认信任项目；在插件 UI 中审核并信任 SparseRead 的
hook。Pi：重启或 `/reload`，审核后批准项目扩展。**安装器不自动授予信任。**
没有 hook 信任时，Codex MCP/skill 与自动读取引导的可用性必须分开判断。

可先预览变更，再选择仅提示模式：

```sh
uv run --python 3.12 python scripts/install_sparseread.py \
  --platform codex --workspace /absolute/path/to/project --dry-run
uv run --python 3.12 python scripts/install_sparseread.py \
  --platform pi --workspace /absolute/path/to/project --sparseread-mode advisory
```

`--doctor-only` 只检查已安装 transport，不修改配置、不调用模型、不扫描整个工作区。
它不是“用户已信任插件/hook”的证明。

## 使用与回退

给 agent 一个正常任务，例如“提取 reports/incident.md 的根因、负责人、截止时间”。
工作流是 preview → 目标明确的 read → 根据 next_action 停读/写交付物。需要精确
统计、编辑或完整保真时，遵循核心给出的 native/compute 路径；内容不足时调用
`sro_raw`，或使用宿主原生工具。定位具体字段时可提供 episode_hint，避免把
selective_read 与 structured_compute 混为一谈。

工具列表：`sro_preview`、`sro_read`、`sro_raw`、`sro_card`、`sro_decide`、`sro_trace`。
工具参数不支持任意外部目录；显式工作区外路径回退到原生工具。MCP 与 Codex
短生命周期 hook 不共享内存 episode；hook 只进行保守路由提示，实际 sparse
读取状态留在 MCP。Pi 则在常驻桥接内保持该会话状态。

停用：Codex 将对应 `[plugins."sparseread-codex@<marketplace>"]` 的 enabled
改为 false；Pi 在目标项目运行 `pi remove --local <安装目录绝对路径>`。
这会停用注册，不自动删除 `.sparseread` 内的 runtime 或用户数据。

## 验证标准

需要同时验证：核心决策一致性；MCP 握手/六工具/preview→read→raw；Pi 真正的
SDK 载入与包注册；大文件无范围读取转向、有范围读取不转向；故障/取消/重复
重试恢复原生；配置合并、重复安装、dry-run、wheel 离开源码仍可运行。
自动测试和独立审核结果另见本阶段验收报告。实际任务收益需要独立模型基准，
本次不进行付费模型 benchmark。
