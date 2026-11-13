# Repository instructions

- Read `CONTRIBUTING.md` before changing skill boundaries or file formats.
- Preserve whole-library installation and progressive loading. Composites reference atomic skills only.
- Skill names are namespaced reusable capabilities; task data and experiment history belong outside skill bodies.
- Never install into a user's global runtime as a side effect of development. Use temporary explicit targets for verification.
- System owns trusted scoring, budgets and hidden-test access. Do not describe offline planning exercises as measured performance gains.
- Verify with `python3 scripts/skills.py check` and `python3 -m unittest discover -s tests -v`.

## main 分支与审批

所有改动从功能分支通过 PR 合入 `main`，必须通过全部必需 CI，并取得唯一审批人 `@aprilvkuo` 本人对该 PR 当前版本的明确批准。Agent 不得代替本人提交批准 Review；未经本人明确授权，不得合并、开启自动合并、直接推送 `main` 或绕过保护。新增提交需重新确认，旧任务的发布授权不自动沿用。详细规则、账号分离及服务器配置见 [.github/MAIN_BRANCH_POLICY.md](.github/MAIN_BRANCH_POLICY.md)；配置文件存在不代表 GitHub 保护已生效。

## 本地记录与版本控制

实验记录、分析、验证报告、具体实验配置、开发计划和会话记忆只保存在被忽略的 `runs/`、`.local/` 或仓库外目录。`docs/` 保留当前架构、接口、使用和开发说明；代码、技能、合成测试 fixture 与通用示例可以跟踪。清理旧记录前先校验本地备份，取消跟踪时保留原文件；不要清理用户的运行数据。提交前运行 `git ls-files -ci --exclude-standard`，输出必须为空，禁止用 `git add -f` 提交本地记录。

CI 的 runner VM 通过既有 `10.0.2.2:7890` 映射访问宿主机默认代理；宿主策略文件不可见时，`.github/network.sh` 仅对该已配置映射按默认、`172.28.4.16:18999`、`172.27.128.224:8234` 的顺序探测，再执行安装命令一次，不因命令失败重放。宿主共享策略、NO_PROXY、依赖锁及 TLS 校验保持原有约定。

CI runner VM 在安全 GET 后还使用实际 uv/pip 客户端读取一个哈希固定的 11KB 公开 wheel，仅写入临时目录并清理；探测未通过时不启动真实安装，遇权限或限流错误停止。真实安装自身失败不得换代理重放。宿主共享代理配置和 NO_PROXY 不变。
