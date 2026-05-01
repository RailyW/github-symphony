# GitHub Permissions

GitHub Symphony 使用 `GITHUB_TOKEN` 或 `tracker.api_token` 中配置的 PAT。

## 只读调度所需权限

- 读取目标 organization/user Project v2。
- 读取 Project v2 fields 和 items。
- 读取目标仓库 Issue/PR。
- 读取 Issue dependencies。

Fine-grained PAT 通常需要：

- 目标仓库：Issues read、Pull requests read、Metadata read。
- Project 所属 owner：Projects read。

GitHub 的 Projects v2 权限会受组织策略影响。如果 GraphQL 返回 project 不存在，通常是 token 无权读取，而不一定是项目编号错误。

## 动态工具写能力

当 `tools.github.mode: read_write` 时，`github_rest` 允许写入配置仓库范围内的 Issue/PR/Actions/Checks 相关 REST path，`github_graphql` 允许 GraphQL mutation，`github_update_project_status` 允许更新配置 Project v2 的 Status 字段。

这意味着 Codex agent 可以在 WORKFLOW prompt 指导下：

- 创建或更新 issue comment。
- 更新 Project v2 Status 字段。
- 查询 PR review、checks、Actions。
- 在 token 和远端权限允许时，通过 `git` / `gh` 创建分支、push 分支、打开或更新 PR。

调度器自身不把 commit、push、merge 做成内置业务动作。默认 prompt 允许 agent 在 PR 前自治流程中执行分支、commit、push、PR 和 feedback sweep；merge 只有在人工把 Project Status 移到 `Merging` 后才进入 land 流程。默认 prompt 禁止 force push、直接 push 默认分支、删除远端分支和使用 PR closing keyword 自动关闭 issue。

如果要启用默认 `PR 前全自动` workflow，Fine-grained PAT 通常还需要：

- 目标仓库：Contents read/write、Pull requests read/write、Issues read/write、Metadata read。
- 如果 agent 需要读取或重跑 CI：Actions read，必要时 Actions write。
- Project 所属 owner：Projects read/write。

实际权限仍取决于组织策略、分支保护和仓库规则。缺少写权限时，agent 应在 Workpad 中记录 blocker，而不是伪装完成。

## Token 安全

- 后端不会把 token 写入事件或日志。
- 桌面端通过 Electron `safeStorage` 保存 token；renderer 不能读取明文 token。
- Codex app-server 子进程启动时会临时收到 `GITHUB_TOKEN` 和 `GH_TOKEN`，便于 `git` / `gh` 和 GitHub 工具使用同一份 PAT。
- 如果要打包分发，建议使用系统钥匙串、safeStorage 或用户环境变量注入 token，而不是把 token 写进 `WORKFLOW.md`。
