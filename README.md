# GitHub Symphony

GitHub Symphony 是一个面向 GitHub Projects v2 的本地 Codex 编排器。它参考 OpenAI Symphony 的概念：长期运行的服务持续读取任务板，在满足状态、依赖和并发条件时，为每个任务创建独立工作区并启动 Codex app-server 处理工作。

当前仓库采用 monorepo：

- `backend/`：Python 后端，负责 App settings、`WORKFLOW.md` 导入导出、GitHub Projects v2、Issue dependencies、Codex app-server、调度和本地 API。
- `desktop/`：Electron + React 桌面客户端，负责启动后端、保存本地设置并展示运行仪表盘。
- `docs/`：架构、权限、开发和打包说明。

## 当前实现范围

- 支持 App 内设置作为桌面端运行配置来源，并保留 `WORKFLOW.md` 导入/导出。
- 支持 GitHub Projects v2 tracker 的核心接口：候选任务、按状态查询、按 ID 刷新状态、状态字段更新 payload。
- 支持 GitHub Issue dependencies 优先的阻塞策略，API 不可用时按配置降级。
- 支持 Codex app-server JSON-RPC stdio 客户端和 `github_graphql` / `github_rest` 动态工具骨架。
- 支持 FastAPI 本地 API、settings 校验、settings 热应用和运行状态快照。
- 支持 Electron/React Dashboard、Settings、Help 三个页面；GitHub token 使用 Electron `safeStorage` 本地加密保存。

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

桌面端优先使用 Settings 页面保存的 GitHub token，并通过 Electron `safeStorage` 加密写入本机 `userData/secrets.json`。CLI 模式仍默认读取 `GITHUB_TOKEN`。PAT 需要能读取目标 Project v2、Issue/PR、依赖关系；如果启用动态工具写能力，还需要相应仓库写权限。详见 [docs/github-permissions.md](docs/github-permissions.md)。

## 开发约定

- 不由调度器自动 `commit`、`merge`、`push` 或删除远端内容。
- Codex agent 如果通过动态工具执行写操作，必须由 Prompt、工具模式和 token 权限共同允许。
- 所有复杂模块都在模块目录内包含 README，便于审查职责边界。
