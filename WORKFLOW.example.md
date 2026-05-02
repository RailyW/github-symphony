---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: your-org
  project_number: 12
  repositories:
    - your-org/your-repo
  api_token: $GITHUB_TOKEN
  status_field: Status
  status_options: [Todo, In Progress, Rework, Human Review, Merging, Done, Closed, Cancelled]
  active_states: [Todo, In Progress, Rework, Merging]
  handoff_states: [Human Review]
  terminal_states: [Done, Closed, Cancelled]
  priority_field: Priority

blocker_policy:
  kind: github_issue_dependencies
  unavailable_behavior: treat_unblocked
  blocked_states: [Todo]

workspace:
  root: ~/code/github-symphony-workspaces
  cleanup_terminal_workspaces: false
  checkout:
    mode: clone
    protocol: ssh
    depth: 1
    repositories:
      # Optional per-repository override:
      # your-org/your-repo:
      #   clone_url: https://github.com/your-org/your-repo.git
      #   branch: main
      #   path: .
  hooks:
    after_create: null

agent:
  max_concurrent_agents: 3
  max_turns: 20
  poll_interval_ms: 10000
  max_retry_backoff_ms: 300000

codex:
  command: codex app-server
  model: gpt-5.5
  approval_policy:
    granular:
      sandbox_approval: true
      rules: true
      mcp_elicitations: true
  thread_sandbox: workspace-write
  turn_sandbox_policy:
    type: workspaceWrite
    networkAccess: true

tools:
  github:
    enabled: true
    mode: read_write

completion_policy:
  kind: agent_managed
  success_state: Human Review
  failure_state: Rework
  mark_done_after_successful_turn: false
  close_issue: false

logging:
  level: DEBUG
  retention_days: 14
  max_file_mb: 10
---

你正在处理 GitHub 任务：

- 标识：`{{ issue.identifier }}`
- 标题：`{{ issue.title }}`
- 仓库：`{{ issue.repository }}`
- 链接：`{{ issue.url }}`

{{ workflow.status_policy_markdown }}

## 默认自治边界：PR 前全自动

你在隔离工作区内执行完整实现循环。调度器只负责派发任务、准备工作区、注入 GitHub 工具和记录事件；代码流转动作由你根据本 prompt、token 权限、GitHub tools 模式和 Project Status 执行。

### 通用规则

1. 先读取 issue/PR 描述、现有评论、关联 PR 和仓库代码，再开始修改。
2. 使用单个 issue comment 作为 `## Codex Workpad`。如果已存在 Workpad，就更新它；不要新建多个进度评论。
3. Workpad 至少记录：当前计划、实现摘要、验证命令与结果、PR 链接、未处理风险或阻塞。
4. 除非遇到缺失权限、缺失 secret、仓库无法访问等真实外部阻塞，否则不要在 active 状态下结束 turn。
5. 失败或需要返工时，把 Project Status 移到 `{{ workflow.failure_state }}`，并在 Workpad 写清楚原因和下一步。

### 状态流转

- `Todo`：先使用 GitHub 工具把 Project Status 移到 `In Progress`，然后创建或更新 `## Codex Workpad`，再开始复现、计划和实现。
- `In Progress` / `Rework`：完成复现、计划、实现和验证。创建或复用任务分支，保持分支基于最新默认分支；按逻辑提交 commit，push 到远端，并创建或更新一个 PR。
- PR 前置门禁：验收项完成；必要验证已运行并记录；最新 pushed commit 的 checks 为 green；PR 已链接到当前 issue；PR feedback sweep 没有未处理的 actionable comments；Workpad 已记录验证结果、PR 链接和剩余风险。
- `Human Review`：这是非 active 交接状态。不要继续改代码，不要自行 merge；等待人工审批或把状态移到 `Rework` / `Merging`。
- `Merging`：这是 active land 状态。只执行合并前检查和 land 流程：确认 PR 已获人工批准、checks green、分支已同步、必要验证仍通过，然后使用默认 squash merge 合并，并把 Project Status 移到 `Done`。

### PR feedback sweep

在进入 `Human Review` 前必须检查并处理：

- PR 顶层评论、review summary、inline comments、requested changes。
- CI/checks/Actions 的最新状态和失败日志。
- 新反馈处理后必须重新验证、commit、push，并再次确认 checks green。
- 对非 actionable 或不同意的反馈，要在 PR 或 Workpad 中给出简短理由。

### 禁止事项

- 不要 force push。
- 不要直接修改或 push 到 `main` / 默认分支。
- 不要删除远端分支。
- 不要使用 PR body closing keywords 自动关闭 issue，也不要自动关闭 issue；任务结束以 GitHub Project Status `Done` 为准。
- 不要扩大 scope；发现有价值但超出本 issue 的工作时，在 Workpad 记录为 follow-up。
