# Codex Runner Autonomy Contract

This spec records the unattended GitHub Project runner contract. Read it before
changing default prompts, Codex approval policy handling, completion policy,
runner continuation behavior, or Settings defaults related to PR-before-merge
autonomy.

## Scenario: Unattended PR-Before-Merge Runner

### 1. Scope / Trigger

- Trigger: GitHub Project items in active states are dispatched to Codex
  app-server from the local runner without an interactive operator channel.
- Applies to:
  - `backend/src/symphony_github/core/settings.py`
  - `backend/src/symphony_github/core/config.py`
  - `backend/src/symphony_github/core/runner.py`
  - `backend/src/symphony_github/codex/app_server.py`
  - `desktop/electron/main.ts`
  - `desktop/src/settingsClient.ts`
  - `desktop/src/App.tsx`
  - `WORKFLOW.example.md`
- The orchestrator must not implement `commit`, `push`, `create PR`, or `merge`
  as backend business actions. These actions remain agent actions constrained by
  prompt, approval policy, token permissions, tools mode, sandbox, and Project
  state.

### 2. Signatures

- `default_app_settings() -> Dict[str, Any]`
  - New settings must default `codex.approval_policy` to
    `{"preset": "high-trust"}` for PR-before-merge autonomy.
- `settings_to_raw_config(raw_settings, github_token=None, token_placeholder=None) -> Dict[str, Any]`
  - Missing `codex.approval_policy` must keep the conservative granular fallback
    so older saved settings are not silently promoted to high trust.
- `_normalize_approval_policy(value, default) -> Any`
  - High-trust presets normalize to app-server-compatible `"never"`.
- `approval_policy_is_never(approval_policy: Any) -> bool`
  - Must return `True` for `"never"` and supported high-trust presets, including
    dict forms with `preset`, `autonomy_preset`, or `mode`.
- `AgentRunner.run(item: WorkItem, run_record: RunRecord) -> RunnerResult`
  - A successful turn must be followed by tracker state refresh.
  - Leaving active states returns `RunnerResult(should_continue=False)`.
  - Exhausting `agent.max_turns` while still active returns
    `RunnerResult(should_continue=True, error=...)`.

### 3. Contracts

- Default prompt contract:
  - All default prompt sources must communicate that the runner is
    non-interactive and cannot receive `ok`, `行`, `continue`, or other manual
    confirmation replies.
  - While the GitHub Project item remains in an active state and work is scoped
    to the current issue, the agent is authorized to create or reuse a task
    branch, modify code, run validation, commit, push the task branch, and create
    or update a PR.
  - The agent must not wait for operator confirmation before commit, push, or PR
    creation in an unattended runner.
  - Real blockers are external conditions only: missing permissions, missing
    secrets, inaccessible repository, GitHub/API/network failures, or CI/checks
    that cannot be determined.
  - Before handoff, the agent must update the single `## Codex Workpad` comment
    and use the `github_update_project_status` tool to move Project Status to
    `{{ workflow.success_state }}` with the current `project_item_id`.
  - The default prompt must include the Project item ID. If a repository or PR
    reports no checks, the agent must record `no checks reported` in the
    Workpad and may continue handoff instead of treating absent checks as a
    blocker.
  - Automatic merge is forbidden unless the current Project Status is `Merging`.
- Approval contract:
  - New settings use `{"preset": "high-trust"}` and backend config normalizes it
    to `"never"`.
  - High-trust app-server behavior auto-approves command, file, permissions,
    applyPatch, and exec approvals, and answers tool user-input approval prompts
    with a continuing option when one exists.
  - Existing saved settings that do not contain `codex.approval_policy` must keep
    granular conservative approval.
- State contract:
  - `Human Review` is a handoff state and must not be active by default.
  - `Merging` is the active land state where merge may be performed after human
    approval and checks.
  - In `agent_managed` mode, the App does not automatically write Project Status
    after a successful turn. The agent must perform the status update through
    GitHub tools.

### 4. Validation & Error Matrix

| Condition | Required behavior |
| --- | --- |
| New default settings are created | `codex.approval_policy` is `{"preset": "high-trust"}` |
| Imported or saved legacy settings omit `approval_policy` | Conservative granular approval is used |
| High-trust preset reaches config parsing | Normalized to `"never"` |
| High-trust preset reaches app-server helper directly | Treated as unattended/high-trust |
| Successful turn refreshes item outside active states | Stop continuation |
| Successful turn refreshes item still in active states and turns remain | Record `runner.continuation` and run another turn |
| Successful turn refreshes item still active and `max_turns` is exhausted | Set run failed, record `runner.max_turns_exhausted`, return retryable result |
| Agent says it is waiting for user text | Do not parse text to update state; rely on tracker state and max-turns diagnostics |
| Current status is not `Merging` | Prompt must prohibit auto merge |

### 5. Good / Base / Bad Cases

- Good: A `Todo` issue is dispatched. The agent moves it to `In Progress`,
  implements the change, validates, commits, pushes a task branch, creates a PR,
  updates Workpad, moves Project Status to `Human Review`, and runner stops after
  tracker refresh shows a non-active state.
- Base: An existing settings document lacks `codex.approval_policy`. Loading it
  preserves conservative granular approval rather than silently enabling
  high-trust unattended execution.
- Bad: The agent ends a turn saying it is waiting for `ok`. The item remains
  active. The runner continues until `agent.max_turns`, then records
  `runner.max_turns_exhausted` instead of silently treating the run as complete.

### 6. Tests Required

- Prompt tests must assert the default prompt contains non-interactive wording,
  refusal to wait for `ok` / `行` / `continue`, commit/push/PR authorization,
  `{{ workflow.success_state }}`, Workpad update, CI/checks blocker language,
  and the non-`Merging` merge prohibition.
- Settings tests must assert new defaults use `{"preset": "high-trust"}` and
  normalize to `"never"`.
- Compatibility tests must assert missing legacy `approval_policy` keeps
  granular approval.
- App-server tests must assert `approval_policy_is_never` recognizes all supported
  high-trust preset spellings and rejects granular policies.
- Runner tests must assert:
  - `agent_managed` mode does not automatically update Project Status.
  - Moving to `Human Review` stops continuation.
  - Staying active through `max_turns` marks the run failed, emits
    `runner.max_turns_exhausted`, and returns `should_continue=True`.
- Build checks must include `npm --prefix desktop run build` so frontend preset
  mirror code stays type-safe.

### 7. Wrong vs Correct

#### Wrong

```python
# A successful Codex turn is treated as complete even though Project Status
# remains active. The orchestrator may silently stop while the item stays Todo.
if turn_succeeded:
    return RunnerResult(should_continue=False)
```

#### Correct

```python
# Completion is based on tracker state, not model text or turn success alone.
latest = (await tracker.fetch_issue_states_by_ids([item.id])).get(item.id)
if latest is None or latest.state not in config.tracker.active_states:
    return RunnerResult(should_continue=False)
if turn_index + 1 >= config.agent.max_turns:
    return RunnerResult(should_continue=True, error="max_turns exhausted")
```

## Maintenance Notes

- Keep the four default prompt sources synchronized:
  `WORKFLOW.example.md`, backend default settings, Electron main fallback, and
  browser fallback settings.
- When the built-in prompt semantics change, legacy saved settings that still
  match the old built-in short prompt must migrate to the current default prompt
  while preserving custom user prompts.
- Keep backend high-trust preset names and frontend `isNeverApprovalPolicy`
  mirror logic synchronized.
- Do not add plaintext provider secrets to App settings to make unattended mode
  work. Provider credentials must continue to follow the existing Codex native
  environment behavior.
