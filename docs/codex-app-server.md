# Codex App Server Notes

GitHub Symphony 使用 Codex app-server 的 stdio JSON-RPC 协议。

## Startup

后端按顺序发送：

1. `initialize`
2. `thread/start`
3. `turn/start`

`initialize` 会设置：

```json
{
  "capabilities": {
    "experimentalApi": true
  }
}
```

`thread/start` 会传入：

- `cwd`
- `approvalPolicy`
- `sandbox`
- `serviceName`
- `dynamicTools`

## Dynamic Tools

当前实现注册两个工具：

- `github_graphql`
- `github_rest`

如果后续 Codex app-server 修改 `dynamicTools` 实验字段，优先使用：

```bash
codex app-server generate-json-schema --out /tmp/codex-schema
```

并对照更新 `backend/src/symphony_github/codex/app_server.py`。
