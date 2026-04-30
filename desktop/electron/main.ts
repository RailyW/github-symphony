import { app, BrowserWindow } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let backendProcess: ChildProcessWithoutNullStreams | null = null;
const externalApiBaseUrl = process.env.SYMPHONY_API_BASE_URL;

// 函数说明：判断当前是否是 Vite 开发模式。
function isDevelopment(): boolean {
  return !app.isPackaged;
}

// 函数说明：计算本地后端 API 地址，renderer 通过 preload 读取同一个值。
function apiBaseUrl(): string {
  return process.env.SYMPHONY_API_BASE_URL || "http://127.0.0.1:8765";
}

// 函数说明：启动 Python 后端进程；如果用户提供外部 API 地址则不重复启动。
function startBackend(): void {
  if (externalApiBaseUrl) {
    return;
  }

  const projectRoot = path.resolve(__dirname, "..", "..");
  const backendSource = path.join(projectRoot, "backend", "src");
  const workflowPath =
    process.env.SYMPHONY_WORKFLOW || path.join(projectRoot, "WORKFLOW.example.md");
  const pythonCommand = process.env.SYMPHONY_PYTHON || "python3";
  const backendPort = process.env.SYMPHONY_BACKEND_PORT || "8765";

  const env = {
    ...process.env,
    PYTHONPATH: [backendSource, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
  };

  backendProcess = spawn(
    pythonCommand,
    [
      "-m",
      "symphony_github",
      "run",
      workflowPath,
      "--host",
      "127.0.0.1",
      "--port",
      backendPort,
    ],
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
  const preloadPath = path.join(__dirname, "preload.js");
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
  startBackend();
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
