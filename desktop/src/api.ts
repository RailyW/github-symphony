import type { EventRecord, StateSnapshot } from "./types";

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
