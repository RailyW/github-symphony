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

开发模式和打包模式都会优先读取 Electron `userData/settings.json` 作为运行配置。GitHub token 通过 Electron `safeStorage` 加密后保存到 `userData/secrets.json`，renderer 无法读取明文 token。

## 打包运行

打包模式依赖 `../backend/dist/symphony-github-backend/symphony-github-backend`。先在仓库根目录执行 PyInstaller 命令生成后端 sidecar，再执行：

```bash
npm run package
```

生成的 macOS DMG 位于 `release/GitHub Symphony-0.1.0-arm64.dmg`。当前默认使用 ad-hoc 签名，未做 Apple Developer ID notarization。

## UI 职责

- `Dashboard` 展示运行中 agent、候选任务和最近事件。
- `Settings` 提供 GitHub Project、Workspace、Agent、Codex、Tools、Prompt 分区配置。
- `Settings` 支持导入/导出 `WORKFLOW.md`，导出时只写 `$GITHUB_TOKEN` 占位符。
- `Help` 提供面向普通开发者的使用说明、GitHub Project 概念和排错指南。
- Renderer 不直接保存 GitHub token；token 由 Electron main process 使用 `safeStorage` 保存。
