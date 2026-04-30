# GitHub Integration

该模块实现 GitHub Projects v2 tracker 和 Codex 动态工具。

## Projects v2 映射

- Project item 关联的 Issue/PR 被归一化为 `WorkItem`。
- `Status` single-select 字段映射为 `WorkItem.state`。
- 配置的 `priority_field` 会被解析为排序字段；缺失时任务排在同等状态的后面。
- Issue dependencies 用 REST API 查询；不可用时按配置降级为“不阻塞”并记录事件。

## 动态工具

- `github_graphql`：透传 GraphQL query 和 variables。
- `github_rest`：只允许 GitHub API 相对路径，并按配置仓库做 allowlist 检查。

所有工具都必须避免记录 token。写操作只有在 `tools.github.mode: read_write` 时允许。
