# GitHub Integration

该模块实现 GitHub Projects v2 tracker、Settings 向导 discovery 和 Codex 动态工具。

## Projects v2 映射

- Project item 关联的 Issue/PR 被归一化为 `WorkItem`。
- `Status` single-select 字段映射为 `WorkItem.state`。
- 配置的 `priority_field` 会被解析为排序字段；缺失时任务排在同等状态的后面。
- Issue dependencies 用 REST API 查询；不可用时按配置降级为“不阻塞”并记录事件。

## 动态工具

- `github_graphql`：透传 GraphQL query 和 variables。
- `github_rest`：只允许 GitHub API 相对路径，并按配置仓库做 allowlist 检查。

所有工具都必须避免记录 token。写操作只有在 `tools.github.mode: read_write` 时允许。

## Settings Discovery

- `discovery.py` 只执行 GitHub 只读 GraphQL 查询，用于 Settings 页面 PAT 向导。
- Discovery 会读取 viewer、可选 owner、owner 下的 Projects v2、Project 字段、single-select options，以及 Project item 中出现过的 Issue/PR 仓库。
- Discovery 使用前端传入的临时 token 或 Electron main 解密出的已保存 token；后端不持久化 token，响应也不得包含 token。
- Discovery 不会修改 GitHub，也不会改变调度器配置；只有 Settings 的 Save & Apply 才会热应用配置。
