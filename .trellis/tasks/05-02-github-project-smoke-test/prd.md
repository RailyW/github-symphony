# GitHub Project Smoke Test Enablement

## Goal

Make the GitHub Project smoke flow work from the packaged Electron GUI by preserving Codex's native credential behavior, normalizing legacy checkout settings, and preventing reuse of stale workspaces that point at the wrong repository.

## What I Already Know

- GitHub Project scanning and dispatch already work.
- The GUI-launched backend does not inherit `codex_rin977_key` from `~/.zshrc`, so Codex subprocesses can miss provider credentials.
- Legacy Settings can still contain a hook placeholder that clones `your-org/your-repo`.
- Existing erroneous workspaces can be reused even when their `origin` remote does not match the Project item's repository.
- The implementation must avoid writing provider keys into Settings or secrets files.
- The implementation must not overwrite existing environment variables and must not log secret values.

## Requirements

- Electron main detects an optional Codex `env_key` by conservatively parsing `~/.codex/config.toml` for line-level `env_key = "..."` or `env_key = '...'`.
- Missing, unreadable, or env-key-free config must be a native/skip path: no error, no blocking, no shell environment import.
- Only when `env_key` is found, Electron may run `$SHELL -ilc "/usr/bin/env -0"` with a short timeout.
- Electron imports only the config-declared key and only when `process.env` does not already define it.
- Electron must never merge the whole shell environment and must never log secret values.
- Electron and Python redaction rules must include `*_key`, `api_key`, and `*_api_key` names.
- Legacy Settings migration must convert `checkout.mode=hook` with an `after_create` containing `your-org/your-repo` into `checkout.mode=clone`, `protocol=ssh`, `depth=1`, and `after_create=null`.
- Electron Settings and browser fallback Settings must share the same migration semantics.
- `WorkspaceManager.prepare()` must reject an existing clone-mode workspace whose git `origin` repository differs from the current Project item repository.
- New clone workspace behavior must stay unchanged.
- `codex_subprocess_env` must remain simple and no plaintext Codex/provider key setting may be added.

## Acceptance Criteria

- Backend tests cover new clone URL behavior, existing matching remote reuse, existing `your-org/your-repo` remote mismatch rejection, and diagnostics redaction for `codex_rin977_key`, `openai_api_key`, and `api_key`.
- `npm --prefix desktop run build` passes, or any failure is reported with the exact blocker.
- Relevant Python tests pass, or any failure is reported with the exact blocker.
- Documentation is updated if runtime behavior changes are user-facing.

## Out Of Scope

- Adding a plaintext Codex provider key setting.
- Deleting or automatically repairing mismatched workspace directories.
- Replacing Codex native credential resolution.
- Pushing, committing, or destructive git operations.

## Technical Notes

- Relevant files requested by the user:
  - `desktop/electron/main.ts`
  - `desktop/src/settingsClient.ts`
  - `desktop/src/App.tsx`
  - `desktop/src/types.ts` if required
  - `backend/src/symphony_github/core/workspace.py`
  - `backend/src/symphony_github/core/diagnostics.py`
  - `backend/tests/test_core.py`
  - `desktop/README.md` or module README if behavior changes
- Relevant specs:
  - `.trellis/spec/backend/workspace-checkout.md`
  - `.trellis/spec/backend/logging.md`
  - `.trellis/spec/backend/error-handling.md`
  - `.trellis/spec/backend/quality.md`
  - `.trellis/spec/frontend/ipc-electron.md`
  - `.trellis/spec/frontend/electron-browser-api-restrictions.md`
  - `.trellis/spec/frontend/react-pitfalls.md`
  - `.trellis/spec/frontend/type-safety.md`
  - `.trellis/spec/frontend/quality.md`
  - `.trellis/spec/shared/code-quality.md`
  - `.trellis/spec/shared/typescript.md`
