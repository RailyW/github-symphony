# Codex App Server Client

该模块封装 Codex app-server 的 JSON-RPC stdio 协议。

## 协议策略

- 启动命令默认 `codex app-server`。
- 启动子进程时会补全常见 PATH，包括 Homebrew、系统目录和 `~/.nvm/versions/node/*/bin`；
  这样从 macOS GUI 打开的打包 App 也能找到 `codex` 与 `node`。
- 首先发送 `initialize`，并启用 `capabilities.experimentalApi = true`。
- 然后发送 `thread/start`，在实验字段 `dynamicTools` 中注册 GitHub 工具。
- 每个 agent turn 使用 `turn/start`。
- `item/tool/call` 由本地动态工具执行器处理，并以 JSON-RPC response 返回。
- 如果 app-server 在返回 JSON-RPC response 前退出，客户端会主动失败 pending request，
  让 runner 进入可观测错误和重试路径，而不是一直停留在 running。

当前实现刻意保持协议处理小而明确。Codex app-server 协议升级时，应先用 `codex app-server generate-json-schema` 对照本模块。
