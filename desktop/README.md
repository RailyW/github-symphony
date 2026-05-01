# Desktop

`desktop/` 是 GitHub Symphony 的 Electron + React 客户端。它负责启动本地 Python 后端、保存 App 内设置，并把后端状态展示为可操作的桌面仪表盘。

## 开发运行

```bash
npm install
npm run dev
```

默认工作流路径：

```bash
SYMPHONY_WORKFLOW=/Users/jeff/project/github-symphony/WORKFLOW.example.md npm run dev
```

Electron 会把 `../backend/src` 注入 `PYTHONPATH`，因此开发模式不要求先把后端安装成全局包。打包模式应使用 PyInstaller 生成后端 sidecar，再由 Electron main process 启动 sidecar。

Electron main 会为后端子进程补全 GUI 启动时常缺失的命令路径，包括 Homebrew、系统目录和 `~/.nvm/versions/node/*/bin`。这保证打包 `.app` 从 Finder 打开时，后端后续启动 `codex app-server` 仍然能找到 `codex` 与 `node`。

开发模式和打包模式都会优先读取 Electron `userData/settings.json` 作为运行配置。GitHub token 通过 Electron `safeStorage` 加密后保存到 `userData/secrets.json`，renderer 无法读取明文 token。

Settings 的 GitHub Project 页是 PAT 驱动的配置向导：用户先粘贴 PAT 或选择已保存 token，Electron main 把 token 仅用于本次本地 discovery 请求；只有点击 Save 或 Save & Apply 时才会把新 token 加密保存。

Electron main 会把 `SYMPHONY_LOG_DIR=<userData>/logs` 传给 Python 后端。Electron 自身写入 `electron-main.jsonl`，后端写入 `backend.jsonl`；Logs 页面读取同一目录中的结构化日志，支持过滤、打开目录和导出脱敏诊断包。

## 打包运行

打包模式依赖 `../backend/dist/symphony-github-backend/symphony-github-backend`。先在仓库根目录执行 PyInstaller 命令生成后端 sidecar，再执行：

```bash
npm run package
```

生成的 macOS DMG 位于 `release/GitHub Symphony-0.1.0-arm64.dmg`。当前默认使用 ad-hoc 签名，未做 Apple Developer ID notarization。

## UI 职责

- `Dashboard` 展示运行中 agent、候选任务和最近事件。
- `Settings` 提供 GitHub Project、Workspace、Agent、Completion、Codex、Tools、Logging、Prompt 分区配置。
- `Settings / GitHub Project` 会从 GitHub 读取 owner、Projects v2、Status 字段、状态选项和 Project 中出现过的仓库，减少手工填写。
- `Settings / GitHub Project` 支持把任意自定义 Status 选项分配为 Active、Handoff、Terminal 三类阶段。
- `Settings / Completion` 默认在 Codex turn 成功后把 GitHub Project item 的 `Status` 更新到目标/交接阶段，防止任务仍处于 Active 阶段时被重复派发；也可切换为 `agent_managed` 由 prompt 和 GitHub 工具流转状态。
- `Settings` 支持导入/导出 `WORKFLOW.md`，导出时只写 `$GITHUB_TOKEN` 占位符。
- `Logs` 展示持久 JSONL 日志、日志目录、过滤器和诊断包导出入口。
- `Help` 提供面向普通开发者的使用说明、GitHub Project 概念和排错指南。
- Renderer 不直接保存 GitHub token；token 由 Electron main process 使用 `safeStorage` 保存。
