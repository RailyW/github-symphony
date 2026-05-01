# Backend

`backend/` 是 GitHub Symphony 的 Python 后端。它负责读取 `WORKFLOW.md`、连接 GitHub Projects v2、管理本地工作区、启动 Codex app-server，并向 Electron/React 客户端暴露本地 API。

## 模块边界

- `symphony_github.core`：配置、工作流文件、调度、事件、持久诊断日志、工作区和 prompt 渲染。
- `symphony_github.integrations.github`：GitHub GraphQL/REST client、Projects v2 tracker、Issue dependencies 和动态工具。
- `symphony_github.codex`：Codex app-server JSON-RPC stdio client。
- `symphony_github.api`：FastAPI 本地 HTTP API。

## 运行

```bash
python -m pip install -e ".[dev]"
symphony-github doctor
symphony-github run ../WORKFLOW.example.md --host 127.0.0.1 --port 8765 --log-level debug
```

基础单元测试不依赖第三方包：

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

## 设计约束

- GitHub token 只在内存中使用，不写入日志、事件或诊断包。
- 调度器按配置的 `active_states`、`handoff_states`、`terminal_states` 和 `blocked_states` 理解任意 GitHub Project 自定义阶段。
- 默认 Autonomy preset 为 `PR 前全自动`，`completion_policy.kind=agent_managed`，由 prompt 和 GitHub 工具驱动 Workpad、分支、commit、push、PR、feedback sweep、`Human Review` 交接和 `Merging` land。
- 调度器不把 commit、push、merge 做成内置业务动作；GitHub 写能力仍受 PAT 权限、approval policy、动态工具模式和配置 allowlist 约束。
- CLI 日志默认写入 `~/.github-symphony/logs`；Electron 打包版通过 `SYMPHONY_LOG_DIR` 写入 `<userData>/logs`。
- 后端只监听 `127.0.0.1`，避免把本地控制面暴露到网络。
