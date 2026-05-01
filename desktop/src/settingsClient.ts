import { apiBaseUrl } from "./api";
import type {
  AppSettings,
  GitHubDiscoveryConnectResult,
  GitHubDiscoveryRequest,
  GitHubProjectDiscoveryResult,
  GitHubProjectOption,
  SettingsLoadResult,
  TokenStatus,
  TokenUpdate,
} from "./types";

// 函数说明：创建浏览器调试模式可用的默认设置；Electron 运行时优先使用 preload IPC。
export function defaultSettings(): AppSettings {
  return {
    tracker: {
      owner_type: "org",
      owner: "your-org",
      project_number: 12,
      repositories: ["your-org/your-repo"],
      status_field: "Status",
      active_states: ["Todo", "In Progress", "Rework"],
      terminal_states: ["Done", "Closed", "Cancelled"],
      priority_field: "Priority",
      api_base_url: "https://api.github.com",
      graphql_url: "https://api.github.com/graphql",
    },
    blocker_policy: {
      kind: "github_issue_dependencies",
      unavailable_behavior: "treat_unblocked",
    },
    workspace: {
      root: "~/code/github-symphony-workspaces",
      cleanup_terminal_workspaces: false,
      hooks: {
        after_create: "git clone git@github.com:your-org/your-repo.git .",
      },
    },
    agent: {
      max_concurrent_agents: 3,
      max_turns: 20,
      poll_interval_ms: 10000,
      max_retry_backoff_ms: 300000,
    },
    codex: {
      command: "codex app-server",
      model: "gpt-5.5",
      approval_policy: {
        granular: {
          sandbox_approval: true,
          rules: true,
          mcp_elicitations: true,
        },
      },
      thread_sandbox: "workspace-write",
      turn_sandbox_policy: {
        type: "workspaceWrite",
        networkAccess: true,
      },
    },
    tools: {
      github: {
        enabled: true,
        mode: "read_write",
      },
    },
    completion_policy: {
      kind: "update_project_status",
      success_state: "Done",
      failure_state: "Rework",
      mark_done_after_successful_turn: true,
      close_issue: false,
    },
    logging: {
      level: "DEBUG",
      retention_days: 14,
      max_file_mb: 10,
    },
    prompt_template: [
      "你正在处理 GitHub 任务：",
      "",
      "- 标识：`{{ issue.identifier }}`",
      "- 标题：`{{ issue.title }}`",
      "- 仓库：`{{ issue.repository }}`",
      "- 链接：`{{ issue.url }}`",
      "",
      "请先阅读 issue/PR 描述和仓库代码，再实施最小必要修改。完成后请在 GitHub 中留下清晰的工作说明、验证结果和剩余风险。",
    ].join("\n"),
  };
}

// 函数说明：读取 App settings；浏览器调试模式下使用 localStorage fallback。
export async function loadSettings(): Promise<SettingsLoadResult> {
  if (window.symphonySettings) {
    return window.symphonySettings.load();
  }

  const stored = window.localStorage.getItem("github-symphony-settings");
  const settings = stored ? mergeSettingsWithDefaults(JSON.parse(stored)) : defaultSettings();
  return {
    settings,
    token: { configured: false, encryptionAvailable: false },
    settingsPath: "localStorage:github-symphony-settings",
  };
}

// 函数说明：把旧版 localStorage settings 与当前默认结构合并，补齐新增字段。
function mergeSettingsWithDefaults(stored: unknown): AppSettings {
  return deepMerge(defaultSettings(), stored) as AppSettings;
}

// 函数说明：递归合并普通对象；数组和标量保留用户保存值。
function deepMerge(defaults: unknown, stored: unknown): unknown {
  if (!isPlainObject(defaults) || !isPlainObject(stored)) {
    return stored ?? defaults;
  }
  const result: Record<string, unknown> = { ...defaults };
  for (const [key, value] of Object.entries(stored)) {
    result[key] = deepMerge(result[key], value);
  }
  return result;
}

// 函数说明：判断值是否为普通对象，避免把数组当成对象递归合并。
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// 函数说明：保存 App settings；Electron 模式下由 main process 处理 safeStorage。
export async function saveSettings(
  settings: AppSettings,
  tokenUpdate: TokenUpdate,
): Promise<SettingsLoadResult> {
  if (window.symphonySettings) {
    return window.symphonySettings.save(settings, tokenUpdate);
  }

  const normalized = await validateSettings(settings);
  window.localStorage.setItem("github-symphony-settings", JSON.stringify(normalized));
  return {
    settings: normalized,
    token: { configured: tokenUpdate.mode === "set", encryptionAvailable: false },
    settingsPath: "localStorage:github-symphony-settings",
  };
}

// 函数说明：热应用 App settings；Electron 模式会由 main process 注入安全存储中的 token。
export async function applySettings(settings: AppSettings): Promise<{ status: string; generation: number }> {
  if (window.symphonySettings) {
    return window.symphonySettings.apply(settings);
  }

  return requestJson<{ status: string; generation: number }>("/api/v1/settings/apply", {
    method: "POST",
    body: JSON.stringify({ settings }),
  });
}

// 函数说明：校验设置并返回后端归一化版本。
export async function validateSettings(settings: AppSettings): Promise<AppSettings> {
  const result = await requestJson<{ ok: boolean; errors: string[]; normalized?: AppSettings }>(
    "/api/v1/settings/validate",
    {
      method: "POST",
      body: JSON.stringify({ settings }),
    },
  );
  if (!result.ok) {
    throw new Error(result.errors.join("\n"));
  }
  return result.normalized || settings;
}

// 函数说明：打开系统文件选择器导入 WORKFLOW.md。
export async function importWorkflow(): Promise<
  | { canceled: true }
  | {
      canceled: false;
      sourcePath: string;
      settings: AppSettings;
      token_hint: string | null;
      warnings: string[];
    }
> {
  if (!window.symphonySettings) {
    throw new Error("导入 WORKFLOW.md 需要在 Electron App 中使用");
  }
  return window.symphonySettings.importWorkflow();
}

// 函数说明：打开系统保存对话框导出 WORKFLOW.md。
export async function exportWorkflow(
  settings: AppSettings,
): Promise<{ canceled: true } | { canceled: false; filePath: string }> {
  if (!window.symphonySettings) {
    throw new Error("导出 WORKFLOW.md 需要在 Electron App 中使用");
  }
  return window.symphonySettings.exportWorkflow(settings);
}

// 函数说明：读取 token 保存状态；不返回真实 token。
export async function readTokenStatus(): Promise<TokenStatus> {
  if (window.symphonySettings) {
    return window.symphonySettings.tokenStatus();
  }
  return { configured: false, encryptionAvailable: false };
}

// 函数说明：用临时 PAT 或已保存 token 测试 GitHub 连接并读取 owner 列表。
export async function discoverConnect(
  request: GitHubDiscoveryRequest,
): Promise<GitHubDiscoveryConnectResult> {
  if (window.symphonySettings) {
    return window.symphonySettings.discoverConnect(request);
  }
  return requestJson<GitHubDiscoveryConnectResult>("/api/v1/settings/discovery/connect", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

// 函数说明：读取指定 owner 下的 GitHub Projects v2 列表。
export async function discoverProjects(
  request: GitHubDiscoveryRequest & { owner_type: "org" | "user"; owner: string },
): Promise<{ projects: GitHubProjectOption[]; warnings: string[] }> {
  if (window.symphonySettings) {
    return window.symphonySettings.discoverProjects(request);
  }
  return requestJson<{ projects: GitHubProjectOption[]; warnings: string[] }>(
    "/api/v1/settings/discovery/projects",
    {
      method: "POST",
      body: JSON.stringify(request),
    },
  );
}

// 函数说明：读取 Project 字段、状态选项和 Project 中出现过的仓库。
export async function discoverProject(
  request: GitHubDiscoveryRequest & {
    owner_type: "org" | "user";
    owner: string;
    project_number: number;
  },
): Promise<GitHubProjectDiscoveryResult> {
  if (window.symphonySettings) {
    return window.symphonySettings.discoverProject(request);
  }
  return requestJson<GitHubProjectDiscoveryResult>("/api/v1/settings/discovery/project", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

// 函数说明：封装 settings API 的 JSON 请求。
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
