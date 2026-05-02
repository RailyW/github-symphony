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

const DEFAULT_STATUS_OPTIONS = [
  "Todo",
  "In Progress",
  "Rework",
  "Human Review",
  "Merging",
  "Done",
  "Closed",
  "Cancelled",
];
const DEFAULT_ACTIVE_STATES = ["Todo", "In Progress", "Rework", "Merging"];
const DEFAULT_HANDOFF_STATES = ["Human Review"];
const DEFAULT_TERMINAL_STATES = ["Done", "Closed", "Cancelled"];
const LEGACY_PLACEHOLDER_REPOSITORY = "your-org/your-repo";
const DEFAULT_PROMPT_TEMPLATE = [
  "你正在处理 GitHub 任务：",
  "",
  "- 标识：`{{ issue.identifier }}`",
  "- 标题：`{{ issue.title }}`",
  "- 仓库：`{{ issue.repository }}`",
  "- 链接：`{{ issue.url }}`",
  "",
  "{{ workflow.status_policy_markdown }}",
  "",
  "## 默认自治边界：PR 前全自动",
  "",
  "你在隔离工作区内执行完整实现循环。调度器只负责派发任务、准备工作区、注入 GitHub 工具和记录事件；代码流转动作由你根据本 prompt、token 权限、GitHub tools 模式和 Project Status 执行。",
  "",
  "### 通用规则",
  "",
  "1. 先读取 issue/PR 描述、现有评论、关联 PR 和仓库代码，再开始修改。",
  "2. 使用单个 issue comment 作为 `## Codex Workpad`。如果已存在 Workpad，就更新它；不要新建多个进度评论。",
  "3. Workpad 至少记录：当前计划、实现摘要、验证命令与结果、PR 链接、未处理风险或阻塞。",
  "4. 除非遇到缺失权限、缺失 secret、仓库无法访问等真实外部阻塞，否则不要在 active 状态下结束 turn。",
  "5. 失败或需要返工时，把 Project Status 移到 `{{ workflow.failure_state }}`，并在 Workpad 写清楚原因和下一步。",
  "",
  "### 状态流转",
  "",
  "- `Todo`：先使用 GitHub 工具把 Project Status 移到 `In Progress`，然后创建或更新 `## Codex Workpad`，再开始复现、计划和实现。",
  "- `In Progress` / `Rework`：完成复现、计划、实现和验证。创建或复用任务分支，保持分支基于最新默认分支；按逻辑提交 commit，push 到远端，并创建或更新一个 PR。",
  "- PR 前置门禁：验收项完成；必要验证已运行并记录；最新 pushed commit 的 checks 为 green；PR 已链接到当前 issue；PR feedback sweep 没有未处理的 actionable comments；Workpad 已记录验证结果、PR 链接和剩余风险。",
  "- `Human Review`：这是非 active 交接状态。不要继续改代码，不要自行 merge；等待人工审批或把状态移到 `Rework` / `Merging`。",
  "- `Merging`：这是 active land 状态。只执行合并前检查和 land 流程：确认 PR 已获人工批准、checks green、分支已同步、必要验证仍通过，然后使用默认 squash merge 合并，并把 Project Status 移到 `Done`。",
  "",
  "### PR feedback sweep",
  "",
  "在进入 `Human Review` 前必须检查并处理：",
  "",
  "- PR 顶层评论、review summary、inline comments、requested changes。",
  "- CI/checks/Actions 的最新状态和失败日志。",
  "- 新反馈处理后必须重新验证、commit、push，并再次确认 checks green。",
  "- 对非 actionable 或不同意的反馈，要在 PR 或 Workpad 中给出简短理由。",
  "",
  "### 禁止事项",
  "",
  "- 不要 force push。",
  "- 不要直接修改或 push 到 `main` / 默认分支。",
  "- 不要删除远端分支。",
  "- 不要使用 PR body closing keywords 自动关闭 issue，也不要自动关闭 issue；任务结束以 GitHub Project Status `Done` 为准。",
  "- 不要扩大 scope；发现有价值但超出本 issue 的工作时，在 Workpad 记录为 follow-up。",
].join("\n");

// 函数说明：创建浏览器调试模式可用的默认设置；Electron 运行时优先使用 preload IPC。
export function defaultSettings(): AppSettings {
  return {
    tracker: {
      owner_type: "org",
      owner: "your-org",
      project_number: 12,
      repositories: ["your-org/your-repo"],
      status_field: "Status",
      status_options: [...DEFAULT_STATUS_OPTIONS],
      active_states: [...DEFAULT_ACTIVE_STATES],
      handoff_states: [...DEFAULT_HANDOFF_STATES],
      terminal_states: [...DEFAULT_TERMINAL_STATES],
      priority_field: "Priority",
      api_base_url: "https://api.github.com",
      graphql_url: "https://api.github.com/graphql",
    },
    blocker_policy: {
      kind: "github_issue_dependencies",
      unavailable_behavior: "treat_unblocked",
      blocked_states: ["Todo"],
    },
    workspace: {
      root: "~/code/github-symphony-workspaces",
      cleanup_terminal_workspaces: false,
      checkout: {
        mode: "clone",
        protocol: "ssh",
        depth: 1,
        repositories: {},
      },
      hooks: {
        after_create: null,
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
      kind: "agent_managed",
      success_state: "Human Review",
      failure_state: "Rework",
      mark_done_after_successful_turn: false,
      close_issue: false,
    },
    logging: {
      level: "DEBUG",
      retention_days: 14,
      max_file_mb: 10,
    },
    prompt_template: DEFAULT_PROMPT_TEMPLATE,
  };
}

// 函数说明：读取 App settings；浏览器调试模式下使用 localStorage fallback。
export async function loadSettings(): Promise<SettingsLoadResult> {
  if (window.symphonySettings) {
    return window.symphonySettings.load();
  }

  const stored = window.localStorage.getItem("github-symphony-settings");
  const settings = stored ? mergeSettingsWithDefaults(JSON.parse(stored)) : defaultSettings();
  if (stored && JSON.stringify(settings) !== stored) {
    window.localStorage.setItem("github-symphony-settings", JSON.stringify(settings));
  }
  return {
    settings,
    token: { configured: false, encryptionAvailable: false },
    settingsPath: "localStorage:github-symphony-settings",
  };
}

// 函数说明：把旧版 localStorage settings 与当前默认结构合并，补齐新增字段。
function mergeSettingsWithDefaults(stored: unknown): AppSettings {
  const merged = deepMerge(defaultSettings(), stored) as AppSettings;
  if (isLegacyHookOnlyWorkspace(stored)) {
    merged.workspace.checkout.mode = "hook";
  }
  migrateLegacyPlaceholderHook(merged);
  return merged;
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

// 函数说明：识别缺少 checkout 但已有 after_create 的旧设置，避免自动 clone 与旧 hook 双重执行。
function isLegacyHookOnlyWorkspace(value: unknown): boolean {
  if (!isPlainObject(value)) {
    return false;
  }
  const workspace = value.workspace;
  if (!isPlainObject(workspace) || isPlainObject(workspace.checkout)) {
    return false;
  }
  const hooks = workspace.hooks;
  return isPlainObject(hooks) && typeof hooks.after_create === "string" && hooks.after_create.trim().length > 0;
}

// 函数说明：把旧模板中克隆 your-org/your-repo 的占位 hook 迁移为动态 clone checkout。
function migrateLegacyPlaceholderHook(settings: AppSettings): void {
  const checkout = settings.workspace.checkout;
  const hooks = settings.workspace.hooks;
  const afterCreate = hooks.after_create;
  if (
    checkout.mode !== "hook"
    || typeof afterCreate !== "string"
    || !afterCreate.includes(LEGACY_PLACEHOLDER_REPOSITORY)
  ) {
    return;
  }

  // 逻辑说明：占位 hook 会让所有 Project item 复用 your-org/your-repo；
  // 动态 checkout 会按当前 GitHub Project item.repository 选择真实仓库。
  checkout.mode = "clone";
  checkout.protocol = "ssh";
  checkout.depth = 1;
  hooks.after_create = null;
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
    throw new Error(`HTTP ${response.status}: ${redactSecretText(await response.text())}`);
  }
  return (await response.json()) as T;
}

// 函数说明：脱敏 fallback HTTP 错误文本，避免调试模式把 PAT 显示到 renderer 错误提示中。
function redactSecretText(text: string): string {
  return text
    .replace(/(\b(?:[A-Za-z_][A-Za-z0-9_]*_key|api_key)\b["']?\s*[:=]\s*["']?)[^"',\s}]+/gi, "$1***")
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g, "***")
    .replace(/gh[pousr]_[A-Za-z0-9_]{20,}/g, "***")
    .replace(/(Bearer\s+)[A-Za-z0-9._-]+/gi, "$1***")
    .replace(/(Authorization["']?\s*[:=]\s*["']?(?:Bearer\s+)?)[A-Za-z0-9._-]+/gi, "$1***");
}
