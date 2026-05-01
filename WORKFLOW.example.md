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
  active_states: [Todo, In Progress, Rework]
  terminal_states: [Done, Closed, Cancelled]
  priority_field: Priority

blocker_policy:
  kind: github_issue_dependencies
  unavailable_behavior: treat_unblocked

workspace:
  root: ~/code/github-symphony-workspaces
  cleanup_terminal_workspaces: false
  hooks:
    after_create: |
      git clone git@github.com:your-org/your-repo.git .

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
  kind: update_project_status
  success_state: Done
  failure_state: Rework
  mark_done_after_successful_turn: true
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

请先阅读 issue/PR 描述和仓库代码，再实施最小必要修改。完成后请在 GitHub 中留下清晰的工作说明、验证结果和剩余风险。
