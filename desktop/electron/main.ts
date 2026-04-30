import { app, BrowserWindow, dialog, ipcMain, safeStorage } from "electron";
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

type TokenUpdate =
  | { mode: "unchanged" }
  | { mode: "set"; value: string }
  | { mode: "clear" };

type StoredSecrets = {
  githubToken?: string;
};

let encryptionAvailableHint = true;

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
    prompt_template: promptTemplate,
  };
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
  return readJsonFile<Record<string, unknown>>(filePath, defaultSettings());
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
  return safeStorage.decryptString(encrypted);
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
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(parsed.detail || text || `HTTP ${response.status}`);
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

// 函数说明：App 启动后自动把本地 App settings 应用到 Python 后端。
async function bootstrapSettings(): Promise<void> {
  try {
    const settings = await loadSettingsDocument();
    await applySettingsToBackend(settings);
  } catch (caught) {
    console.error(`[settings] ${caught instanceof Error ? caught.message : String(caught)}`);
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

// 函数说明：启动 Python 后端进程；如果用户提供外部 API 地址则不重复启动。
function startBackend(): void {
  if (externalApiBaseUrl) {
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
  };

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
    console.log(`[backend] ${chunk.toString().trimEnd()}`);
  });
  backendProcess.stderr.on("data", (chunk) => {
    console.error(`[backend] ${chunk.toString().trimEnd()}`);
  });
  backendProcess.on("exit", (code) => {
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
  registerSettingsIpc();
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
    backendProcess.kill();
  }
});
