# Upstream Symphony Autonomy Research

## Source Snapshot

* Repository: `https://github.com/openai/symphony`
* Local clone: `/Users/jeff/codex_temp/openai-symphony`
* Commit inspected: `58cf97da06d556c019ccea20c67f4f77da124bf3`
* Date inspected: 2026-05-01

## Key Findings

* Upstream README positions Symphony as a system that turns project work into isolated autonomous implementation runs. The stated operator model is managing work at a higher level rather than supervising Codex step by step.
* Upstream README explicitly says agents complete tasks and provide proof of work including CI status, PR review feedback, complexity analysis, and walkthrough media; after acceptance, agents land the PR safely.
* Upstream README warns this is an engineering preview for trusted environments.
* Upstream SPEC says Symphony is a scheduler/runner and tracker reader. Ticket writes such as state transitions, comments, and PR links are typically handled by the coding agent through workflow/runtime tools.
* Upstream SPEC does not mandate one universal approval/sandbox/operator-confirmation posture. Implementations must document their selected trust and safety posture.
* Upstream SPEC says a successful run may end at a workflow-defined handoff state such as `Human Review`, not necessarily terminal `Done`.
* Upstream `elixir/WORKFLOW.md` has `approval_policy: never` in its high-trust example workflow and uses `workspace-write` sandboxing.
* Upstream `elixir/WORKFLOW.md` lists related skills for `commit`, `push`, `pull`, and `land`.
* Upstream `elixir/WORKFLOW.md` treats `Human Review` as a wait state and `Merging` as the state where agent follows the land flow.
* Upstream `elixir/WORKFLOW.md` requires PR feedback sweep before `Human Review`, including top-level comments, inline comments, review summaries, revalidation, and push updates.
* Upstream completion bar before `Human Review` requires acceptance criteria complete, validation green, PR feedback sweep complete, checks green, branch pushed, PR linked, and required PR metadata present.
* Upstream app-server implementation auto-approves approval requests when `approval_policy == "never"` and otherwise surfaces approval-required failure rather than stalling indefinitely.

## Local Project Gap

* Current GitHub Symphony has the right architectural skeleton: GitHub Projects v2 tracker, Codex app-server runner, per-task workspaces, dynamic GitHub tools, desktop Settings, and Help.
* Current defaults are conservative: README says the scheduler does not automatically commit, merge, push, or delete remote content.
* Current runner can automatically update Project Status after a successful turn when `completion_policy.kind=update_project_status`.
* Current `agent_managed` mode exists but the default prompt is too short to safely drive PR-ready autonomous behavior.
* Current app-server default response declines approval and user-input requests. It needs a documented high-trust path for `approval_policy: never`.
* Current dynamic tools can call GitHub GraphQL/REST, but Project Status updates require the agent to craft mutation details. A dedicated `github_update_project_status` tool reduces prompt complexity and error risk.

## Implementation Direction

* Follow upstream separation of concerns: keep orchestration generic and move repo-specific autonomous workflow into `WORKFLOW.example.md`, Settings defaults, and prompt guidance.
* Make `PR 前全自动` the default workflow posture while keeping merge gated by human movement into `Merging`.
* Preserve safety controls through explicit status states, token permissions, dynamic tool mode, approval policy, repository REST allowlist, and docs.
* Prefer additive runtime helpers over hardcoding commit/push/merge in backend orchestrator.

