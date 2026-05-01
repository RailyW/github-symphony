# GitHub agent PR 前自治工作流

## Goal

把 GitHub Symphony 从“调度 agent 后交给人工检验”的保守模式，升级为默认支持“PR 前全自动”的自治工作流。Agent 可以在隔离工作区内创建分支、commit、push、开/更新 PR、处理 PR feedback 和检查；merge 只在人工把 GitHub Project 状态移到 `Merging` 后由 agent 执行 land 流程。

调度器仍保持 OpenAI Symphony 的边界：它负责 scheduler/runner/tracker reader，不把 commit、push、merge 做成调度器内置业务动作。代码流转动作由 Codex agent 在 workflow prompt、approval policy、GitHub token 权限、动态工具和 Project 状态机共同约束下完成。

## What I Already Know

* 用户确认默认自治边界为 `PR 前全自动`。
* upstream Symphony 的核心思想是让团队管理 work，而不是逐步监督 coding agent。
* upstream SPEC 明确 approval、sandbox、operator-confirmation 是实现定义；ticket writes 通常由 agent 工具链完成。
* upstream `elixir/WORKFLOW.md` 实际把 `commit`、`push`、`pull`、`land` 写入 agent 工作流，并要求 PR checks、PR feedback sweep、branch pushed 和 PR linked 后才进入 `Human Review`。
* 当前项目已有 GitHub Projects v2 tracker、Codex app-server runner、`github_graphql` / `github_rest` 动态工具、桌面 Settings 和 Help。
* 当前项目 README、Help 和 runner 仍偏保守：调度器成功后主要自动更新 Project Status，不支持默认 PR-ready 自治说明。

## Requirements

* 默认状态机更新为：
  * `status_options`: `Todo`, `In Progress`, `Rework`, `Human Review`, `Merging`, `Done`, `Closed`, `Cancelled`
  * `active_states`: `Todo`, `In Progress`, `Rework`, `Merging`
  * `handoff_states`: `Human Review`
  * `terminal_states`: `Done`, `Closed`, `Cancelled`
* 默认 completion policy 更新为：
  * `kind`: `agent_managed`
  * `success_state`: `Human Review`
  * `failure_state`: `Rework`
  * `mark_done_after_successful_turn`: `false`
* 默认 GitHub workflow prompt 必须覆盖：
  * `Todo`: agent 先把任务移到 `In Progress`，创建或更新单个 `## Codex Workpad` issue comment。
  * `In Progress` / `Rework`: agent 复现、计划、实现、验证、commit、push、开或更新 PR。
  * PR 前置门禁：验收项完成、最新 commit 检查绿、PR feedback sweep 无未处理 actionable comments、PR 已链接到 issue、workpad 记录验证结果。
  * `Human Review`: 非 active，等待人工审批，agent 不继续改代码。
  * `Merging`: active，agent 只执行 land 流程，确认 PR 已批准、checks green、分支同步和必要验证后，用默认 squash merge 合并并把 Project Status 移到 `Done`。
  * 默认禁止自动关闭 issue、删除远端分支、force push、直接修改 main。
* Codex subprocess 启动时，如果 tracker token 已配置，必须临时注入 `GITHUB_TOKEN` 和 `GH_TOKEN`，仅供 agent workspace 使用；日志和 diagnostics 不得泄漏 token。
* 默认 approval handler 继续保守拒绝需要确认的请求；当配置为 `approval_policy: never` 时，app-server approval / user-input 请求按高信任策略自动批准或选择安全继续项，并记录 auto-approved 事件。
* 动态工具新增 `github_update_project_status`，参数为 `project_item_id` 和 `state_name`，内部复用 tracker 的 Project v2 Status 更新逻辑。
* `github_update_project_status` 写操作必须受 `tools.github.mode=read_write` 约束，失败时返回结构化 tool failure，不让 app-server 主循环崩溃。
* 桌面端 Settings 或 Completion 页面展示 Autonomy preset，默认文案为 `PR 前全自动`。
* Help、README、architecture、permissions 更新为新安全边界：调度器不直接做 commit/push/merge；agent 可在高信任 workflow、token 权限、prompt 规则和状态机允许时执行。
* Settings 对 `approval_policy: never`、`read_write` GitHub tools、token 写权限给出明确风险提示。

## Acceptance Criteria

* [ ] 新默认 workflow 和 Settings 默认值能解析计划中的状态机与 `agent_managed` completion policy。
* [ ] `Merging` 属于 active state，`Human Review` 不属于 active state。
* [ ] `agent_managed` 时 runner 不自动把成功 turn 改到 `Human Review`。
* [ ] Codex subprocess 环境在配置 GitHub token 时包含 `GITHUB_TOKEN` / `GH_TOKEN`，诊断日志不包含 token 明文。
* [ ] 默认 approval handler 仍拒绝人工确认请求；`approval_policy: never` 时自动批准 command/file-change/applyPatch/exec approval，并记录事件。
* [ ] `github_update_project_status` 在 `read_write` 下可调用 tracker 更新 Project Status，在 `read_only`、未知状态、空 project item id、API 失败时返回结构化失败。
* [ ] `WORKFLOW.example.md`、桌面 Settings 默认值、Help 文案、README/docs 的自治边界一致。
* [ ] Prompt 包含 workpad、branch/commit/push/PR、PR feedback sweep、checks green、Human Review、Merging land、禁止 force push/直接 main/删除远端分支等规则。
* [ ] 后端测试、前端 typecheck/build 或项目现有质量命令通过；如果某项不能运行，必须记录原因。

## Out of Scope

* 不实现低风险小改直接自动 merge。
* 不把 commit、push、merge 做成 orchestrator 内置业务动作。
* 不默认删除远端分支。
* 不默认通过 PR body closing keyword 自动关闭 GitHub issue；任务终态以 Project Status `Done` 为准。
* 不要求本次实现完整 GitHub App 权限向导，只更新现有 PAT/Settings 模型和说明。

## Technical Notes

* 相关实现区域：`backend/src/symphony_github/core/config.py`、`core/settings.py`、`core/runner.py`、`codex/app_server.py`、`integrations/github/dynamic_tools.py`、`WORKFLOW.example.md`、`desktop/src/App.tsx`、`desktop/src/settingsClient.ts`、`desktop/src/types.ts`、README/docs。
* 相关研究文件：`.trellis/tasks/05-01-github-agent-pr-autonomy/research/upstream-symphony-autonomy.md`。
* 默认 merge method 按计划使用 `squash`，目前优先写入 prompt 约束；除非已有配置模式很容易扩展，否则不强制新增配置字段。
* 代码注释需遵守仓库 AGENTS 约定，新增函数和复杂逻辑写清晰中文注释。

