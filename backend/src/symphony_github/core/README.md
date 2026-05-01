# Core

`core` 模块保存与具体 tracker 无关的编排逻辑。

## 职责

- 解析 `WORKFLOW.md` 并保留 prompt 模板。
- 解析、归一化和导入导出 App 内 settings。
- 将配置归一化为强约束的 dataclass。
- 组装一次配置对应的 runtime 组件，供 CLI 和热重配 API 复用。
- 管理内存事件流、当前运行快照和持久 JSONL 诊断日志。
- 为每个 GitHub work item 创建独立工作区并执行 hooks。
- 根据状态、依赖、并发槽和重试策略派发 Codex agent。
- 将 GitHub Project Status 建模为 `active_states`、`handoff_states`、`terminal_states` 和 `blocked_states`，支持任意自定义阶段名。
- 默认使用 `agent_managed` completion policy，由 prompt 和 GitHub 工具驱动 PR 前自治、`Human Review` 交接和 `Merging` land；也支持切换为 App 自动更新 Project Status。
- 为 prompt 注入 `workflow.status_policy_markdown` 和结构化阶段策略，让 agent 自动适应当前 Project 配置。
- 支持热应用新配置；已运行 agent 保留派发时的配置和 runner。
- 提供日志脱敏、轮转、查询和诊断包导出辅助逻辑。

## 非职责

- 不直接调用 GitHub API；GitHub 访问通过 `integrations.github` 完成。
- 不直接实现桌面 UI；UI 只读取 `api` 层暴露的状态。
- 不把 git commit、push、merge 或远端删除做成调度器内置业务动作；这些动作只能由 agent 在 prompt、approval policy、GitHub token 权限和工具模式允许时执行。
- 不自动关闭 GitHub Issue；默认 prompt 也禁止通过 PR closing keyword 自动关闭 issue，任务终态以 Project item 的 Status 为准。
