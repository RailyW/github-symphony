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
const DEFAULT_HIGH_TRUST_APPROVAL_POLICY = { preset: "high-trust" };
const LEGACY_GRANULAR_APPROVAL_POLICY = {
  granular: {
    sandbox_approval: true,
    rules: true,
    mcp_elicitations: true,
  },
};
const DEFAULT_PROMPT_TEMPLATE = [
  "你正在处理 GitHub 任务：",
  "",
  "- 标识：`{{ issue.identifier }}`",
  "- 标题：`{{ issue.title }}`",
  "- 仓库：`{{ issue.repository }}`",
  "- 链接：`{{ issue.url }}`",
  "- Project item ID：`{{ issue.project_item_id }}`",
  "",
  "{{ workflow.status_policy_markdown }}",
  "",
  "## 默认自治边界：PR 前全自动",
  "",
  "你在 non-interactive runner（无人值守）的隔离工作区内执行完整实现循环。runner 无法接收 `ok` / `行` / `continue` / “确认后继续”等人工输入；不要要求人工确认后才继续。",
  "",
  "调度器只负责派发任务、准备工作区、注入 GitHub 工具和记录事件；代码流转动作由你根据本 prompt、token 权限、GitHub tools 模式和 Project Status 执行。在 GitHub Project item 仍处于 active 状态且工作仍限定在当前 issue scope 内时，创建/复用任务分支、修改代码、运行验证，以及 commit、push task branch、create/update PR 已授权；不得等待人工确认后再 commit、push 或创建/更新 PR。",
  "",
  "### 通用规则",
  "",
  "1. 先读取 issue/PR 描述、现有评论、关联 PR 和仓库代码，再开始修改。",
  "2. 使用单个 issue comment 作为 `## Codex Workpad`。如果已存在 Workpad，就更新它；不要新建多个进度评论。",
  "3. Workpad 至少记录：当前计划、实现摘要、验证命令与结果、PR 链接、未处理风险或阻塞。",
  "4. Project Status 流转必须优先使用专用动态工具 `github_update_project_status`，参数为当前 Project item ID 和目标状态名。",
  "5. 真实阻塞仅限外部条件：缺权限、缺 secret、仓库不可访问、CI/checks 无法判断、GitHub/API/网络故障等。遇到真实阻塞时，在 Workpad 写清原因、缺口和下一步；不要用“等待 ok/行/continue”作为阻塞理由。",
  "6. 非 Merging 阶段完成 PR 前置门禁后，必须更新 Workpad，并调用 `github_update_project_status` 把 Project Status 移到 `{{ workflow.success_state }}`。",
  "7. 失败或需要返工时，调用 `github_update_project_status` 把 Project Status 移到 `{{ workflow.failure_state }}`，并在 Workpad 写清楚原因和下一步。",
  "",
  "### 状态流转",
  "",
  "- `Todo`：先使用 GitHub 工具把 Project Status 移到 `In Progress`，然后创建或更新 `## Codex Workpad`，再开始复现、计划和实现。",
  "- `In Progress` / `Rework`：完成复现、计划、实现和验证。创建或复用任务分支，保持分支基于最新默认分支；按逻辑提交 commit，push 到远端，并创建或更新一个 PR。",
  "- PR 前置门禁：验收项完成；必要验证已运行并记录；最新 pushed commit 的 checks 为 green；如果仓库或 PR 没有 reported checks，则在 Workpad 记录 `no checks reported` 且不把它视为阻塞；PR 已链接到当前 issue；PR feedback sweep 没有未处理的 actionable comments；Workpad 已记录验证结果、PR 链接和剩余风险。",
  "- `Human Review`：这是非 active 交接状态。不要继续改代码，不要自行 merge；等待人工审批或把状态移到 `Rework` / `Merging`。",
  "- `Merging`：这是唯一允许自动 merge 的 active land 状态。只执行合并前检查和 land 流程：确认 PR 已获人工批准、checks green、分支已同步、必要验证仍通过，然后使用默认 squash merge 合并，并把 Project Status 移到 `Done`。",
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
  "- 除非当前 Project Status 是 `Merging`，不要自动 merge。",
  "- 不要使用 PR body closing keywords 自动关闭 issue，也不要自动关闭 issue；任务结束以 GitHub Project Status `Done` 为准。",
  "- 不要扩大 scope；发现有价值但超出本 issue 的工作时，在 Workpad 记录为 follow-up。",
].join("\n");
const LEGACY_BRIEF_PROMPT_TEMPLATE = [
  "你正在处理 GitHub 任务：",
  "",
  "- 标识：`{{ issue.identifier }}`",
  "- 标题：`{{ issue.title }}`",
  "- 仓库：`{{ issue.repository }}`",
  "- 链接：`{{ issue.url }}`",
  "",
  "请先阅读 issue/PR 描述和仓库代码，再实施最小必要修改。完成后请在 GitHub 中留下清晰的工作说明、验证结果和剩余风险。",
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
      approval_policy: { ...DEFAULT_HIGH_TRUST_APPROVAL_POLICY },
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
  if (shouldPreserveLegacyApprovalPolicy(stored)) {
    merged.codex.approval_policy = legacyGranularApprovalPolicy();
  }
  if (isLegacyHookOnlyWorkspace(stored)) {
    merged.workspace.checkout.mode = "hook";
  }
  migrateLegacyPlaceholderHook(merged);
  migrateLegacyPromptTemplate(merged);
  return merged;
}

// 函数说明：旧 settings 若没有显式 approval_policy，保留原本保守策略，避免静默扩大权限。
function shouldPreserveLegacyApprovalPolicy(value: unknown): boolean {
  if (!isPlainObject(value)) {
    return false;
  }
  const codex = value.codex;
  if (!isPlainObject(codex)) {
    return true;
  }
  return !Object.prototype.hasOwnProperty.call(codex, "approval_policy");
}

// 函数说明：每次迁移都返回新对象，避免多个 settings 实例共享同一个可变 JSON 值。
function legacyGranularApprovalPolicy(): Record<string, unknown> {
  return JSON.parse(JSON.stringify(LEGACY_GRANULAR_APPROVAL_POLICY)) as Record<string, unknown>;
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

// 函数说明：旧安装中保存的是短提示词时，升级为当前 PR 前自治默认 prompt。
function migrateLegacyPromptTemplate(settings: AppSettings): void {
  if (normalizePromptForComparison(settings.prompt_template) !== normalizePromptForComparison(LEGACY_BRIEF_PROMPT_TEMPLATE)) {
    return;
  }
  settings.prompt_template = DEFAULT_PROMPT_TEMPLATE;
}

// 函数说明：只为迁移比较规整换行和首尾空白，不改变用户真实保存的 prompt 内容。
function normalizePromptForComparison(value: string): string {
  return value.replace(/\r\n/g, "\n").trim();
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
