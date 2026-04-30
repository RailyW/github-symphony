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

当 `tools.github.mode: read_write` 时，`github_rest` 允许写入配置仓库范围内的 Issue/PR/Actions/Checks 相关 REST path，`github_graphql` 允许 GraphQL mutation。

这意味着 Codex agent 可以在 WORKFLOW prompt 指导下：

- 创建或更新 issue comment。
- 更新 Project v2 Status 字段。
- 查询 PR review、checks、Actions。

调度器自身默认不自动写 GitHub 状态，也不自动创建、删除或关闭远端内容。

## Token 安全

- 后端不会把 token 写入事件或日志。
- 桌面端不保存 token。
- 如果要打包分发，建议使用系统钥匙串或用户环境变量注入 token，而不是把 token 写进 `WORKFLOW.md`。
