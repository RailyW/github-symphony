# GitHub Project Repository Checkout Productization

## Goal

Make GitHub Project item repository binding explicit and reliable by aligning three layers: Project item dispatch allowlist, local workspace checkout, and agent runtime `cwd`. The feature should support multi-repository GitHub Projects without requiring users to hand-write brittle clone hooks.

## Requirements

- Treat `tracker.repositories` as the authoritative list of GitHub repositories this workflow may dispatch and expose through GitHub REST tooling.
- Skip or ignore Project items whose `content.repository.nameWithOwner` is not in `tracker.repositories`; record enough diagnostic context to explain why no candidate work was found.
- Add a first-class `workspace.checkout` configuration that can clone the repository matching the current `WorkItem.repository` into the per-item workspace on first creation.
- Preserve `workspace.hooks.after_create` as an optional post-checkout extension hook; existing hook-only workflows must remain compatible.
- Provide a dynamic default checkout path that works for the common case: clone `git@github.com:${SYMPHONY_REPOSITORY}.git` into `.`.
- Support per-repository checkout overrides for repositories that need a custom `clone_url`, `branch`, or `path`.
- Ensure Codex continues to run only inside the per-item workspace directory, never in the source repository or the workspace root.
- Update Settings types, defaults, import/export normalization, and UI so users can see and edit checkout mode/protocol/depth and repository overrides.
- Keep GitHub token handling unchanged: PAT is injected into Codex environment only when configured, and checkout itself should not log secrets.
- Update README/docs/examples so the default workflow explains repository allowlist, checkout, and hook responsibilities clearly.

## Acceptance Criteria

- [ ] Project items from repositories outside `tracker.repositories` are not dispatched.
- [ ] A new workspace for `owner/repo#123` clones the matching repository, not a hard-coded repository.
- [ ] Existing workflows with only `workspace.hooks.after_create` still run.
- [ ] Settings can persist and export the new checkout configuration.
- [ ] `WORKFLOW.example.md` demonstrates the new checkout configuration and no longer relies on a hard-coded single-repo clone as the only default.
- [ ] Unit tests cover config parsing, repository filtering, checkout command construction/execution, and settings normalization.
- [ ] Frontend typecheck passes after new settings fields are added.
- [ ] Backend tests pass for updated workspace/tracker behavior.

## Definition of Done

- Tests added or updated for backend config/workspace/tracker behavior and settings serialization.
- Frontend and backend type/lint checks run where available.
- Documentation and examples updated for the new checkout model.
- No unrelated refactors or destructive workspace/git operations.

## Technical Approach

Add explicit checkout support in the backend config model and workspace manager:

- Introduce `WorkspaceCheckoutConfig` with `mode`, `protocol`, `depth`, and per-repository overrides.
- Keep `mode: hook` or `mode: none` compatible for users who want to own all workspace population through hooks.
- Implement built-in `mode: clone` to clone the current item repository into the workspace before running `after_create`.
- Generate clone URLs from `item.repository` using `protocol` unless a repository override provides `clone_url`.
- Validate checkout path containment so custom paths cannot escape the workspace.
- Run checkout only on first workspace creation, matching current `after_create` semantics.
- Filter tracker items by configured repositories during normalization or immediately after normalization.

Update Settings:

- Extend shared settings types with `workspace.checkout`.
- Add UI fields in Workspace settings for checkout mode, protocol, depth, and repository overrides.
- Normalize legacy settings by defaulting to clone-by-current-repository while still preserving an existing `after_create` hook.

## Decision (ADR-lite)

**Context**: Upstream Symphony intentionally leaves workspace population to hooks, but GitHub Projects v2 introduces multi-repository dispatch where a hard-coded clone hook can checkout the wrong repository.

**Decision**: Keep hooks as an extension point and add a GitHub-aware `workspace.checkout` model for the common clone case. Also make `tracker.repositories` a real dispatch allowlist.

**Consequences**: The default path becomes safer and easier to configure. Advanced users can still use hooks. The backend must own more validation and tests around checkout behavior.

## Out of Scope

- Supporting non-GitHub Git hosts beyond explicit custom `clone_url` overrides.
- Maintaining multiple repositories inside one item workspace unless a custom hook implements it.
- Automatic branch selection from PR head refs; initial clone can use default branch or configured branch.
- Credential management changes beyond existing SSH/PAT environment behavior.

## Research References

- [`research/upstream-workspace-checkout.md`](research/upstream-workspace-checkout.md) — upstream and current-project evidence for workspace path and hook-based checkout behavior.

## Technical Notes

- Relevant backend files: `backend/src/symphony_github/core/config.py`, `backend/src/symphony_github/core/workspace.py`, `backend/src/symphony_github/integrations/github/tracker.py`, `backend/src/symphony_github/core/settings.py`.
- Relevant frontend files: `desktop/src/types.ts`, `desktop/src/settingsClient.ts`, `desktop/src/App.tsx`, `desktop/electron/main.ts`.
- Relevant docs/examples: `WORKFLOW.example.md`, `README.md`, `docs/architecture.md`.
- Specs for implement/check context should include backend config/quality, frontend React/types/components, and shared code quality/type rules.
