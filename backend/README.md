# Backend

`backend/` 是 GitHub Symphony 的 Python 后端。它负责读取 `WORKFLOW.md`、连接 GitHub Projects v2、管理本地工作区、启动 Codex app-server，并向 Electron/React 客户端暴露本地 API。

## 模块边界

- `symphony_github.core`：配置、工作流文件、调度、事件、工作区和 prompt 渲染。
- `symphony_github.integrations.github`：GitHub GraphQL/REST client、Projects v2 tracker、Issue dependencies 和动态工具。
- `symphony_github.codex`：Codex app-server JSON-RPC stdio client。
- `symphony_github.api`：FastAPI 本地 HTTP API。

## 运行

```bash
python -m pip install -e ".[dev]"
symphony-github doctor
symphony-github run ../WORKFLOW.example.md --host 127.0.0.1 --port 8765
```

基础单元测试不依赖第三方包：

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## 设计约束

- GitHub token 只在内存中使用，不写入日志或事件。
- 调度器默认不写 GitHub 状态；写能力只通过显式配置的动态工具暴露给 Codex agent。
- 后端只监听 `127.0.0.1`，避免把本地控制面暴露到网络。
