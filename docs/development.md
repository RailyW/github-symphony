# Development

## Backend

```bash
cd /Users/jeff/project/github-symphony/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
PYTHONPATH=src python -m unittest discover -s tests
symphony-github doctor
```

当前机器如果只有 Python 3.9，也可以运行基础 `unittest` 和 `compileall`；正式运行建议 Python 3.11+。

## Desktop

```bash
cd /Users/jeff/project/github-symphony/desktop
npm install
SYMPHONY_WORKFLOW=/Users/jeff/project/github-symphony/WORKFLOW.example.md npm run dev
```

Electron main process 会启动：

```bash
python3 -m symphony_github run <workflow> --host 127.0.0.1 --port 8765
```

开发模式下它会把 `backend/src` 加入 `PYTHONPATH`。

## Packaging

建议分两步：

1. 用 PyInstaller 把 `backend` 打成 macOS/Windows sidecar。
2. 用 `electron-builder` 把 sidecar 和 React 静态资源一起打包。

当前仓库已经保留 `desktop/package.json` 的 `package` 脚本，但 sidecar 打包脚本需要在确认目标 Python 版本和签名策略后补齐。

## Verification

默认验证不触发真实 GitHub 写操作：

```bash
PYTHONPATH=backend/src python3 -m compileall backend/src
PYTHONPATH=backend/src python3 -m unittest discover -s backend/tests
```

真实 GitHub smoke test 后续应通过 `SYMPHONY_GITHUB_LIVE=1` 显式开启，并使用独立测试 Project。
