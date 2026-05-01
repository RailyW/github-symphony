import { app, BrowserWindow, dialog, ipcMain, safeStorage, shell } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let backendProcess: ChildProcessWithoutNullStreams | null = null;
const externalApiBaseUrl = process.env.SYMPHONY_API_BASE_URL;
const SETTINGS_FILE_NAME = "settings.json";
const SECRETS_FILE_NAME = "secrets.json";
const ELECTRON_LOG_FILE_NAME = "electron-main.jsonl";

type TokenUpdate =
  | { mode: "unchanged" }
  | { mode: "set"; value: string }
  | { mode: "clear" };

type StoredSecrets = {
  githubToken?: string;
};

type DiscoveryRequest = {
  github_token?: string;
  use_saved_token?: boolean;
  api_base_url?: string;
  graphql_url?: string;
  owner_type?: string;
  owner?: string;
  project_number?: number;
};

let encryptionAvailableHint = true;

// 函数说明：返回 Electron 和 Python 后端共享的持久日志目录。
function logDirPath(): string {
  return path.join(app.getPath("userData"), "logs");
}

// 函数说明：把 Electron main 进程事件写入 JSONL 文件，便于诊断白屏和后端启动问题。
function writeElectronLog(
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR",
  eventType: string,
  message: string,
  payload: Record<string, unknown> = {},
): void {
  try {
    const directory = logDirPath();
    fs.mkdirSync(directory, { recursive: true });
    const entry = {
      timestamp: new Date().toISOString(),
      level,
      logger: "electron.main",
      event_type: eventType,
      message: redactSecretText(message),
      payload: redactSecretData(payload),
    };
    fs.appendFileSync(
      path.join(directory, ELECTRON_LOG_FILE_NAME),
      `${JSON.stringify(entry)}\n`,
      "utf-8",
    );
  } catch (caught) {
    console.error(`[electron-log] ${caught instanceof Error ? caught.message : String(caught)}`);
  }
}

// 函数说明：递归脱敏 Electron main 写入日志的 payload。
function redactSecretData(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => redactSecretData(item));
  }
  if (value && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (isSecretKey(key)) {
        result[key] = "***";
      } else {
        result[key] = redactSecretData(item);
      }
    }
    return result;
  }
  if (typeof value === "string") {
    return redactSecretText(value);
  }
  return value;
}

// 函数说明：判断字段名是否代表敏感值；精确处理 PAT，避免把 path 误判为 PAT。
function isSecretKey(key: string): boolean {
  const normalized = key.toLowerCase();
  return (
    ["token", "secret", "authorization", "password", "pat", "api_token", "github_token"].includes(
      normalized,
    )
    || normalized.endsWith("_token")
    || normalized.endsWith("_secret")
    || normalized.endsWith("_password")
    || normalized.endsWith("_pat")
  );
}

// 函数说明：脱敏文本中的 GitHub PAT 和 Authorization header。
function redactSecretText(text: string): string {
  return text
    .replace(/github_pat_[A-Za-z0-9_]{20,}/g, "***")
    .replace(/gh[pousr]_[A-Za-z0-9_]{20,}/g, "***")
    .replace(/(Bearer\s+)[A-Za-z0-9._-]+/gi, "$1***")
    .replace(/(Authorization["']?\s*[:=]\s*["']?(?:Bearer\s+)?)[A-Za-z0-9._-]+/gi, "$1***");
}

// 函数说明：判断当前是否是 Vite 开发模式。
function isDevelopment(): boolean {
  return !app.isPackaged;
}

// 函数说明：计算本地后端 API 地址，renderer 通过 preload 读取同一个值。
function apiBaseUrl(): string {
  const backendPort = process.env.SYMPHONY_BACKEND_PORT || "8765";
  return process.env.SYMPHONY_API_BASE_URL || `http://127.0.0.1:${backendPort}`;
}

// 函数说明：返回当前 Electron userData 下的非敏感设置文件路径。
function settingsPath(): string {
  return path.join(app.getPath("userData"), SETTINGS_FILE_NAME);
}

// 函数说明：返回当前 Electron userData 下的加密 secret 文件路径。
function secretsPath(): string {
  return path.join(app.getPath("userData"), SECRETS_FILE_NAME);
}

// 函数说明：读取 JSON 文件；文件不存在时返回调用方提供的默认值。
async function readJsonFile<T>(filePath: string, fallback: T): Promise<T> {
  try {
    const text = await fsp.readFile(filePath, "utf-8");
    return JSON.parse(text) as T;
  } catch (caught) {
    if ((caught as NodeJS.ErrnoException).code === "ENOENT") {
      return fallback;
    }
    throw caught;
  }
}

// 函数说明：以格式化 JSON 写入本地文件，并确保父目录存在。
async function writeJsonFile(filePath: string, value: unknown): Promise<void> {
  await fsp.mkdir(path.dirname(filePath), { recursive: true });
  await fsp.writeFile(filePath, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
}

// 函数说明：返回 App 首次启动使用的默认设置；字段与后端 AppSettings v1 对齐。
function defaultSettings(): Record<string, unknown> {
  const promptTemplate = readBundledPromptTemplate();
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
    prompt_template: promptTemplate,
  };
}

// 函数说明：为 GUI 启动的打包 App 补全常见命令行工具 PATH。
function buildAugmentedPath(existingPath: string | undefined): string {
  const entries = commonCommandPathEntries();
  if (existingPath) {
    entries.push(...existingPath.split(path.delimiter).filter(Boolean));
  }
  return dedupeExistingPathEntries(entries).join(path.delimiter);
}

// 函数说明：返回 macOS GUI 环境中经常缺失的 Node、Codex、Homebrew 路径。
function commonCommandPathEntries(): string[] {
  const home = app.getPath("home");
  const entries = [
    path.join(home, ".local", "bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
  ];
  const nvmNodeRoot = path.join(home, ".nvm", "versions", "node");

  // 逻辑说明：Codex CLI 由 npm/nvm 安装时，GUI App 不会自动继承 shell rc PATH。
  // 这里发现所有 nvm Node 版本，并把较新的版本排在前面，保证 `env node` 能找到 node。
  if (fs.existsSync(nvmNodeRoot)) {
    const nvmBins = fs
      .readdirSync(nvmNodeRoot, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => path.join(nvmNodeRoot, entry.name, "bin"))
      .sort()
      .reverse();
    entries.unshift(...nvmBins);
  }

  return entries;
}

// 函数说明：去重并过滤不存在的 PATH 目录，避免把无效路径传给后端子进程。
function dedupeExistingPathEntries(entries: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const entry of entries) {
    const normalized = path.resolve(entry);
    if (seen.has(normalized) || !fs.existsSync(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

// 函数说明：从随包 WORKFLOW.example.md 中读取 prompt body；失败时使用内置提示。
function readBundledPromptTemplate(): string {
  const projectRoot = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..", "..");
  const workflowPath = path.join(projectRoot, "WORKFLOW.example.md");

  try {
    const text = fs.readFileSync(workflowPath, "utf-8");
    const lines = text.split(/\r?\n/);
    const endIndex = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
    if (lines[0]?.trim() === "---" && endIndex > 0) {
      return lines.slice(endIndex + 1).join("\n").trim();
    }
  } catch {
    // 逻辑说明：打包资源缺失不应阻止 App 启动，下面返回内置最小 prompt。
  }

  return [
    "你正在处理 GitHub 任务：",
    "",
    "- 标识：`{{ issue.identifier }}`",
    "- 标题：`{{ issue.title }}`",
    "- 仓库：`{{ issue.repository }}`",
    "- 链接：`{{ issue.url }}`",
    "",
    "请先阅读 issue/PR 描述和仓库代码，再实施最小必要修改。",
  ].join("\n");
}

// 函数说明：读取本地 settings；首次启动时写入默认设置，形成 App 内配置来源。
async function loadSettingsDocument(): Promise<Record<string, unknown>> {
  const filePath = settingsPath();
  if (!fs.existsSync(filePath)) {
    const initial = defaultSettings();
    await writeJsonFile(filePath, initial);
    return initial;
  }
  const stored = await readJsonFile<Record<string, unknown>>(filePath, defaultSettings());
  return mergeSettingsWithDefaults(stored);
}

// 函数说明：把旧版本 settings 与当前默认结构深度合并，避免新增字段导致旧安装 UI 崩溃。
function mergeSettingsWithDefaults(stored: Record<string, unknown>): Record<string, unknown> {
  return deepMerge(defaultSettings(), stored);
}

// 函数说明：递归合并对象；数组和标量以用户已保存值为准。
function deepMerge(
  defaults: Record<string, unknown>,
  stored: Record<string, unknown>,
): Record<string, unknown> {
  const result: Record<string, unknown> = { ...defaults };
  for (const [key, value] of Object.entries(stored)) {
    const defaultValue = result[key];
    if (isPlainObject(defaultValue) && isPlainObject(value)) {
      result[key] = deepMerge(
        defaultValue as Record<string, unknown>,
        value as Record<string, unknown>,
      );
    } else {
      result[key] = value;
    }
  }
  return result;
}

// 函数说明：判断值是否为普通对象，供 settings 深度合并使用。
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// 函数说明：读取 secret 状态，不向 renderer 返回真实 token。
async function tokenStatus(): Promise<{ configured: boolean; encryptionAvailable: boolean }> {
  const secrets = await readJsonFile<StoredSecrets>(secretsPath(), {});
  // 逻辑说明：不要在启动路径调用 safeStorage.isEncryptionAvailable()。
  // macOS 上 safeStorage 会同步访问 Keychain；在未签名或权限状态复杂时可能阻塞 Electron
  // main thread，表现为安装后的 App 白屏或窗口无响应。实际加密能力在保存 token 时再验证。
  return {
    configured: Boolean(secrets.githubToken),
    encryptionAvailable: encryptionAvailableHint,
  };
}

// 函数说明：读取并解密 GitHub token；不存在时返回空字符串。
async function readGithubToken(): Promise<string> {
  const secrets = await readJsonFile<StoredSecrets>(secretsPath(), {});
  if (!secrets.githubToken) {
    return "";
  }
  const encrypted = Buffer.from(secrets.githubToken, "base64");
  try {
    return safeStorage.decryptString(encrypted);
  } catch (caught) {
    writeElectronLog("ERROR", "electron.secret_decrypt_failed", "GitHub token 解密失败", {
      error: caught instanceof Error ? caught.message : String(caught),
    });
    throw caught;
  }
}

// 函数说明：按 tokenUpdate 语义更新加密 token，避免普通保存误清 secret。
async function updateGithubToken(update: TokenUpdate): Promise<void> {
  if (update.mode === "unchanged") {
    return;
  }

  const secrets = await readJsonFile<StoredSecrets>(secretsPath(), {});
  if (update.mode === "clear") {
    delete secrets.githubToken;
    await writeJsonFile(secretsPath(), secrets);
    return;
  }

  if (!safeStorage.isEncryptionAvailable()) {
    encryptionAvailableHint = false;
    throw new Error("当前系统不可用 Electron safeStorage，无法安全保存 GitHub token");
  }

  // 逻辑说明：safeStorage 输出二进制密文，落盘时统一转成 base64 文本。
  const encrypted = safeStorage.encryptString(update.value);
  encryptionAvailableHint = true;
  secrets.githubToken = encrypted.toString("base64");
  await writeJsonFile(secretsPath(), secrets);
}

// 函数说明：调用后端 JSON API，并把错误响应转换为清晰异常。
async function backendJson<T>(route: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl()}${route}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  const text = await response.text();
  let parsed: Record<string, unknown> = {};
  try {
    parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  } catch {
    parsed = {};
  }
  if (!response.ok) {
    writeElectronLog("ERROR", "electron.backend_json_failed", "后端 API 请求失败", {
      route,
      status: response.status,
      body: text.slice(0, 1000),
    });
    throw new Error(String(parsed.detail || text || `HTTP ${response.status}`));
  }
  return parsed as T;
}

// 函数说明：等待后端 API 可用，避免 App 刚启动时应用设置打到空端口。
async function waitForBackend(timeoutMs = 12000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      await backendJson("/api/v1/state");
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  throw new Error("后端服务启动超时");
}

// 函数说明：调用后端校验 settings，成功时返回后端归一化后的 settings。
async function validateSettings(settings: Record<string, unknown>): Promise<Record<string, unknown>> {
  const result = await backendJson<{
    ok: boolean;
    errors: string[];
    normalized?: Record<string, unknown>;
  }>("/api/v1/settings/validate", {
    method: "POST",
    body: JSON.stringify({ settings }),
  });
  if (!result.ok) {
    throw new Error(result.errors.join("\n"));
  }
  return result.normalized || settings;
}

// 函数说明：把本地 settings 和安全存储中的 token 热应用到后端。
async function applySettingsToBackend(settings: Record<string, unknown>): Promise<unknown> {
  await waitForBackend();
  const githubToken = await readGithubToken();
  return backendJson("/api/v1/settings/apply", {
    method: "POST",
    body: JSON.stringify({
      settings,
      github_token: githubToken || undefined,
    }),
  });
}

// 函数说明：为 discovery 请求解析 token；临时 PAT 优先，显式请求时才读取已保存 token。
async function resolveDiscoveryToken(request: DiscoveryRequest): Promise<string> {
  const token = String(request.github_token || "").trim();
  if (token) {
    return token;
  }
  if (request.use_saved_token) {
    const savedToken = await readGithubToken();
    if (savedToken) {
      return savedToken;
    }
  }
  throw new Error("请先输入 GitHub PAT，或选择使用已保存 token。");
}

// 函数说明：构造 discovery API payload；token 只进入本次本地请求，不会写入 settings。
async function discoveryPayload(request: DiscoveryRequest): Promise<Record<string, unknown>> {
  return {
    ...request,
    github_token: await resolveDiscoveryToken(request),
  };
}

// 函数说明：App 启动后自动把本地 App settings 应用到 Python 后端。
async function bootstrapSettings(): Promise<void> {
  try {
    const settings = await loadSettingsDocument();
    await applySettingsToBackend(settings);
    writeElectronLog("INFO", "electron.settings_bootstrap_applied", "启动设置已热应用到后端");
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : String(caught);
    writeElectronLog("ERROR", "electron.settings_bootstrap_failed", "启动设置热应用失败", {
      error: message,
    });
    console.error(`[settings] ${message}`);
  }
}

// 函数说明：注册 renderer 可调用的 settings IPC。
function registerSettingsIpc(): void {
  ipcMain.handle("settings:load", async () => ({
    settings: await loadSettingsDocument(),
    token: await tokenStatus(),
    settingsPath: settingsPath(),
  }));

  ipcMain.handle(
    "settings:save",
    async (_event, settings: Record<string, unknown>, tokenUpdate: TokenUpdate) => {
      const normalized = await validateSettings(settings);
      await writeJsonFile(settingsPath(), normalized);
      await updateGithubToken(tokenUpdate || { mode: "unchanged" });
      return {
        settings: normalized,
        token: await tokenStatus(),
        settingsPath: settingsPath(),
      };
    },
  );

  ipcMain.handle("settings:apply", async (_event, settings: Record<string, unknown>) => (
    applySettingsToBackend(settings)
  ));

  ipcMain.handle("settings:token-status", async () => tokenStatus());

  ipcMain.handle("settings:discover-connect", async (_event, request: DiscoveryRequest) => (
    backendJson("/api/v1/settings/discovery/connect", {
      method: "POST",
      body: JSON.stringify(await discoveryPayload(request || {})),
    })
  ));

  ipcMain.handle("settings:discover-projects", async (_event, request: DiscoveryRequest) => (
    backendJson("/api/v1/settings/discovery/projects", {
      method: "POST",
      body: JSON.stringify(await discoveryPayload(request || {})),
    })
  ));

  ipcMain.handle("settings:discover-project", async (_event, request: DiscoveryRequest) => (
    backendJson("/api/v1/settings/discovery/project", {
      method: "POST",
      body: JSON.stringify(await discoveryPayload(request || {})),
    })
  ));

  ipcMain.handle("settings:import-workflow", async () => {
    const result = await dialog.showOpenDialog({
      title: "导入 WORKFLOW.md",
      properties: ["openFile"],
      filters: [{ name: "Markdown", extensions: ["md", "markdown"] }],
    });
    if (result.canceled || !result.filePaths[0]) {
      return { canceled: true };
    }
    const text = await fsp.readFile(result.filePaths[0], "utf-8");
    const imported = await backendJson<Record<string, unknown>>("/api/v1/settings/import-workflow", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    return { canceled: false, sourcePath: result.filePaths[0], ...imported };
  });

  ipcMain.handle("settings:export-workflow", async (_event, settings: Record<string, unknown>) => {
    const exported = await backendJson<{ text: string }>("/api/v1/settings/export-workflow", {
      method: "POST",
      body: JSON.stringify({ settings }),
    });
    const result = await dialog.showSaveDialog({
      title: "导出 WORKFLOW.md",
      defaultPath: "WORKFLOW.md",
      filters: [{ name: "Markdown", extensions: ["md", "markdown"] }],
    });
    if (result.canceled || !result.filePath) {
      return { canceled: true };
    }
    await fsp.writeFile(result.filePath, exported.text, "utf-8");
    return { canceled: false, filePath: result.filePath };
  });
}

// 函数说明：注册日志页需要的 IPC；renderer 仍只访问受限能力，不直接操作任意文件。
function registerLogsIpc(): void {
  ipcMain.handle("logs:config", async () => backendJson("/api/v1/logs/config"));

  ipcMain.handle("logs:query", async (_event, filters: Record<string, unknown> = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value == null || value === "") {
        continue;
      }
      params.set(key, String(value));
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return backendJson(`/api/v1/logs/query${suffix}`);
  });

  ipcMain.handle("logs:export", async () => backendJson("/api/v1/logs/export", { method: "POST" }));

  ipcMain.handle("logs:open-directory", async () => {
    await fsp.mkdir(logDirPath(), { recursive: true });
    const error = await shell.openPath(logDirPath());
    if (error) {
      writeElectronLog("ERROR", "electron.logs_open_failed", "打开日志目录失败", { error });
      return { ok: false, error };
    }
    writeElectronLog("INFO", "electron.logs_opened", "用户打开了日志目录", {
      log_dir: logDirPath(),
    });
    return { ok: true };
  });
}

// 函数说明：启动 Python 后端进程；如果用户提供外部 API 地址则不重复启动。
function startBackend(): void {
  if (externalApiBaseUrl) {
    writeElectronLog("INFO", "electron.backend_external", "使用外部后端 API，不启动 sidecar", {
      api_base_url: externalApiBaseUrl,
    });
    return;
  }

  const projectRoot = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..", "..");
  const backendSource = app.isPackaged
    ? path.join(process.resourcesPath, "backend", "src")
    : path.join(projectRoot, "backend", "src");
  const workflowPath =
    process.env.SYMPHONY_WORKFLOW || path.join(projectRoot, "WORKFLOW.example.md");
  const packagedBackend = path.join(
    process.resourcesPath,
    "backend",
    "symphony-github-backend",
    "symphony-github-backend",
  );
  const backendCommand = process.env.SYMPHONY_PYTHON || (app.isPackaged ? packagedBackend : "python3");
  const backendArgs = app.isPackaged
    ? ["run", workflowPath, "--host", "127.0.0.1", "--port", process.env.SYMPHONY_BACKEND_PORT || "8765"]
    : [
        "-m",
        "symphony_github",
        "run",
        workflowPath,
        "--host",
        "127.0.0.1",
        "--port",
        process.env.SYMPHONY_BACKEND_PORT || "8765",
      ];

  const env = {
    ...process.env,
    PYTHONPATH: [backendSource, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
    PATH: buildAugmentedPath(process.env.PATH),
    SYMPHONY_LOG_DIR: logDirPath(),
    SYMPHONY_LOG_LEVEL: "DEBUG",
  };

  writeElectronLog("INFO", "electron.backend_starting", "准备启动 Python 后端", {
    command: backendCommand,
    args: backendArgs,
    cwd: projectRoot,
    path: env.PATH,
    log_dir: env.SYMPHONY_LOG_DIR,
  });

  backendProcess = spawn(
    backendCommand,
    backendArgs,
    {
      cwd: projectRoot,
      env,
      stdio: "pipe",
    },
  );

  // 逻辑说明：后端日志保留在 Electron 控制台，便于开发模式排查启动失败。
  backendProcess.stdout.on("data", (chunk) => {
    const line = chunk.toString().trimEnd();
    writeElectronLog("DEBUG", "electron.backend_stdout", "后端 stdout", { line });
    console.log(`[backend] ${line}`);
  });
  backendProcess.stderr.on("data", (chunk) => {
    const line = chunk.toString().trimEnd();
    writeElectronLog("WARNING", "electron.backend_stderr", "后端 stderr", { line });
    console.error(`[backend] ${line}`);
  });
  backendProcess.on("error", (caught) => {
    writeElectronLog("ERROR", "electron.backend_spawn_error", "后端进程启动失败", {
      error: caught.message,
    });
  });
  backendProcess.on("exit", (code) => {
    writeElectronLog("INFO", "electron.backend_exited", "后端进程已退出", {
      code: code ?? "unknown",
    });
    console.log(`[backend] exited with code ${code ?? "unknown"}`);
    backendProcess = null;
  });
}

// 函数说明：创建主窗口并加载 Vite 或打包后的静态页面。
function createWindow(): void {
  const preloadPath = path.join(__dirname, "preload.cjs");
  const window = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    title: "GitHub Symphony",
    backgroundColor: "#f6f7f9",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDevelopment()) {
    void window.loadURL(process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173");
  } else {
    void window.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

// 函数说明：应用启动时先启动后端，再创建窗口。
app.whenReady().then(() => {
  process.env.SYMPHONY_API_BASE_URL = externalApiBaseUrl || apiBaseUrl();
  writeElectronLog("INFO", "electron.app_ready", "Electron App 已启动", {
    packaged: app.isPackaged,
    api_base_url: process.env.SYMPHONY_API_BASE_URL,
    user_data: app.getPath("userData"),
  });
  registerSettingsIpc();
  registerLogsIpc();
  startBackend();
  void bootstrapSettings();
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

// 函数说明：所有窗口关闭时退出应用。
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

// 函数说明：应用退出前停止后端子进程。
app.on("before-quit", () => {
  if (backendProcess && !backendProcess.killed) {
    writeElectronLog("INFO", "electron.backend_kill", "App 退出前停止后端进程");
    backendProcess.kill();
  }
});
