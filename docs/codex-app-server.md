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

当前实现注册三个工具：

- `github_graphql`
- `github_rest`
- `github_update_project_status`

当 tracker token 已配置时，Codex 子进程会收到临时环境变量 `GITHUB_TOKEN` 和 `GH_TOKEN`，供 GitHub CLI、git credential helper 或动态工具使用。该 token 不会写入 app-server 事件 payload；诊断日志导出仍会做脱敏处理。

默认 approval handler 会拒绝需要人工确认的请求，避免无人值守场景中意外扩大权限。新 settings 默认使用 `high-trust` preset 并由后端归一化为 app-server 已支持的 `never`；已有 settings 缺少该字段时不会静默升权。高信任模式会自动批准 command、file、permissions、applyPatch、exec approval，并为工具 user-input 选择可继续项；这种模式应只用于隔离工作区、受限 token 和可信 prompt。

如果后续 Codex app-server 修改 `dynamicTools` 实验字段，优先使用：

```bash
codex app-server generate-json-schema --out /tmp/codex-schema
```

并对照更新 `backend/src/symphony_github/codex/app_server.py`。
