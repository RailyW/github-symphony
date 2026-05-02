# Upstream Workspace Checkout Research

## Summary

OpenAI's upstream `symphony` does not model repository-to-local-path binding as a core tracker concept. It creates a deterministic per-issue workspace and delegates repository population to workspace lifecycle hooks, especially `after_create`.

## Upstream References

- Upstream repository inspected locally at `~/codex_temp/openai-symphony`, commit `58cf97da06d556c019ccea20c67f4f77da124bf3`.
- `elixir/WORKFLOW.md` configures `workspace.root` and uses `hooks.after_create` to run `git clone --depth 1 https://github.com/openai/symphony .`.
- `elixir/README.md` documents that `hooks.after_create` is the intended place to bootstrap a fresh workspace with `git clone ... .`.
- `SPEC.md` states that workspace population and synchronization are implementation-defined and typically handled by hooks, not by required built-in VCS behavior.
- `elixir/lib/symphony_elixir/workspace.ex` maps an issue identifier to `<workspace.root>/<safe_identifier>`, creates that directory, and runs `after_create` only on first creation.
- `elixir/lib/symphony_elixir/codex/app_server.ex` starts Codex with the per-issue workspace as `cwd` for both `thread/start` and `turn/start`.

## Current Project Mapping

- `tracker.owner_type`, `tracker.owner`, and `tracker.project_number` select the GitHub Project v2 board.
- Each Project item gets its repository from GraphQL `content.repository.nameWithOwner`.
- `tracker.repositories` is currently validated as non-empty `owner/repo` strings and primarily used as a GitHub REST allowlist.
- Settings discovery scans Project items and returns the repositories found in Issue/PR content.
- `WorkspaceManager` creates `<workspace.root>/<sanitize(item.identifier)>` and runs `workspace.hooks.after_create` only when the directory is first created.
- The hook receives `SYMPHONY_REPOSITORY`, `SYMPHONY_IDENTIFIER`, `SYMPHONY_NUMBER`, `SYMPHONY_KIND`, and `SYMPHONY_WORKSPACE`.
- Codex app-server is started in that workspace and receives the same workspace as thread and turn `cwd`.

## Gap

The current GitHub product can read multi-repository Projects, but the default workspace hook is still a single hard-coded clone command. This means the configured Project item repository, REST allowlist, and actual local checkout can drift apart.

## Recommended Direction

Keep upstream-compatible workspace hooks as an extension point, but add explicit GitHub-aware checkout configuration:

- Make `tracker.repositories` an actual dispatch allowlist.
- Add `workspace.checkout` for first-class repository checkout behavior.
- Support a dynamic default that clones the current `SYMPHONY_REPOSITORY`.
- Validate that a work item's repository is both allowed by `tracker.repositories` and resolvable by checkout configuration before Codex runs.
- Preserve hooks for post-clone setup and advanced workflows.
