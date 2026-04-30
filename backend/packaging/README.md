# Backend Packaging

`backend/packaging/` 保存把 Python 后端编译为桌面 sidecar 的入口文件。

## 文件职责

- `backend_entry.py`：PyInstaller 使用的最小入口，等价于运行 `python -m symphony_github`，最终会生成名为 `symphony-github-backend` 的独立可执行文件。

## 打包约定

PyInstaller 产物必须输出到 `backend/dist/symphony-github-backend`，因为 `desktop/package.json` 的 `extraResources` 会从该路径复制资源。Electron 打包后会从 `Contents/Resources/backend/symphony-github-backend/symphony-github-backend` 启动后端。

该目录只保存打包入口和说明，不保存真实构建产物；构建产物由 `.gitignore` 排除。
