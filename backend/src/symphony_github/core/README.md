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
- 在 Codex turn 正常完成后按 `completion_policy` 更新 GitHub Project Status，避免任务仍处于 active state 时被重复派发。
- 支持热应用新配置；已运行 agent 保留派发时的配置和 runner。
- 提供日志脱敏、轮转、查询和诊断包导出辅助逻辑。

## 非职责

- 不直接调用 GitHub API；GitHub 访问通过 `integrations.github` 完成。
- 不直接实现桌面 UI；UI 只读取 `api` 层暴露的状态。
- 不自动执行 git commit、merge、push 或远端删除。
- 不自动关闭 GitHub Issue；当前完成策略只更新 Project item 的 Status。
