# API

`api` 模块向 Electron/React 客户端提供本地 HTTP 控制面。

Electron 打包版通过 `file://` 读取 React 页面，因此浏览器会把页面到 `http://127.0.0.1` 的请求视为跨源请求。`server.py` 已启用 FastAPI CORS middleware，允许本地桌面页面访问 API；服务仍应保持仅监听 loopback 地址。

## 端点

- `GET /api/v1/state`
- `GET /api/v1/issues/{id}`
- `POST /api/v1/refresh`
- `POST /api/v1/runs/{issue_id}/restart`
- `POST /api/v1/runs/{issue_id}/stop`
- `GET /api/v1/events?cursor=`

## 约束

服务只应绑定到 `127.0.0.1`。这些端点用于本地桌面客户端和开发调试，不应暴露到公网。
