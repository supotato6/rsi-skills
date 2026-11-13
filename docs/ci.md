# CI 执行环境

仓库检查和跨仓库兼容性检查都由 GitHub Actions 执行。仓库变量 `RSI_SKILLS_RUNNER` 控制执行位置，值必须是合法的 JSON 字符串或标签数组；未设置时使用 `"ubuntu-latest"`。

本地机器的仓库专属 runner 使用以下变量值：

```json
["self-hosted", "Linux", "X64", "rsi-skills-local"]
```

runner 在仓库 Settings → Actions → Runners 中注册，只有带齐全部标签且在线的 runner 才会接收这些作业。不要将本仓库的标签配置给未经授权的机器。runner 离线时作业排队；不会自动改用 GitHub 托管资源。移除变量可恢复默认的托管 runner。

runner 的安装目录、凭据、代理启动脚本和资源上限保存在机器的运行配置中，不提交到仓库。运行服务须按该机器的网络约定加载代理配置；Python 工具缓存与构建产物使用 runner 工作目录，技能安装验证仍使用临时显式目标。维护人员可通过用户服务 `github-actions-rsi-skills.service` 查看状态、日志或停止执行；停机前应等待当前作业结束。

本地 runner 需要支持当前 `actions/checkout`、`actions/setup-python` 的 Linux 环境，以及 Python 下载、包安装和 GitHub Actions 所需的外网访问。跨仓库检查仍要求 `RSI_REPO_READ_TOKEN` 对四个仓库有 Contents 读取权限。

执行位置不改变检查内容或合入规则：保留 `check (3.10)`、`check (3.13)` 和两种组合的兼容性测试；使用真实 PR/push 提交，不用本地手填状态替代 GitHub Actions 结果。合入仍遵循 [main 分支规则](../.github/MAIN_BRANCH_POLICY.md)。
