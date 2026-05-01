import type { EventRecord, LogConfig, LogQueryFilters, LogQueryResult, StateSnapshot } from "./types";

// 函数说明：读取 preload 注入的 API 地址，浏览器调试时回退到默认端口。
export function apiBaseUrl(): string {
  return window.symphony?.apiBaseUrl || "http://127.0.0.1:8765";
}

// 函数说明：封装 JSON 请求，统一错误信息。
async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }

  return (await response.json()) as T;
}

// 函数说明：读取服务状态快照。
export function fetchState(): Promise<StateSnapshot> {
  return requestJson<StateSnapshot>("/api/v1/state");
}

// 函数说明：触发后端刷新。
export function refreshState(): Promise<{ status: string }> {
  return requestJson<{ status: string }>("/api/v1/refresh", { method: "POST" });
}

// 函数说明：停止本地 run。
export function stopRun(issueId: string): Promise<{ status: string }> {
  return requestJson<{ status: string }>(`/api/v1/runs/${encodeURIComponent(issueId)}/stop`, {
    method: "POST",
  });
}

// 函数说明：重启本地 run。
export function restartRun(issueId: string): Promise<{ status: string }> {
  return requestJson<{ status: string }>(`/api/v1/runs/${encodeURIComponent(issueId)}/restart`, {
    method: "POST",
  });
}

// 函数说明：按 cursor 获取事件。
export function fetchEvents(cursor?: number): Promise<{ events: EventRecord[]; next_cursor: number }> {
  const query = cursor == null ? "" : `?cursor=${cursor}`;
  return requestJson<{ events: EventRecord[]; next_cursor: number }>(`/api/v1/events${query}`);
}

// 函数说明：读取后端持久日志配置；Electron 模式优先走 preload IPC。
export function fetchLogConfig(): Promise<LogConfig> {
  if (window.symphonyLogs) {
    return window.symphonyLogs.config();
  }
  return requestJson<LogConfig>("/api/v1/logs/config");
}

// 函数说明：查询结构化日志；空过滤条件不会拼进 URL。
export function queryLogs(filters: LogQueryFilters): Promise<LogQueryResult> {
  if (window.symphonyLogs) {
    return window.symphonyLogs.query(filters);
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value == null || value === "") {
      continue;
    }
    params.set(key, String(value));
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson<LogQueryResult>(`/api/v1/logs/query${suffix}`);
}

// 函数说明：导出诊断包；浏览器调试模式直接请求本地后端。
export function exportLogBundle(): Promise<{ path: string }> {
  if (window.symphonyLogs) {
    return window.symphonyLogs.exportBundle();
  }
  return requestJson<{ path: string }>("/api/v1/logs/export", { method: "POST" });
}

// 函数说明：打开日志目录；浏览器调试模式无法访问本地 shell，返回明确错误。
export function openLogDirectory(): Promise<{ ok: boolean; error?: string }> {
  if (window.symphonyLogs) {
    return window.symphonyLogs.openDirectory();
  }
  return Promise.resolve({ ok: false, error: "打开日志目录需要在 Electron App 中使用" });
}
