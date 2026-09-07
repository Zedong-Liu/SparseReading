# 中文长文

标题：先少读，再决策——SparseRead 把 Agent 的阅读控制点前移

现代 Agent 已经会打开 PDF、仓库、表格和日志，但默认接口仍是对象级的：先把整份东西放进上下文，再从噪声里找回这一步真正需要的证据。我们把这种错配叫做 over-reading。它费的是 token 体积和墙钟时间，也会把真正有用的信号稀释掉。

论文里，在有代表性的高稀疏任务上，进入阅读预算的内容只有 6–15% 对当前决策有用。长上下文、KV cache、读完再摘要，优化的都是「已经读进来的内容怎么处理」。ACON 是模型侧的 zero-shot 观察压缩器，辅助调用计入成本；Complexity Trap 的 observation masking 保留最近 10 条观察——两者都发生在宽读已经付过账之后。

SparseRead 把控制点前移。它是一层 training-free、对模型透明的 pre-reading：Object Preview 先给有界视图，Agent 写出 Read Intent，Read Gate 决定 `force` / `advisory` / `native`，Reader Backend 再按 scout / focus / collect / refine / verify 取有锚点的证据。Evidence State 告诉模型覆盖了什么、还缺什么、能不能停，或者退回原生工具。

论文 [arXiv:2608.22237](https://arxiv.org/pdf/2608.22237)《Read Less, Solve More》在六个模型、五个场景上报告：相对 Naive，token 体积最多降 92.9%，墙钟最多降 89.0%。这是「最多」，不是平均。NanoBot 的 30 个模型–场景格子全部降低 token 和墙钟；其中 26 格质量持平或更好，不是 30/30，另外 4 格分数落在 −0.10 到 −0.02。和 ACON、Complexity Trap 的 last-10 masking 比，Figure 8 的六个设置里 SparseRead 的 token 和墙钟都最低，也是唯一分数从未低于 Naive 的方法；这六格相对 Naive 的 token 降幅是 63.4–86.3%，论文把对应的墙钟降幅写成 1.8–3.6× 完成时间加速。Figure 8 是每格单次 run。

一个可以讲清楚的旗舰案例——单次 run，不是平均。LooGLE 5Q，DeepSeek-V4-Flash，NanoBot：在一篇约 10 万字符的史文章节里回答五个局部问题。Naive 369,042 token / 17 次请求 / 97.8 秒；SparseRead 50,699 / 4 次 / 39.4 秒；分数都是 1.0，token 降 86.3%。答案本来就是局部的，Full Read 还是把整章读了进去。

这不是弱模型补丁。论文里最强的 Claude Opus 5 在稀疏适配场景上仍省 59.6–89.0% token、51.3–78.1% 墙钟。本地仓库没有 Opus 轨迹，所以只引用这个范围，不编一条 Opus 动画。

适用边界必须写进同一篇。它不会在每个请求上替换原生工具。强制稀疏读（force mode，不是门控后的 SparseRead）在 SPARQL / 全局统计任务上会让 token 增加 309.2%；对照规则编码任务增加 210.5%。这里要看的是 token 代价，不是质量叙事。Read Gate 的工作就是在这种 regime 走开。

开源和无 key 回放：

https://github.com/Zedong-Liu/SparseReading

```bash
make demo
make reproduce CASE=flagship
```

当前仓库能回放 Figure 8 的旗舰案例、多文件审计和 force-mode 边界记录，不能从这一份 checkout 重跑 125-task 全表、Opus 轨迹，或材料化学 / Claw-Eval 夹具。`make reproduce-suite` 只检查已记录的三个案例。

不要把 token 下降自动写成「成本一定下降」或「质量一定上升」。

claim_ids: paper-intro-useful-6-15, paper-abs-token-max, paper-abs-wall-max, paper-s62-30-of-30-cost, paper-abs-quality-preserve, paper-s63-vs-baselines, paper-s63-token-range, paper-s63-speedup, case-flagship-loogle5q-flash, case-flagship-loogle5q-flash-reduction, paper-s62-opus-sparsefit, paper-fig5-t67, paper-fig5-t59
