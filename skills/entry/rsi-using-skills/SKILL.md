---
name: rsi-using-skills
description: Use when an RSI task needs skill selection or several installed skills may apply to the same solution or improvement effort.
---

# 使用 RSI 技能库

根据任务目标和当前缺口选择技能，先看名称与描述，再读取所选技能正文。可以同时使用多个技能；加载技能不等于启动 Agent、申请额外预算或并行执行操作。

- 新的表格预测任务：从 `rsi-solve-tabular-task` 进入。
- Kaggle 私有打榜、从开源基线继续优化并提交：使用 `rsi-kaggle-optimize`。
- 需要判断 Kaggle 任务下一步优化方向，或从 Top 开源方案进化链寻找最值得做的改动：使用 `rsi-kaggle-evolution-analysis`。
- 改进已有方案：从 `rsi-improve-solution` 进入。
- 从指定 Kaggle 比赛或榜单寻找公开最佳方案：使用 `rsi-kaggle-open-sota`。
- 将固定 Kaggle Notebook 复现到本机并准备正式基线提交：使用 `rsi-kaggle-reproduce`。
- 局部问题：直接选择适用的原子技能，不必经过组合技能。

组合技能提供默认路线；可按证据调整可选步骤。原子技能负责方法。引用通过已安装技能名称解析，支持运行时原生加载或读取目录提供的绝对路径。缺少必要技能或工具时说明缺口，不假称已加载或已执行。

共享一个任务目标、候选版本、实验历史和预算。依赖同一方案的修改与评估顺序执行；独立操作也受 System 的资源限制。若方法冲突，先保留任务定义、数据边界和 System 的工具约束，再选择与当前任务匹配的方法并记录理由。

记录实际加载的技能名。评分、隐藏测试、预算和最终候选选择由 System 控制，安装清单不能代替运行使用记录。
