# Desktop

`desktop/` 是 GitHub Symphony 的 Electron + React 客户端。它负责启动本地 Python 后端，并把后端状态展示为可操作的桌面仪表盘。

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

## 打包运行

打包模式依赖 `../backend/dist/symphony-github-backend/symphony-github-backend`。先在仓库根目录执行 PyInstaller 命令生成后端 sidecar，再执行：

```bash
npm run package
```

生成的 macOS DMG 位于 `release/GitHub Symphony-0.1.0-arm64.dmg`。当前默认使用 ad-hoc 签名，未做 Apple Developer ID notarization。

## UI 职责

- 展示运行中 agent、候选任务和最近事件。
- 提供刷新、停止本地 run、重启本地 run 的控制。
- 不直接保存 GitHub token；认证仍通过后端读取环境变量或配置完成。
