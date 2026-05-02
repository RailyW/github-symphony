# GitHub Symphony

GitHub Symphony 是一个面向 GitHub Projects v2 的本地 Codex 编排器。本项目以 OpenAI 在 GitHub 开源的 [symphony](https://github.com/openai/symphony) 仓库为模板进行二次开发：长期运行的服务持续读取任务板，在满足状态、依赖和并发条件时，为每个任务创建独立工作区并启动 Codex app-server 处理工作。

当前仓库采用 monorepo：

- `backend/`：Python 后端，负责 App settings、`WORKFLOW.md` 导入导出、GitHub Projects v2、Issue dependencies、Codex app-server、调度和本地 API。
- `desktop/`：Electron + React 桌面客户端，负责启动后端、保存本地设置并展示运行仪表盘。
- `docs/`：架构、权限、开发和打包说明。

## 当前实现范围

- 支持 App 内设置作为桌面端运行配置来源，并保留 `WORKFLOW.md` 导入/导出。
- 支持 GitHub Projects v2 tracker 的核心接口：候选任务、按状态查询、按 ID 刷新状态、状态字段更新 payload。
- 支持 GitHub Issue dependencies 优先的阻塞策略，API 不可用时按配置降级。
- 支持把 `tracker.repositories` 作为 Project item 派发 allowlist，并在新任务工作区中按当前 Issue/PR 所属仓库执行内置 checkout。
- 支持 Codex app-server JSON-RPC stdio 客户端和 `github_graphql`、`github_rest`、`github_update_project_status` 动态工具。
- 支持 FastAPI 本地 API、settings 校验、settings 热应用和运行状态快照。
- 支持 Electron/React Dashboard、Settings、Help 三个页面；GitHub token 使用 Electron `safeStorage` 本地加密保存。
- 支持 Settings 中的 PAT 驱动 GitHub 配置向导：读取 owner、Projects v2、Status 字段、状态选项和 Project 中已有 Issue/PR 仓库，用户不再需要手填大部分 GitHub tracker 参数。
- 默认 Autonomy preset 为 `PR 前全自动`：agent 在隔离工作区内执行 Workpad、分支、commit、push、PR、PR feedback sweep 和 checks green；人工把 Project Status 移到 `Merging` 后，agent 才执行 land 流程。

## 快速开始

后端要求 Python 3.11+。当前实现尽量保持基础模块可被系统 Python 3.9 编译和单元测试，但正式运行建议使用 Python 3.11+。

```bash
cd /Users/jeff/project/github-symphony/backend
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev,package]"
./.venv/bin/symphony-github doctor
./.venv/bin/symphony-github run ../WORKFLOW.example.md --host 127.0.0.1 --port 8765
```

桌面端：

```bash
cd /Users/jeff/project/github-symphony/desktop
npm install
npm run dev
```

注意：安装依赖会从第三方包仓库下载软件包，执行前请自行确认网络与供应链策略。

## 桌面成品包

macOS 当前可通过 PyInstaller sidecar + electron-builder 生成 DMG：

```bash
cd /Users/jeff/project/github-symphony
backend/.venv/bin/pyinstaller --clean -y \
  --name symphony-github-backend \
  --distpath backend/dist \
  --workpath backend/build \
  --specpath backend/build \
  --paths backend/src \
  --collect-submodules uvicorn \
  --collect-submodules fastapi \
  --collect-submodules starlette \
  --collect-submodules pydantic \
  backend/packaging/backend_entry.py

cd /Users/jeff/project/github-symphony/desktop
npm run package
```

生成产物位于 `desktop/release/GitHub Symphony-0.1.0-arm64.dmg`。当前包未做 Developer ID notarization，分发给其他 macOS 设备时可能触发 Gatekeeper 提示。

## GitHub Token

桌面端优先使用 Settings 页面保存的 GitHub token，并通过 Electron `safeStorage` 加密写入本机 `userData/secrets.json`。Settings 的 Connect/Test PAT discovery 只临时使用 token，不保存到磁盘；只有 Save 或 Save & Apply 会保存新 token。CLI 模式仍默认读取 `GITHUB_TOKEN`。运行 Codex app-server 时，如果 tracker token 已配置，后端会把它注入子进程环境变量 `GITHUB_TOKEN` 和 `GH_TOKEN`，但不会写入事件或诊断日志。PAT 需要能读取目标 Project v2、Issue/PR、依赖关系；如果启用 `read_write` 动态工具和 PR 前自治，还需要相应仓库写权限。详见 [docs/github-permissions.md](docs/github-permissions.md)。

## Workspace Checkout

`tracker.repositories` 是工作流允许派发和暴露 GitHub REST 工具的仓库列表。Project item 的 `content.repository.nameWithOwner` 不在该列表内时，调度器会跳过该 item 并写入诊断事件。

新工作区默认使用 `workspace.checkout.mode=clone`，按当前 `WorkItem.repository` 生成 `git@github.com:owner/repo.git` 并 clone 到该任务工作区的 `.`。`protocol=https` 可改为 HTTPS URL；`depth` 控制浅克隆；`workspace.checkout.repositories` 可以对单个仓库覆盖 `clone_url`、`branch` 或 `path`。`workspace.hooks.after_create` 仍会在首次创建工作区时执行，但语义变为 checkout 之后的扩展 hook；旧版只有 `after_create`、没有 `checkout` 的 WORKFLOW 会自动保持 `mode=hook`，避免重复 clone。

## 开发约定

- 不由调度器把 `commit`、`push`、`merge` 或删除远端内容做成内置业务动作。
- Codex agent 如果执行远端写操作，必须由 Prompt、approval policy、工具模式和 token 权限共同允许；新 settings 默认 high-trust preset 会自动批准 command、file、permissions、applyPatch、exec approval，并为工具 user-input 选择可继续项，只应配合隔离 workspace、受限 token 和可信 prompt 使用。
- 默认禁止 force push、直接 push 默认分支、删除远端分支和用 PR closing keyword 自动关闭 issue；任务完成以 Project Status `Done` 为准。
- 所有复杂模块都在模块目录内包含 README，便于审查职责边界。
