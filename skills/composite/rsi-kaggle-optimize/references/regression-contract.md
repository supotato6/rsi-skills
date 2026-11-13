# 离线回归输入

运行 `python3 /installed/rsi-kaggle-optimize/scripts/regression.py /task/plan.json`。所有文件路径相对 plan.json；返回 JSON，退出 0 通过，1 阻断。仅依赖 Python 3.10+ 标准库，无联网和上传行为。

```json
{
  "competition": "example-competition",
  "metric": "accuracy",
  "direction": "higher",
  "protocol": "fixed-split-v1",
  "baseline_id": "baseline",
  "candidates": [
    {"id": "baseline", "code": "baseline/code.zip", "artifact": "baseline/submission.csv", "evaluation": "baseline/evaluation.json"},
    {"id": "trial-1", "code": "trial-1/code.zip", "artifact": "trial-1/submission.csv", "evaluation": "trial-1/evaluation.json"}
  ],
  "uploads": [
    {"kind": "kaggle-notebook", "resource": "owner/private-solution", "visibility": "private", "metadata": "kernel-metadata.json"}
  ]
}
```

`code` 是完整冻结源码包，不应只包含入口文件。`artifact` 是该候选实际拟提交文件。评估报告来自 System 可信评估记录，字段如下（哈希替换为文件实际 SHA-256，示例 score 仅为假设数据）：

```json
{
  "candidate_id": "baseline",
  "competition": "example-competition",
  "metric": "accuracy",
  "direction": "higher",
  "protocol": "fixed-split-v1",
  "kind": "validation",
  "status": "complete",
  "artifact_valid": true,
  "code_sha256": "ACTUAL_CODE_SHA256",
  "artifact_sha256": "ACTUAL_ARTIFACT_SHA256",
  "score": 0.5
}
```

协议 ID 应固定数据版本、划分、评分定义和可比较条件。不同协议、训练分数、Public LB 与本地 CV 不能混在同一比较中。基线必须有效；失败改进会被排除并列出原因；严格更优才取代基线，平分保留基线。此脚本不判断统计显著性，后续仍需实验比较与 System 最终选择。

Notebook metadata 文件必须是实际上传使用的完整配置，其中 `is_private: true` 为布尔值。脚本不生成 metadata、不验证其其他 API 字段，也不自动核对 resource 与服务端 ID；执行工具需验证这些一致。其他资源使用 `kind: "other-private-resource"`，明确 `visibility: "private"`，并提供 `config_evidence` 文件路径；脚本只检查声明和证据文件存在，具体工具的字段含义必须由执行方核实。没有拟上传资源时明确给 `uploads: []`，不能用空列表隐藏实际上传操作。

这是可复用的本地门禁，不是安全隔离层：脚本无法证明输入完整、报告可信、Discussion 建议正确、实际请求未被修改或远端已私有。成功结果始终保留 `remote_privacy_verified: false` 和 `public_improvement_proven: false`；执行方需另存服务端可见性证据与真实提交成绩。不得拿脚本通过替代这些检查。
