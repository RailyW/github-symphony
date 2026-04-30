# Core

`core` 模块保存与具体 tracker 无关的编排逻辑。

## 职责

- 解析 `WORKFLOW.md` 并保留 prompt 模板。
- 将配置归一化为强约束的 dataclass。
- 管理内存事件流和当前运行快照。
- 为每个 GitHub work item 创建独立工作区并执行 hooks。
- 根据状态、依赖、并发槽和重试策略派发 Codex agent。

## 非职责

- 不直接调用 GitHub API；GitHub 访问通过 `integrations.github` 完成。
- 不直接实现桌面 UI；UI 只读取 `api` 层暴露的状态。
- 不自动执行 git commit、merge、push 或远端删除。
