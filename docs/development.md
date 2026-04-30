# Development

## Backend

```bash
cd /Users/jeff/project/github-symphony/backend
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -e ".[dev,package]"
./.venv/bin/python -m pytest tests -q
./.venv/bin/symphony-github doctor
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

打包分两步：

1. 用 PyInstaller 把 `backend` 打成 macOS/Windows sidecar。
2. 用 `electron-builder` 把 sidecar 和 React 静态资源一起打包。

macOS arm64 当前验证命令：

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

`desktop/package.json` 会把 `../backend/dist/symphony-github-backend` 复制到 Electron resources 中。打包后的 Electron main process 直接启动 `Contents/Resources/backend/symphony-github-backend/symphony-github-backend`，不再依赖开发机里的 `.venv`。

Windows 包应在 Windows 环境重复同样的 PyInstaller sidecar 步骤，再执行 `npm run package` 生成 NSIS 安装包；当前 macOS 环境没有执行 Windows 原生打包验证。

## Verification

默认验证不触发真实 GitHub 写操作：

```bash
backend/.venv/bin/python -m compileall backend/src
backend/.venv/bin/python -m pytest backend/tests -q
desktop/node_modules/.bin/electron-builder --version
```

真实 GitHub smoke test 后续应通过 `SYMPHONY_GITHUB_LIVE=1` 显式开启，并使用独立测试 Project。
