// 逻辑说明：Electron preload 在当前打包方式下按 CommonJS 脚本执行。
// 使用 .cts 能让 TypeScript 在 NodeNext 模式下输出 preload.cjs，
// 避免 ESM import/export 在已安装 .app 中触发 preload 语法错误。
const { contextBridge } = require("electron") as {
  contextBridge: {
    exposeInMainWorld: (apiKey: string, api: unknown) => void;
  };
};
const { ipcRenderer } = require("electron") as {
  ipcRenderer: {
    invoke: (channel: string, ...args: unknown[]) => Promise<unknown>;
  };
};

// 函数说明：暴露最小只读配置给 renderer，避免开启 Node integration。
contextBridge.exposeInMainWorld("symphony", {
  apiBaseUrl: process.env.SYMPHONY_API_BASE_URL || "http://127.0.0.1:8765",
});

// 函数说明：暴露设置页需要的最小 IPC 能力；renderer 不直接访问文件系统或明文 token。
contextBridge.exposeInMainWorld("symphonySettings", {
  load: () => ipcRenderer.invoke("settings:load"),
  save: (settings: unknown, tokenUpdate: unknown) => (
    ipcRenderer.invoke("settings:save", settings, tokenUpdate)
  ),
  apply: (settings: unknown) => ipcRenderer.invoke("settings:apply", settings),
  importWorkflow: () => ipcRenderer.invoke("settings:import-workflow"),
  exportWorkflow: (settings: unknown) => ipcRenderer.invoke("settings:export-workflow", settings),
  tokenStatus: () => ipcRenderer.invoke("settings:token-status"),
});
