# API

`api` 模块向 Electron/React 客户端提供本地 HTTP 控制面。

Electron 打包版通过 `file://` 读取 React 页面，因此浏览器会把页面到 `http://127.0.0.1` 的请求视为跨源请求。`server.py` 已启用 FastAPI CORS middleware，允许本地桌面页面访问 API；服务仍应保持仅监听 loopback 地址。

## 端点

- `GET /api/v1/state`
- `GET /api/v1/settings/default`
- `POST /api/v1/settings/validate`
- `POST /api/v1/settings/apply`
- `POST /api/v1/settings/import-workflow`
- `POST /api/v1/settings/export-workflow`
- `POST /api/v1/settings/discovery/connect`
- `POST /api/v1/settings/discovery/projects`
- `POST /api/v1/settings/discovery/project`
- `GET /api/v1/issues/{id}`
- `POST /api/v1/refresh`
- `POST /api/v1/runs/{issue_id}/restart`
- `POST /api/v1/runs/{issue_id}/stop`
- `GET /api/v1/events?cursor=`

## 约束

服务只应绑定到 `127.0.0.1`。这些端点用于本地桌面客户端和开发调试，不应暴露到公网。

Settings API 接收的是 App 内配置结构。`apply` 会热替换后续调度使用的 tracker、runner factory 和 prompt；已经运行的 agent 不会被取消。`export-workflow` 只输出 `$GITHUB_TOKEN` 占位符，不会返回 Electron 安全存储里的真实 token。

Discovery API 是 Settings 页面使用的只读 GitHub 配置向导入口。它接收临时 PAT 或 Electron main 解密出的已保存 token，读取 owner、Projects v2、Project 字段和 Project item 仓库信息；这些端点不会保存 token、不会修改 GitHub，也不会触发调度器热应用。
