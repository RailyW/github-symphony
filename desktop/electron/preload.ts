import { contextBridge } from "electron";

// 函数说明：暴露最小只读配置给 renderer，避免开启 Node integration。
contextBridge.exposeInMainWorld("symphony", {
  apiBaseUrl: process.env.SYMPHONY_API_BASE_URL || "http://127.0.0.1:8765",
});
