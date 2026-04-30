# Codex App Server Client

该模块封装 Codex app-server 的 JSON-RPC stdio 协议。

## 协议策略

- 启动命令默认 `codex app-server`。
- 首先发送 `initialize`，并启用 `capabilities.experimentalApi = true`。
- 然后发送 `thread/start`，在实验字段 `dynamicTools` 中注册 GitHub 工具。
- 每个 agent turn 使用 `turn/start`。
- `item/tool/call` 由本地动态工具执行器处理，并以 JSON-RPC response 返回。

当前实现刻意保持协议处理小而明确。Codex app-server 协议升级时，应先用 `codex app-server generate-json-schema` 对照本模块。
