# Project Context

> Project lineage and AI development authority that apply across the repository.

---

## Upstream Template

This project is a derivative implementation based on OpenAI's open-source `symphony` repository:

- Upstream template: <https://github.com/openai/symphony>
- Local repository focus: GitHub Projects v2 orchestration, local Codex app-server execution, and the Electron desktop management surface.

When local patterns are missing or ambiguous, inspect the upstream template first and use it as the architectural reference. Preserve local behavior when this project has intentionally diverged for GitHub integration, desktop packaging, credentials handling, or Trellis-managed development workflow.

---

## AI Git Operation Authority

The project owner grants Codex full development permission in this repository. Codex may run git operations that affect the current git tree when they are required by the user's request or the Trellis workflow, including:

- `git commit`
- `git merge`
- `git push`

This permission supersedes any older project-local blanket ban on these git operations for Codex-assisted development.

Destructive history or workspace operations, such as hard resets, force pushes, or deleting branches, still require an explicit user request for that exact operation.

---

## Product Automation Boundary

This authority applies to Codex working as the repository development assistant. It does not imply that the GitHub Symphony product runtime should autonomously commit, merge, push, or delete remote content unless product configuration, user prompts, tool mode, and token permissions explicitly allow that behavior.
