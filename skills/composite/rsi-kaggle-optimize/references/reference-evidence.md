# 参照与复现证据

实验内的基线冻结；最终比较另存新快照。快照含比赛、指标、方向、Public 榜单、取得时间、覆盖范围、原始响应哈希和最佳版本映射。恢复不修改基线、任务身份或截止，也不重复已完成或中断的最终抓取。无预算/网络失败时注明当前参照不可用。

下载请求包含 Notebook、版本序号、Best Score 的 `scriptVersionId`；响应包含实际版本、源码 SHA256、元数据 SHA256、输入/模型依赖声明和未固定项。依赖为空不能推断依赖完整。下载、执行或正式成绩任一缺口都必须可见。

| 层级/方式 | 所需证据 | 不代表 |
| --- | --- | --- |
| source_only | 已找到源码 | 版本正确或运行过 |
| source_verified | System 核验固定版本、源码/元数据身份 | 完整执行或分数复现 |
| full_execution | System 的成功执行记录、完整要求范围、输入/权重/环境身份 | 正式分数匹配 |
| score_matched | 正式 submission_id、绑定的候选/产物哈希、同指标/榜单/阶段的实际成绩 | 重新训练过 |
| public_output_reuse | 公开输出来源版本、内容哈希与提交身份 | 原源码完整重跑 |
| smoke_only | 明确子集与占位/跳过范围 | 全量运行或可提交产物 |

执行方式与分数层级是两条维度：公开输出可匹配官方成绩，仍是复用；模型声明 `public_score_reproduced: true` 不能代替 System 查分。`reference.json` 保留 `status: reproduced|blocked` 和具体 `reason`，增加 `mode: source_execution|public_output_reuse`、`execution_scope: full|smoke`。smoke 或明确未完成所需隐藏运行时必须为 blocked。完整复现声明需源码、固定版本、执行证据路径；System 独立审核输入身份、全量范围及真实执行，未验证的声明与已验证层级分开保存。

离线 `regression.py` 的可选输入：

- `reference_report`：上述声明，仅输出 `declared_level`；不认证完整执行或正式分数。
- `comparison_started_at`：本次最终比较开始的带时区 ISO 时间。
- `final_reference`：新发现结果，含 `freshness: live`、`fetched_at`、`competition`、`metric`、`direction`、`board: public` 和 `candidates[0].public_score`。
- `official_submission`：System 实际查到的 `submission_id`、`score`、同名比赛/指标/方向/榜单字段。

脚本仅产生数值关系 `ahead|matched|behind`，过期、缺失、身份不同则 unverified；不会从冻结基线推导当前结论，也不把本地 CV 当正式分。`public_improvement_proven` 始终为 false：System 另核验官方来源、评分阶段与扫描覆盖后才能作最终声明。
