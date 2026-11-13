# main 分支合入规则

所有提交从功能分支通过 PR 合入 `main`，必须完成本仓库的全部必需 CI，并取得 **@aprilvkuo 本人**对当前改动的明确批准。Agent 可以实现、验证、推送功能分支和准备 PR；未经本人对该 PR 当前版本的明确授权，不得执行合并、开启自动合并或代替本人提交批准 Review。此前任务中的发布授权不自动适用于后续 PR。

## GitHub 强制规则

- `.github/CODEOWNERS` 将所有路径（含 CODEOWNERS、CI 和本规则）交由唯一 Code Owner `@aprilvkuo`。
- `main` 必须经 PR，至少一次批准且必须包含该 Code Owner 的批准。其他人的 Review 不能替代。
- 改动后旧批准自动失效；重新同步 `main` 后仍需满足最新 CI 和审批要求。
- 必需 CI 限定来源为 GitHub Actions，合入前分支必须与 `main` 同步，Review 对话必须解决。
- 管理员同样受规则约束，PR 绕过名单为空；禁止强推和删除 `main`。
- 只有 `@aprilvkuo` 可撤销 Review；不得通过修改规则、删除 CODEOWNERS 或替换检查结果来跳过审批。

| 必需检查 | 来源 |
| --- | --- |
| `check (3.10)` | GitHub Actions (`15368`) |
| `check (3.13)` | GitHub Actions (`15368`) |

配置见 [main-branch-protection.json](main-branch-protection.json)。该文件是 GitHub 分支保护 API 的请求体，**提交文件本身不会启用服务器保护**。必须应用并回读 GitHub 设置；CI 已存在或变绿也不代表分支已受保护。

## 账号与权限

GitHub 不允许 PR 作者批准自己的 PR。日常 Agent/自动化应使用独立的开发账号或 GitHub App 创建 PR，由 `@aprilvkuo` 使用本人账号在 GitHub Review 中批准。Agent 即使能访问本人的凭据，也不得冒用审批身份；聊天中的确认不能替代服务器要求的 Review。不要为解决自审批限制而增加 bypass 或关闭管理员约束。

拥有修改仓库规则权限的管理员，以及组织 Owner，仍可更改保护配置。若需要权限层面保证只有本人能修改这些规则，应另行审查组织 Owner 和仓库管理权限；本变更不修改组织成员或角色。

## 首次启用与核对

1. 由 `@aprilvkuo` 人工确认并合入本次治理配置 PR，让 `main` 已有正确的 CODEOWNERS。
2. 私有组织仓库需要 GitHub Team 或 Enterprise 套餐；Free 套餐不能强制执行这些保护。保持仓库私有，套餐不足时先升级组织。
3. 使用具有本仓库 Administration: write 权限的管理员凭据，在仓库根目录应用配置：

   ```bash
   source /mnt/disk2/guohongjie_home/.config/network/proxy.env
   PROXY_PROBE_URL=https://github.com proxy_run gh api --method PUT \
     repos/para-rsi/rsi-skills/branches/main/protection \
     --input .github/main-branch-protection.json
   ```

   `proxy.env` 是本机网络约定，其它机器需使用各自的网络配置。应用前先读取现有保护，若规则已有变动，先审查差异，避免覆盖更严格的配置。

4. 回读 `repos/para-rsi/rsi-skills/branches/main/protection` 与 `main` 的 CODEOWNERS，核对管理员约束、唯一 Owner、空 bypass、全部必需检查及强推/删除开关。GitHub 的 CODEOWNERS errors API 应无错误。
5. 独立开发账号创建后续 PR；CI 全绿但没有本人批准时仍应阻止合并。本人批准后才能合并；继续提交新改动时必须重新审批。

官方说明：[保护分支](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule)、[Code Owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)、[禁止作者自审批](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)。
