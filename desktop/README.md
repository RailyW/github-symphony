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

## UI 职责

- 展示运行中 agent、候选任务和最近事件。
- 提供刷新、停止本地 run、重启本地 run 的控制。
- 不直接保存 GitHub token；认证仍通过后端读取环境变量或配置完成。
