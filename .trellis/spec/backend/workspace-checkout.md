# Workspace Checkout Contract

## Scenario: GitHub Project Workspace Checkout

### 1. Scope / Trigger

- Trigger: any change to `workspace.checkout`, `tracker.repositories`, workspace creation, or GitHub Project item dispatch.
- Scope: backend workflow config parsing, workspace preparation, GitHub Project item filtering, Settings import/export payloads, and renderer Settings fields that mirror the backend contract.
- Goal: keep the Project item repository, REST allowlist, and local checkout target aligned before Codex starts.

### 2. Signatures

- Workflow config:
  ```yaml
  tracker:
    repositories:
      - owner/repo
  workspace:
    checkout:
      mode: clone | hook | none
      protocol: ssh | https
      depth: 1
      repositories:
        owner/repo:
          clone_url: git@github.com:owner/repo.git
          branch: main
          path: .
  ```
- Backend dataclasses:
  - `TrackerConfig.repositories: List[str]`
  - `WorkspaceConfig.checkout: WorkspaceCheckoutConfig`
  - `WorkspaceCheckoutConfig.mode: str`
  - `WorkspaceCheckoutConfig.protocol: str`
  - `WorkspaceCheckoutConfig.depth: Optional[int]`
  - `WorkspaceCheckoutConfig.repositories: Dict[str, WorkspaceCheckoutRepositoryConfig]`
- Workspace helper:
  - `build_checkout_plan(config, workspace, item) -> Optional[CheckoutPlan]`
- Settings payload:
  - `settings.workspace.checkout` must mirror the workflow fields above.

### 3. Contracts

- `tracker.repositories` is the dispatch allowlist and GitHub REST allowlist. Every entry must be a single `owner/repo` pair.
- A GitHub Project item must be skipped before dispatch if its `content.repository.nameWithOwner` is not in `tracker.repositories`.
- `workspace.checkout.mode=clone` runs built-in `git clone` only when the per-item workspace is newly created.
- `workspace.checkout.mode=hook` never runs built-in checkout; `workspace.hooks.after_create` remains responsible for preparing code.
- `workspace.checkout.mode=none` creates an empty workspace and still allows `after_create` if configured.
- Missing `workspace.checkout` plus a non-empty legacy `after_create` hook must normalize to `mode=hook` to prevent duplicate clones.
- Missing `workspace.checkout` without a legacy hook must default to `mode=clone`, `protocol=ssh`, and `depth=1`.
- Checkout runs before `workspace.hooks.after_create`.
- Hook and checkout subprocesses receive:
  - `SYMPHONY_ISSUE_ID`
  - `SYMPHONY_PROJECT_ITEM_ID`
  - `SYMPHONY_IDENTIFIER`
  - `SYMPHONY_REPOSITORY`
  - `SYMPHONY_NUMBER`
  - `SYMPHONY_KIND`
  - `SYMPHONY_WORKSPACE`
- Codex must start only after workspace preparation succeeds and must use the per-item workspace as `cwd`.

### 4. Validation & Error Matrix

- `tracker.repositories` empty -> config error.
- `tracker.repositories` contains duplicates -> config error.
- `tracker.repositories` entry is not exactly `owner/repo` -> config error.
- `workspace.checkout.mode` outside `clone | hook | none` -> config error.
- `workspace.checkout.protocol` outside `ssh | https` -> config error.
- `workspace.checkout.depth < 1` -> config error; `null` or empty means full clone.
- `workspace.checkout.repositories` key not in `tracker.repositories` -> config error.
- `workspace.checkout.repositories.*.path` empty -> config error.
- checkout path resolves outside the item workspace -> `WorkspaceError`.
- `git clone` exits non-zero -> `WorkspaceError` with stderr redacted.
- checkout fails during first workspace creation -> remove the partially prepared workspace before rethrowing.
- hook fails after checkout -> `WorkspaceError` with stderr redacted; for clone mode, remove the partially prepared workspace before rethrowing.
- URL credentials in clone stderr -> redact before surfacing diagnostics.

### 5. Good/Base/Bad Cases

- Good: Project item `owner/repo#123` is in `tracker.repositories`; `mode=clone` clones `git@github.com:owner/repo.git` into `.` and then runs `after_create`.
- Good: `workspace.checkout.repositories.owner/repo.clone_url` overrides the generated URL for Enterprise or unusual remotes.
- Base: legacy workflow has only `after_create: git clone ... .`; normalized checkout mode is `hook`, so only the hook runs.
- Base: `depth: null` creates a full clone command without `--depth`.
- Bad: Project item from `other/repo` appears in the same Project; tracker logs a debug skip and never dispatches it.
- Bad: custom checkout path `../repo` resolves outside the item workspace and must be rejected.
- Bad: failed clone leaves no prepared workspace behind, so the next scheduler retry can attempt checkout again.

### 6. Tests Required

- Config parsing:
  - default checkout values
  - legacy hook-only compatibility
  - strict `tracker.repositories` format and duplicate validation
  - checkout override keys must be in tracker allowlist
  - invalid checkout mode/protocol/depth/path
- Workspace behavior:
  - generated SSH and HTTPS clone commands
  - custom `clone_url`, `branch`, and `path`
  - checkout runs before hook
  - non-clone modes do not run built-in checkout
  - checkout path containment
  - clone failure redacts secrets and cleans the new workspace
- Tracker behavior:
  - out-of-allowlist Project items are skipped
  - in-allowlist Project items still normalize and fetch blockers as before
- Settings behavior:
  - default settings include checkout
  - import/export preserves checkout
  - legacy saved settings with hook-only workspace normalize to `mode=hook`
- UI/type behavior:
  - renderer and Electron typecheck pass after settings type changes.

### 7. Wrong vs Correct

#### Wrong

```yaml
tracker:
  repositories:
    - owner/repo
workspace:
  hooks:
    after_create: |
      git clone git@github.com:owner/repo.git .
```

This is acceptable only for a legacy single-repository workflow. In a multi-repository Project, it can clone `owner/repo` for an issue that actually belongs to another allowed repository.

#### Correct

```yaml
tracker:
  repositories:
    - owner/api
    - owner/web
workspace:
  checkout:
    mode: clone
    protocol: ssh
    depth: 1
    repositories:
      owner/api:
        branch: main
      owner/web:
        clone_url: git@github.com:owner/web.git
  hooks:
    after_create: |
      test -f package.json && npm install
```

The checkout target now follows `SYMPHONY_REPOSITORY`, while the hook is limited to post-checkout setup.
