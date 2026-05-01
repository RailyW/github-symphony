import {
  AlertCircle,
  BookOpen,
  Check,
  CircleStop,
  ExternalLink,
  FileDown,
  FileUp,
  FolderOpen,
  HelpCircle,
  LayoutDashboard,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ScrollText,
  Server,
  Settings,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  exportLogBundle,
  fetchLogConfig,
  fetchState,
  openLogDirectory,
  queryLogs,
  refreshState,
  restartRun,
  stopRun,
} from "./api";
import {
  discoverConnect,
  discoverProject,
  discoverProjects,
  applySettings,
  exportWorkflow,
  importWorkflow,
  loadSettings,
  saveSettings,
  validateSettings,
} from "./settingsClient";
import type {
  AppSettings,
  EventRecord,
  GitHubDiscoveryConnectResult,
  GitHubOwnerOption,
  GitHubProjectDiscoveryResult,
  GitHubProjectFieldOption,
  GitHubProjectOption,
  LogConfig,
  LogEntry,
  LogQueryFilters,
  RunRecord,
  SettingsLoadResult,
  StateSnapshot,
  TokenStatus,
  TokenUpdate,
  WorkItem,
} from "./types";

type PageKey = "dashboard" | "settings" | "logs" | "help";
type SettingsTab = "github" | "workspace" | "agent" | "completion" | "codex" | "tools" | "logging" | "prompt";

// 函数说明：桌面仪表盘根组件，负责页面导航和共享状态加载。
export function App(): JSX.Element {
  const [page, setPage] = useState<PageKey>("dashboard");
  const [state, setState] = useState<StateSnapshot | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [tokenStatus, setTokenStatus] = useState<TokenStatus>({
    configured: false,
    encryptionAvailable: false,
  });
  const [settingsPath, setSettingsPath] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 函数说明：从后端刷新运行状态，并把错误展示给用户。
  const loadRuntimeState = useCallback(async () => {
    try {
      const next = await fetchState();
      setState(next);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  // 函数说明：从 Electron main 或浏览器 fallback 读取 App settings。
  const loadSettingsState = useCallback(async () => {
    const result = await loadSettings();
    applySettingsLoadResult(result, setSettings, setTokenStatus, setSettingsPath);
  }, []);

  // 逻辑说明：启动后同时读取运行态和设置态，并保持 Dashboard 轻量轮询。
  useEffect(() => {
    void loadRuntimeState();
    void loadSettingsState().catch((caught) => {
      setError(caught instanceof Error ? caught.message : String(caught));
    });
    const timer = window.setInterval(() => {
      void loadRuntimeState();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [loadRuntimeState, loadSettingsState]);

  // 函数说明：触发后端 refresh 后立即刷新 UI。
  const handleRefresh = useCallback(async () => {
    setBusy(true);
    try {
      await refreshState();
      await loadRuntimeState();
    } finally {
      setBusy(false);
    }
  }, [loadRuntimeState]);

  return (
    <main className="appFrame">
      <Sidebar page={page} onChange={setPage} />
      <section className="contentFrame">
        <header className="topbar">
          <div>
            <h1>{pageTitle(page)}</h1>
            <p>{pageSubtitle(page, state, settingsPath)}</p>
          </div>
          {page === "dashboard" ? (
            <button className="primaryButton" type="button" onClick={handleRefresh} disabled={busy}>
              <RefreshCw size={16} aria-hidden="true" />
              Refresh
            </button>
          ) : null}
        </header>

        {error ? <Banner message={error} /> : null}
        {state?.config_error ? <Banner message={state.config_error} /> : null}
        {state?.settings_error ? <Banner message={state.settings_error} /> : null}
        {statusMessage ? <SuccessBanner message={statusMessage} /> : null}

        {page === "dashboard" ? (
          <DashboardPage
            state={state}
            onStop={stopRunAndReload(loadRuntimeState)}
            onRestart={restartRunAndReload(loadRuntimeState)}
          />
        ) : null}
        {page === "settings" && settings ? (
          <SettingsPage
            settings={settings}
            tokenStatus={tokenStatus}
            onSettingsChange={setSettings}
            onTokenStatusChange={setTokenStatus}
            onMessage={setStatusMessage}
            onError={setError}
            onApplied={loadRuntimeState}
          />
        ) : null}
        {page === "settings" && !settings ? <EmptyState text="Loading settings" /> : null}
        {page === "logs" ? <LogsPage onMessage={setStatusMessage} onError={setError} /> : null}
        {page === "help" ? <HelpPage /> : null}
      </section>
    </main>
  );
}

// 函数说明：把 settings load 结果写入 React state。
function applySettingsLoadResult(
  result: SettingsLoadResult,
  setSettings: (settings: AppSettings) => void,
  setTokenStatus: (status: TokenStatus) => void,
  setSettingsPath: (path: string) => void,
): void {
  setSettings(result.settings);
  setTokenStatus(result.token);
  setSettingsPath(result.settingsPath);
}

// 函数说明：渲染左侧导航。
function Sidebar({ page, onChange }: { page: PageKey; onChange: (page: PageKey) => void }): JSX.Element {
  return (
    <nav className="sidebar" aria-label="Primary navigation">
      <div className="brandMark">
        <Server size={20} aria-hidden="true" />
        <span>GitHub Symphony</span>
      </div>
      <NavButton page={page} target="dashboard" icon={<LayoutDashboard size={18} />} onChange={onChange}>
        Dashboard
      </NavButton>
      <NavButton page={page} target="settings" icon={<Settings size={18} />} onChange={onChange}>
        Settings
      </NavButton>
      <NavButton page={page} target="logs" icon={<ScrollText size={18} />} onChange={onChange}>
        Logs
      </NavButton>
      <NavButton page={page} target="help" icon={<HelpCircle size={18} />} onChange={onChange}>
        Help
      </NavButton>
    </nav>
  );
}

// 函数说明：渲染单个导航按钮。
function NavButton({
  page,
  target,
  icon,
  children,
  onChange,
}: {
  page: PageKey;
  target: PageKey;
  icon: JSX.Element;
  children: ReactNode;
  onChange: (page: PageKey) => void;
}): JSX.Element {
  return (
    <button
      className={`navButton ${page === target ? "active" : ""}`}
      type="button"
      onClick={() => onChange(target)}
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}

// 函数说明：返回当前页面标题。
function pageTitle(page: PageKey): string {
  if (page === "settings") {
    return "Settings";
  }
  if (page === "logs") {
    return "Logs";
  }
  if (page === "help") {
    return "Help";
  }
  return "Dashboard";
}

// 函数说明：返回当前页面副标题。
function pageSubtitle(page: PageKey, state: StateSnapshot | null, settingsPath: string): string {
  if (page === "settings") {
    return settingsPath || "App settings";
  }
  if (page === "logs") {
    return "持久诊断日志、过滤和脱敏诊断包";
  }
  if (page === "help") {
    return "面向 Codex CLI / Claude Code 用户的 GitHub agent 调度指南";
  }
  const generation = state?.settings_generation ? ` · generation ${state.settings_generation}` : "";
  return `${state?.workflow_path || "App Settings"}${generation}`;
}

// 函数说明：渲染 Dashboard 页面。
function DashboardPage({
  state,
  onStop,
  onRestart,
}: {
  state: StateSnapshot | null;
  onStop: (issueId: string) => void;
  onRestart: (issueId: string) => void;
}): JSX.Element {
  const metrics = useMemo(() => summarizeState(state), [state]);
  const recentEvents = dashboardRecentEvents(state?.recent_events || []);

  return (
    <>
      <section className="metrics" aria-label="Service metrics">
        <Metric label="Running" value={metrics.running} />
        <Metric label="Candidates" value={metrics.candidates} />
        <Metric label="Blocked" value={metrics.blocked} />
        <Metric label="Last Poll" value={metrics.lastPoll} />
      </section>

      <section className="layout">
        <Panel title="Running Agents" icon={<Play size={17} aria-hidden="true" />}>
          <RunList runs={state?.running || []} onStop={onStop} onRestart={onRestart} />
        </Panel>

        <Panel title="Candidate Work" icon={<Server size={17} aria-hidden="true" />}>
          <CandidateList items={state?.candidates || []} />
        </Panel>
      </section>

      <Panel title="Recent Events" icon={<AlertCircle size={17} aria-hidden="true" />}>
        <EventList events={recentEvents} emptyText="No notable events" />
      </Panel>
    </>
  );
}

// 函数说明：渲染 Settings 页面和内部页签。
function SettingsPage({
  settings,
  tokenStatus,
  onSettingsChange,
  onTokenStatusChange,
  onMessage,
  onError,
  onApplied,
}: {
  settings: AppSettings;
  tokenStatus: TokenStatus;
  onSettingsChange: (settings: AppSettings) => void;
  onTokenStatusChange: (status: TokenStatus) => void;
  onMessage: (message: string | null) => void;
  onError: (message: string | null) => void;
  onApplied: () => Promise<void>;
}): JSX.Element {
  const [tab, setTab] = useState<SettingsTab>("github");
  const [tokenInput, setTokenInput] = useState("");
  const [tokenMode, setTokenMode] = useState<TokenUpdate["mode"]>("unchanged");
  const [busy, setBusy] = useState(false);

  // 函数说明：统一执行设置动作，保证按钮 busy 和错误提示一致。
  const runAction = useCallback(
    async (action: () => Promise<void>) => {
      setBusy(true);
      onError(null);
      onMessage(null);
      try {
        await action();
      } catch (caught) {
        onError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setBusy(false);
      }
    },
    [onError, onMessage],
  );

  // 函数说明：保存设置到本地 userData，并按用户选择更新 token。
  const handleSave = useCallback(async () => {
    await runAction(async () => {
      const result = await saveSettings(settings, tokenUpdateFromForm(tokenMode, tokenInput));
      onSettingsChange(result.settings);
      onTokenStatusChange(result.token);
      setTokenInput("");
      setTokenMode("unchanged");
      onMessage("设置已保存到本机。");
    });
  }, [onMessage, onSettingsChange, onTokenStatusChange, runAction, settings, tokenInput, tokenMode]);

  // 函数说明：热应用当前设置到后端调度器。
  const handleApply = useCallback(async () => {
    await runAction(async () => {
      await applySettings(settings);
      await onApplied();
      onMessage("设置已热应用；运行中的 agent 会继续使用原配置。");
    });
  }, [onApplied, onMessage, runAction, settings]);

  // 函数说明：先保存本地设置，再热应用到后端。
  const handleSaveAndApply = useCallback(async () => {
    await runAction(async () => {
      const result = await saveSettings(settings, tokenUpdateFromForm(tokenMode, tokenInput));
      onSettingsChange(result.settings);
      onTokenStatusChange(result.token);
      setTokenInput("");
      setTokenMode("unchanged");
      await applySettings(result.settings);
      await onApplied();
      onMessage("设置已保存并热应用。");
    });
  }, [onApplied, onMessage, onSettingsChange, onTokenStatusChange, runAction, settings, tokenInput, tokenMode]);

  // 函数说明：只调用后端校验，不落盘、不热应用。
  const handleValidate = useCallback(async () => {
    await runAction(async () => {
      const normalized = await validateSettings(settings);
      onSettingsChange(normalized);
      onMessage("设置校验通过。");
    });
  }, [onMessage, onSettingsChange, runAction, settings]);

  // 函数说明：从用户选择的 WORKFLOW.md 导入配置到表单。
  const handleImport = useCallback(async () => {
    await runAction(async () => {
      const result = await importWorkflow();
      if (result.canceled) {
        return;
      }
      onSettingsChange(result.settings);
      const warning = result.warnings.length ? ` ${result.warnings.join(" ")}` : "";
      onMessage(`已导入 ${result.sourcePath}。${warning}`);
    });
  }, [onMessage, onSettingsChange, runAction]);

  // 函数说明：把当前表单导出成 WORKFLOW.md。
  const handleExport = useCallback(async () => {
    await runAction(async () => {
      const result = await exportWorkflow(settings);
      if (result.canceled) {
        return;
      }
      onMessage(`已导出到 ${result.filePath}。`);
    });
  }, [onMessage, runAction, settings]);

  return (
    <section className="settingsShell">
      <div className="settingsActions">
        <button className="secondaryButton" type="button" onClick={handleImport} disabled={busy}>
          <FileUp size={15} aria-hidden="true" />
          Import WORKFLOW
        </button>
        <button className="secondaryButton" type="button" onClick={handleExport} disabled={busy}>
          <FileDown size={15} aria-hidden="true" />
          Export WORKFLOW
        </button>
        <button className="secondaryButton" type="button" onClick={handleValidate} disabled={busy}>
          <Check size={15} aria-hidden="true" />
          Validate
        </button>
        <button className="secondaryButton" type="button" onClick={handleSave} disabled={busy}>
          <Save size={15} aria-hidden="true" />
          Save
        </button>
        <button className="primaryButton" type="button" onClick={handleSaveAndApply} disabled={busy}>
          <RefreshCw size={15} aria-hidden="true" />
          Save & Apply
        </button>
        <button className="secondaryButton" type="button" onClick={handleApply} disabled={busy}>
          Apply Only
        </button>
      </div>

      <div className="settingsLayout">
        <div className="settingsTabs" role="tablist" aria-label="Settings sections">
          {settingsTabs().map((item) => (
            <button
              className={`tabButton ${tab === item.key ? "active" : ""}`}
              key={item.key}
              type="button"
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="settingsPanel">
          {tab === "github" ? (
            <GitHubSettings
              settings={settings}
              tokenStatus={tokenStatus}
              tokenInput={tokenInput}
              tokenMode={tokenMode}
              onTokenInput={setTokenInput}
              onTokenMode={setTokenMode}
              onMessage={onMessage}
              onError={onError}
              onChange={onSettingsChange}
            />
          ) : null}
          {tab === "workspace" ? <WorkspaceSettings settings={settings} onChange={onSettingsChange} /> : null}
          {tab === "agent" ? <AgentSettings settings={settings} onChange={onSettingsChange} /> : null}
          {tab === "completion" ? <CompletionSettings settings={settings} onChange={onSettingsChange} /> : null}
          {tab === "codex" ? <CodexSettings settings={settings} onChange={onSettingsChange} /> : null}
          {tab === "tools" ? <ToolSettings settings={settings} onChange={onSettingsChange} /> : null}
          {tab === "logging" ? <LoggingSettings settings={settings} onChange={onSettingsChange} /> : null}
          {tab === "prompt" ? <PromptSettings settings={settings} onChange={onSettingsChange} /> : null}
        </div>
      </div>
    </section>
  );
}

// 函数说明：返回 Settings 内部页签。
function settingsTabs(): Array<{ key: SettingsTab; label: string }> {
  return [
    { key: "github", label: "GitHub Project" },
    { key: "workspace", label: "Workspace" },
    { key: "agent", label: "Agent" },
    { key: "completion", label: "Completion" },
    { key: "codex", label: "Codex" },
    { key: "tools", label: "Tools" },
    { key: "logging", label: "Logging" },
    { key: "prompt", label: "Prompt" },
  ];
}

// 函数说明：渲染 GitHub Project 配置区。
function GitHubSettings({
  settings,
  tokenStatus,
  tokenInput,
  tokenMode,
  onTokenInput,
  onTokenMode,
  onMessage,
  onError,
  onChange,
}: {
  settings: AppSettings;
  tokenStatus: TokenStatus;
  tokenInput: string;
  tokenMode: TokenUpdate["mode"];
  onTokenInput: (value: string) => void;
  onTokenMode: (mode: TokenUpdate["mode"]) => void;
  onMessage: (message: string | null) => void;
  onError: (message: string | null) => void;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  const [owners, setOwners] = useState<GitHubOwnerOption[]>([]);
  const [projects, setProjects] = useState<GitHubProjectOption[]>([]);
  const [projectDiscovery, setProjectDiscovery] = useState<GitHubProjectDiscoveryResult | null>(null);
  const [selectedOwnerKey, setSelectedOwnerKey] = useState(
    `${settings.tracker.owner_type}:${settings.tracker.owner}`,
  );
  const [selectedProjectNumber, setSelectedProjectNumber] = useState(
    String(settings.tracker.project_number || ""),
  );
  const [discoverySource, setDiscoverySource] = useState<"input" | "saved">("input");
  const [busyDiscovery, setBusyDiscovery] = useState(false);

  // 函数说明：拼装 discovery 请求；临时 PAT 和已保存 token 二选一，不写入 settings 文件。
  const buildDiscoveryRequest = useCallback(
    (source: "input" | "saved") => ({
      github_token: source === "input" ? tokenInput.trim() : undefined,
      use_saved_token: source === "saved",
      api_base_url: settings.tracker.api_base_url,
      graphql_url: settings.tracker.graphql_url,
    }),
    [settings.tracker.api_base_url, settings.tracker.graphql_url, tokenInput],
  );

  // 函数说明：统一执行 discovery 动作，避免多处重复 busy/error 处理。
  const runDiscovery = useCallback(
    async (action: () => Promise<void>) => {
      setBusyDiscovery(true);
      onError(null);
      onMessage(null);
      try {
        await action();
      } catch (caught) {
        onError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setBusyDiscovery(false);
      }
    },
    [onError, onMessage],
  );

  // 函数说明：按 owner 读取 Project 列表，并在可能时自动选择当前配置或第一个 Project。
  const loadProjectsForOwner = useCallback(
    async (owner: GitHubOwnerOption, source: "input" | "saved") => {
      const result = await discoverProjects({
        ...buildDiscoveryRequest(source),
        owner_type: owner.owner_type,
        owner: owner.login,
      });
      setProjects(result.projects);
      setProjectDiscovery(null);

      const preferredProject = result.projects.find(
        (project) => project.number === settings.tracker.project_number,
      ) || result.projects[0];
      setSelectedProjectNumber(preferredProject ? String(preferredProject.number) : "");

      updateSettings(onChange, settings, (draft) => {
        draft.tracker.owner_type = owner.owner_type;
        draft.tracker.owner = owner.login;
        if (preferredProject) {
          draft.tracker.project_number = preferredProject.number;
        }
      });
    },
    [buildDiscoveryRequest, onChange, settings],
  );

  // 函数说明：连接 GitHub 并读取 owner 列表，随后自动加载当前或默认 owner 的 Projects。
  const handleConnect = useCallback(
    async (source: "input" | "saved") => {
      await runDiscovery(async () => {
        setDiscoverySource(source);
        const result: GitHubDiscoveryConnectResult = await discoverConnect(
          buildDiscoveryRequest(source),
        );
        setOwners(result.owners);

        const currentOwner = result.owners.find(
          (owner) => `${owner.owner_type}:${owner.login}` === selectedOwnerKey,
        ) || result.owners[0];
        if (!currentOwner) {
          throw new Error("当前 PAT 未返回可用 owner，请检查 token 权限。");
        }
        const nextOwnerKey = `${currentOwner.owner_type}:${currentOwner.login}`;
        setSelectedOwnerKey(nextOwnerKey);
        await loadProjectsForOwner(currentOwner, source);
        onMessage(`已连接 GitHub：${result.viewer.login}`);
      });
    },
    [
      buildDiscoveryRequest,
      loadProjectsForOwner,
      onMessage,
      runDiscovery,
      selectedOwnerKey,
    ],
  );

  // 函数说明：用户切换 owner 后刷新 Project 列表。
  const handleOwnerChange = useCallback(
    async (ownerKey: string) => {
      setSelectedOwnerKey(ownerKey);
      const owner = owners.find((item) => `${item.owner_type}:${item.login}` === ownerKey);
      if (!owner) {
        return;
      }
      await runDiscovery(async () => {
        await loadProjectsForOwner(owner, discoverySource);
      });
    },
    [discoverySource, loadProjectsForOwner, owners, runDiscovery],
  );

  // 函数说明：读取 Project 字段和仓库，并用推荐值填充 tracker 配置。
  const handleInspectProject = useCallback(async () => {
    await runDiscovery(async () => {
      const owner = owners.find((item) => `${item.owner_type}:${item.login}` === selectedOwnerKey);
      if (!owner) {
        throw new Error("请先选择 GitHub owner。");
      }
      const projectNumber = Number(selectedProjectNumber);
      if (!projectNumber) {
        throw new Error("请先选择 GitHub Project。");
      }
      const result = await discoverProject({
        ...buildDiscoveryRequest(discoverySource),
        owner_type: owner.owner_type,
        owner: owner.login,
        project_number: projectNumber,
      });
      const statusField = chooseStatusField(result.status_fields, settings.tracker.status_field);
      if (!statusField) {
        throw new Error("该 Project 没有 single-select Status 字段，请先在 GitHub Project 中创建。");
      }
      const priorityField = choosePriorityField(result.priority_fields, settings.tracker.priority_field);
      const statusOptions = statusField.options.map((option) => option.name);
      const activeStates = chooseStates(
        statusOptions,
        ["Todo", "In Progress", "Rework", "Merging"],
        "first",
      );
      const terminalStates = chooseStates(statusOptions, ["Done", "Closed", "Cancelled"], "last");
      const handoffStates = chooseHandoffStates(
        statusOptions,
        activeStates,
        terminalStates,
        settings.tracker.handoff_states,
      );
      const successState = chooseSuccessState(
        statusOptions,
        activeStates,
        handoffStates,
        terminalStates,
        settings.completion_policy.success_state,
      );
      const failureState = statusOptions.includes(settings.completion_policy.failure_state || "")
        ? settings.completion_policy.failure_state
        : statusOptions.find((state) => state === "Rework") || activeStates[0] || null;
      const blockedStates = chooseBlockedStates(
        statusOptions,
        activeStates,
        settings.blocker_policy.blocked_states,
      );

      setProjectDiscovery(result);
      updateSettings(onChange, settings, (draft) => {
        draft.tracker.owner_type = owner.owner_type;
        draft.tracker.owner = owner.login;
        draft.tracker.project_number = projectNumber;
        draft.tracker.status_field = statusField.name;
        draft.tracker.status_options = statusOptions;
        draft.tracker.priority_field = priorityField?.name || null;
        draft.tracker.active_states = activeStates;
        draft.tracker.handoff_states = handoffStates;
        draft.tracker.terminal_states = terminalStates;
        draft.blocker_policy.blocked_states = blockedStates;
        draft.completion_policy.success_state = successState;
        draft.completion_policy.failure_state = failureState;
        if (result.repositories.length) {
          draft.tracker.repositories = result.repositories;
        }
      });
      onMessage("已从 GitHub Project 读取字段、状态选项和仓库列表。");
    });
  }, [
    buildDiscoveryRequest,
    discoverySource,
    onChange,
    onMessage,
    owners,
    runDiscovery,
    selectedOwnerKey,
    selectedProjectNumber,
    settings,
  ]);

  const statusField = projectDiscovery
    ? chooseStatusField(projectDiscovery.status_fields, settings.tracker.status_field)
    : null;
  const statusOptions = statusField?.options.map((option) => option.name) || settings.tracker.status_options || [];
  const ungroupedStates = statusOptions.filter(
    (state) => !settings.tracker.active_states.includes(state)
      && !settings.tracker.handoff_states.includes(state)
      && !settings.tracker.terminal_states.includes(state),
  );

  return (
    <>
      <SectionIntro
        title="GitHub Project"
        text="先连接 GitHub，再选择 owner、Project、Status 字段和状态集合；大部分配置会从 GitHub 自动读取。"
      />
      <div className="tokenBox">
        <div>
          <strong>GitHub Token</strong>
          <p>
            当前状态：{tokenStatus.configured ? "已保存到系统安全存储" : "未保存"}。
            {tokenStatus.encryptionAvailable ? "保存 token 时会使用 safeStorage 加密。" : "safeStorage 不可用。"}
            默认 Autonomy preset 是 PR 前全自动；如果 token 具备仓库写权限，Codex 子进程会获得
            GITHUB_TOKEN/GH_TOKEN 并可按 prompt 执行 push、PR 和 Project Status 写入。
          </p>
        </div>
        <input
          type="password"
          value={tokenInput}
          placeholder="粘贴 PAT 后点击 Connect；只有 Save 或 Save & Apply 才会保存"
          onChange={(event) => {
            onTokenInput(event.target.value);
            onTokenMode(event.target.value ? "set" : "unchanged");
          }}
        />
        <div className="buttonRow">
          <button
            className="primaryButton"
            type="button"
            onClick={() => void handleConnect("input")}
            disabled={busyDiscovery || !tokenInput.trim()}
          >
            Connect PAT
          </button>
          <button
            className="secondaryButton"
            type="button"
            onClick={() => void handleConnect("saved")}
            disabled={busyDiscovery || !tokenStatus.configured}
          >
            Use Saved Token
          </button>
          <button className="secondaryButton dangerText" type="button" onClick={() => {
            onTokenInput("");
            onTokenMode("clear");
          }}>
            <Trash2 size={15} aria-hidden="true" />
            Clear Token
          </button>
        </div>
        {tokenMode === "clear" ? <p className="inlineWarning">下次保存会清除已保存 token。</p> : null}
      </div>
      <div className="discoveryGrid">
        <SelectField
          label="Owner"
          value={selectedOwnerKey}
          options={owners.map((owner) => `${owner.owner_type}:${owner.login}`)}
          onChange={(value) => void handleOwnerChange(value)}
        />
        <label className="field">
          <span>Project</span>
          <select
            value={selectedProjectNumber}
            onChange={(event) => {
              const value = event.target.value;
              setSelectedProjectNumber(value);
              updateSettings(onChange, settings, (draft) => {
                draft.tracker.project_number = Number(value);
              });
            }}
          >
            {projects.map((project) => (
              <option value={String(project.number)} key={project.id}>
                #{project.number} {project.title}{project.closed ? " (closed)" : ""}
              </option>
            ))}
          </select>
        </label>
        <button
          className="secondaryButton alignEnd"
          type="button"
          onClick={() => void handleInspectProject()}
          disabled={busyDiscovery || !selectedProjectNumber}
        >
          Load Project Details
        </button>
      </div>
      <div className="formGrid">
        <SelectField
          label="Status Field"
          value={settings.tracker.status_field}
          options={projectDiscovery?.status_fields.map((field) => field.name) || [settings.tracker.status_field]}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.tracker.status_field = value;
          })}
        />
        <SelectField
          label="Priority Field"
          value={settings.tracker.priority_field || "none"}
          options={["none", ...(projectDiscovery?.priority_fields.map((field) => field.name) || [])]}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.tracker.priority_field = value === "none" ? null : value;
          })}
        />
      </div>
      {statusOptions.length ? (
        <div className="statePickerGrid">
          <OptionChecklist
            label="Active States"
            options={statusOptions}
            selected={settings.tracker.active_states}
            onChange={(values) => updateSettings(onChange, settings, (draft) => {
              draft.tracker.active_states = values;
            })}
          />
          <OptionChecklist
            label="Handoff States"
            options={statusOptions}
            selected={settings.tracker.handoff_states}
            onChange={(values) => updateSettings(onChange, settings, (draft) => {
              draft.tracker.handoff_states = values;
            })}
          />
          <OptionChecklist
            label="Terminal States"
            options={statusOptions}
            selected={settings.tracker.terminal_states}
            onChange={(values) => updateSettings(onChange, settings, (draft) => {
              draft.tracker.terminal_states = values;
            })}
          />
          <StateRoleSummary ungroupedStates={ungroupedStates} />
        </div>
      ) : (
        <div className="inlineHint">连接 GitHub 并加载 Project 后，可以在这里勾选 active/handoff/terminal 状态。</div>
      )}
      <ListEditor
        label="Repositories"
        values={settings.tracker.repositories}
        placeholder="owner/repo"
        onChange={(values) => updateSettings(onChange, settings, (draft) => {
          draft.tracker.repositories = values;
        })}
      />
      <details className="advancedBox">
        <summary>Advanced API endpoints</summary>
        <div className="formGrid">
          <TextField
            label="GitHub REST API"
            value={settings.tracker.api_base_url}
            onChange={(value) => updateSettings(onChange, settings, (draft) => {
              draft.tracker.api_base_url = value;
            })}
          />
          <TextField
            label="GitHub GraphQL API"
            value={settings.tracker.graphql_url}
            onChange={(value) => updateSettings(onChange, settings, (draft) => {
              draft.tracker.graphql_url = value;
            })}
          />
        </div>
      </details>
    </>
  );
}

// 函数说明：渲染工作区设置区。
function WorkspaceSettings({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  return (
    <>
      <SectionIntro
        title="Workspace"
        text="每个派发任务会拥有独立工作区。after_create hook 会在新工作区内执行，常用于 clone 仓库。"
      />
      <div className="formGrid">
        <TextField
          label="Workspace Root"
          value={settings.workspace.root}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.workspace.root = value;
          })}
        />
        <ToggleField
          label="Cleanup Terminal Workspaces"
          checked={settings.workspace.cleanup_terminal_workspaces}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.workspace.cleanup_terminal_workspaces = value;
          })}
        />
      </div>
      <TextAreaField
        label="After Create Hook"
        value={settings.workspace.hooks.after_create || ""}
        minRows={6}
        onChange={(value) => updateSettings(onChange, settings, (draft) => {
          draft.workspace.hooks.after_create = value || null;
        })}
      />
    </>
  );
}

// 函数说明：渲染调度器和重试设置区。
function AgentSettings({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  return (
    <>
      <SectionIntro title="Agent" text="控制并发、单任务最大 turn 数、轮询间隔和异常重试退避。" />
      <div className="formGrid">
        <NumberField
          label="Max Concurrent Agents"
          value={settings.agent.max_concurrent_agents}
          min={1}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.agent.max_concurrent_agents = value;
          })}
        />
        <NumberField
          label="Max Turns"
          value={settings.agent.max_turns}
          min={1}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.agent.max_turns = value;
          })}
        />
        <NumberField
          label="Poll Interval Ms"
          value={settings.agent.poll_interval_ms}
          min={1000}
          step={1000}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.agent.poll_interval_ms = value;
          })}
        />
        <NumberField
          label="Max Retry Backoff Ms"
          value={settings.agent.max_retry_backoff_ms}
          min={1000}
          step={1000}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.agent.max_retry_backoff_ms = value;
          })}
        />
      </div>
    </>
  );
}

// 函数说明：渲染任务完成策略设置区，控制成功 turn 后如何更新 GitHub Project 状态。
function CompletionSettings({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  const knownStates = knownStatusStates(settings);

  return (
    <>
      <SectionIntro
        title="Completion"
        text="配置 Codex turn 正常完成后的目标/交接阶段。默认 Autonomy preset 为 PR 前全自动，成功后由 agent 把任务交接到 Human Review。"
      />
      <div className="autonomyPreset">
        <span>Autonomy preset</span>
        <strong>PR 前全自动</strong>
        <p>
          App 不在本地 runner 内置 commit、push 或 merge。agent_managed 模式要求 prompt 与
          GitHub tools 驱动 Workpad、分支、PR、feedback sweep、checks green 和状态流转。
        </p>
      </div>
      <div className="formGrid">
        <SelectField
          label="Policy Kind"
          value={settings.completion_policy.kind}
          options={["update_project_status", "agent_managed", "none"]}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.completion_policy.kind = value as AppSettings["completion_policy"]["kind"];
          })}
        />
        <SelectField
          label="Success Target State"
          value={settings.completion_policy.success_state}
          options={knownStates}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.completion_policy.success_state = value;
          })}
        />
        <SelectField
          label="Failure State"
          value={settings.completion_policy.failure_state || "none"}
          options={["none", ...knownStates]}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.completion_policy.failure_state = value === "none" ? null : value;
          })}
        />
        <ToggleField
          label="Move To Target After Successful Turn"
          checked={settings.completion_policy.mark_done_after_successful_turn}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.completion_policy.mark_done_after_successful_turn = value;
          })}
        />
        <ToggleField
          label="Close Issue"
          checked={settings.completion_policy.close_issue}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.completion_policy.close_issue = value;
          })}
        />
      </div>
      <div className="inlineHint">
        update_project_status 会由 App 自动把 Project Status 改到目标阶段；agent_managed 会交给 prompt
        和 GitHub 工具自行流转；none 不做状态写入。默认不会自动关闭 Issue，Merging 仍需要人工先把
        Project Status 移入该阶段。
      </div>
    </>
  );
}

// 函数说明：渲染 Codex app-server 设置区。
function CodexSettings({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  return (
    <>
      <SectionIntro
        title="Codex"
        text="配置每个任务启动 Codex app-server 的命令、模型、sandbox 和 approval policy。"
      />
      <div className="formGrid">
        <TextField
          label="Command"
          value={settings.codex.command}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.codex.command = value;
          })}
        />
        <TextField
          label="Model"
          value={settings.codex.model || ""}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.codex.model = value || null;
          })}
        />
        <TextField
          label="Thread Sandbox"
          value={settings.codex.thread_sandbox}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.codex.thread_sandbox = value;
          })}
        />
      </div>
      <JsonField
        label="Approval Policy JSON"
        value={settings.codex.approval_policy}
        onChange={(value) => updateSettings(onChange, settings, (draft) => {
          draft.codex.approval_policy = value;
        })}
      />
      {isNeverApprovalPolicy(settings.codex.approval_policy) ? (
        <div className="inlineWarningBox">
          approval_policy 为 never 或 high_trust preset 时，app-server 的 command、file-change、
          applyPatch、exec 和可识别工具 approval prompt 会自动批准。只应在隔离工作区、受限 token 和可信
          prompt 下使用。
        </div>
      ) : null}
      <JsonField
        label="Turn Sandbox Policy JSON"
        value={settings.codex.turn_sandbox_policy}
        onChange={(value) => updateSettings(onChange, settings, (draft) => {
          draft.codex.turn_sandbox_policy = value;
        })}
      />
    </>
  );
}

// 函数说明：渲染持久诊断日志设置区。
function LoggingSettings({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  return (
    <>
      <SectionIntro
        title="Logging"
        text="后端、Electron main、Codex stderr、GitHub API 摘要和调度事件会写入本机 JSONL 日志，便于复现问题后直接导出诊断包。"
      />
      <div className="formGrid">
        <SelectField
          label="Level"
          value={settings.logging.level}
          options={["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.logging.level = value as AppSettings["logging"]["level"];
          })}
        />
        <NumberField
          label="Retention Days"
          value={settings.logging.retention_days}
          min={1}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.logging.retention_days = value;
          })}
        />
        <NumberField
          label="Max File MB"
          value={settings.logging.max_file_mb}
          min={1}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.logging.max_file_mb = value;
          })}
        />
      </div>
      <div className="inlineHint">
        Electron 打包版日志默认位于 App 的 userData/logs；CLI 默认位于 ~/.github-symphony/logs。
        Logs 页面可直接打开目录或导出脱敏 zip 诊断包。
      </div>
    </>
  );
}

// 函数说明：渲染动态工具设置区。
function ToolSettings({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  const knownStates = knownStatusStates(settings);

  return (
    <>
      <SectionIntro
        title="GitHub Dynamic Tools"
        text="控制注入给 Codex agent 的 GitHub GraphQL / REST 工具，以及 issue dependencies 只在哪些阶段阻塞派发。"
      />
      <div className="formGrid">
        <ToggleField
          label="GitHub Tools Enabled"
          checked={settings.tools.github.enabled}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.tools.github.enabled = value;
          })}
        />
        <SelectField
          label="Mode"
          value={settings.tools.github.mode}
          options={["read_only", "read_write"]}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.tools.github.mode = value as "read_only" | "read_write";
          })}
        />
        <SelectField
          label="Blocker Unavailable Behavior"
          value={settings.blocker_policy.unavailable_behavior}
          options={["treat_unblocked", "treat_blocked"]}
          onChange={(value) => updateSettings(onChange, settings, (draft) => {
            draft.blocker_policy.unavailable_behavior = value;
          })}
        />
      </div>
      <OptionChecklist
        label="Blocked States"
        options={knownStates}
        selected={settings.blocker_policy.blocked_states}
        onChange={(values) => updateSettings(onChange, settings, (draft) => {
          draft.blocker_policy.blocked_states = values;
        })}
      />
      {settings.tools.github.mode === "read_write" ? (
        <div className="inlineWarningBox">
          read_write 会允许 GitHub GraphQL mutation、仓库 allowlist 内的 REST 写操作，以及
          github_update_project_status。配合具备写权限的 token 时，agent 可以更新评论、PR 和 Project Status。
        </div>
      ) : null}
    </>
  );
}

// 函数说明：渲染 Prompt 模板设置区。
function PromptSettings({
  settings,
  onChange,
}: {
  settings: AppSettings;
  onChange: (settings: AppSettings) => void;
}): JSX.Element {
  return (
    <>
      <SectionIntro
        title="Prompt Template"
        text="这里是原 WORKFLOW.md 的 Markdown body。模板使用 Jinja2 StrictUndefined，可访问 issue、tracker、workflow、workspace、env。"
      />
      <TextAreaField
        label="Prompt"
        value={settings.prompt_template}
        minRows={18}
        onChange={(value) => updateSettings(onChange, settings, (draft) => {
          draft.prompt_template = value;
        })}
      />
      <div className="templateHints">
        <code>{"{{ issue.identifier }}"}</code>
        <code>{"{{ issue.title }}"}</code>
        <code>{"{{ issue.repository }}"}</code>
        <code>{"{{ issue.url }}"}</code>
        <code>{"{{ workflow.status_policy_markdown }}"}</code>
        <code>{"{{ workflow.success_state }}"}</code>
      </div>
    </>
  );
}

// 函数说明：渲染持久日志页面，支持过滤、打开目录和导出脱敏诊断包。
function LogsPage({
  onMessage,
  onError,
}: {
  onMessage: (message: string | null) => void;
  onError: (message: string | null) => void;
}): JSX.Element {
  const [config, setConfig] = useState<LogConfig | null>(null);
  const [filters, setFilters] = useState<LogQueryFilters>({});
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  // 函数说明：读取第一页日志，过滤条件变化或用户点击 Refresh 时调用。
  const loadFirstPage = useCallback(async () => {
    setBusy(true);
    onError(null);
    try {
      const [nextConfig, result] = await Promise.all([
        fetchLogConfig(),
        queryLogs({ ...filters, cursor: null }),
      ]);
      setConfig(nextConfig);
      setEntries(result.entries);
      setNextCursor(result.next_cursor);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }, [filters, onError]);

  // 函数说明：读取下一页日志并追加到当前列表。
  const loadMore = useCallback(async () => {
    if (nextCursor == null) {
      return;
    }
    setBusy(true);
    onError(null);
    try {
      const result = await queryLogs({ ...filters, cursor: nextCursor });
      setEntries((current) => [...current, ...result.entries]);
      setNextCursor(result.next_cursor);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }, [filters, nextCursor, onError]);

  // 逻辑说明：首次进入 Logs 页面时自动读取一页日志。
  useEffect(() => {
    void loadFirstPage();
  }, [loadFirstPage]);

  // 函数说明：导出诊断包并把路径显示给用户。
  const handleExport = useCallback(async () => {
    setBusy(true);
    onError(null);
    onMessage(null);
    try {
      const result = await exportLogBundle();
      onMessage(`诊断包已导出：${result.path}`);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }, [onError, onMessage]);

  // 函数说明：请求 Electron main 打开日志目录。
  const handleOpenDirectory = useCallback(async () => {
    const result = await openLogDirectory();
    if (result.ok) {
      onMessage("已打开日志目录。");
    } else {
      onError(result.error || "无法打开日志目录");
    }
  }, [onError, onMessage]);

  return (
    <section className="logsPage">
      <Panel title="Log Storage" icon={<ScrollText size={17} aria-hidden="true" />}>
        <div className="logConfigGrid">
          <Metric label="Directory" value={config?.log_dir || "-"} />
          <Metric label="Level" value={config?.level || "-"} />
          <Metric label="Retention" value={config ? `${config.retention_days} days` : "-"} />
          <Metric label="Max File" value={config ? `${config.max_file_mb} MB` : "-"} />
        </div>
        <div className="panelActions">
          <button className="secondaryButton" type="button" onClick={handleOpenDirectory} disabled={busy}>
            <FolderOpen size={15} aria-hidden="true" />
            Open Directory
          </button>
          <button className="secondaryButton" type="button" onClick={handleExport} disabled={busy}>
            <FileDown size={15} aria-hidden="true" />
            Export Bundle
          </button>
        </div>
      </Panel>

      <Panel title="Filters" icon={<RefreshCw size={17} aria-hidden="true" />}>
        <div className="logFilters">
          <SelectField
            label="Level"
            value={filters.level || "all"}
            options={["all", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]}
            onChange={(value) => setFilters((current) => ({
              ...current,
              level: value === "all" ? undefined : value,
            }))}
          />
          <TextField
            label="Event Type"
            value={filters.event_type || ""}
            onChange={(value) => setFilters((current) => ({ ...current, event_type: value || undefined }))}
          />
          <TextField
            label="Identifier"
            value={filters.identifier || ""}
            onChange={(value) => setFilters((current) => ({ ...current, identifier: value || undefined }))}
          />
          <TextField
            label="Keyword"
            value={filters.q || ""}
            onChange={(value) => setFilters((current) => ({ ...current, q: value || undefined }))}
          />
          <button className="primaryButton alignEnd" type="button" onClick={() => void loadFirstPage()} disabled={busy}>
            <RefreshCw size={15} aria-hidden="true" />
            Refresh
          </button>
        </div>
      </Panel>

      <Panel title="Entries" icon={<AlertCircle size={17} aria-hidden="true" />}>
        <LogEntryList entries={entries} />
        {nextCursor != null ? (
          <div className="panelActions">
            <button className="secondaryButton" type="button" onClick={() => void loadMore()} disabled={busy}>
              Load More
            </button>
          </div>
        ) : null}
      </Panel>
    </section>
  );
}

// 函数说明：渲染日志条目列表，错误堆栈和 payload 使用可折叠 details 避免页面过长。
function LogEntryList({ entries }: { entries: LogEntry[] }): JSX.Element {
  if (entries.length === 0) {
    return <EmptyState text="No matching logs" />;
  }

  return (
    <div className="logList">
      {entries.map((entry) => (
        <article className={`logEntry level${entry.level}`} key={`${entry._source}-${entry._cursor}`}>
          <header>
            <span className="logLevel">{entry.level}</span>
            <span className="monoText">{entry.event_type}</span>
            <time>{entry.timestamp ? formatTime(entry.timestamp) : "-"}</time>
            <span className="logSource">{entry._source || entry.logger}</span>
          </header>
          <p>{entry.message}</p>
          {entry.identifier ? <div className="inlineHint compactHint">{entry.identifier}</div> : null}
          {entry.payload && Object.keys(entry.payload).length ? (
            <details className="logDetails">
              <summary>Payload</summary>
              <pre>{JSON.stringify(entry.payload, null, 2)}</pre>
            </details>
          ) : null}
          {entry.exception ? (
            <details className="logDetails">
              <summary>Exception</summary>
              <pre>{entry.exception}</pre>
            </details>
          ) : null}
        </article>
      ))}
    </div>
  );
}

// 函数说明：渲染帮助页面。
function HelpPage(): JSX.Element {
  return (
    <article className="helpPage">
      {helpSections().map((section) => (
        <section className="helpSection" key={section.title}>
          <h2>
            <BookOpen size={18} aria-hidden="true" />
            {section.title}
          </h2>
          {section.paragraphs.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
          {section.items.length ? (
            <ul>
              {section.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}
    </article>
  );
}

// 函数说明：返回离线帮助内容，面向已有 CLI agent 经验但不了解调度器的开发者。
function helpSections(): Array<{ title: string; paragraphs: string[]; items: string[] }> {
  return [
    {
      title: "这个 App 做什么",
      paragraphs: [
        "GitHub Symphony 是一个本地运行的 agent 调度器。它持续读取 GitHub Projects v2 看板，把符合条件的 Issue 或 Pull Request 派发给独立 Codex app-server agent。",
        "你可以把它理解为 Codex CLI 的长期运行控制台：Codex CLI 通常一次处理一个任务，而 GitHub Symphony 负责从任务板中挑选任务、创建工作区、启动 agent、记录事件和处理重试。",
      ],
      items: [
        "任务来源是 GitHub Projects v2，不是本地 todo 文件。",
        "执行对象是 Project 中的 Issue 或 Pull Request。",
        "默认 Autonomy preset 是 PR 前全自动：agent 可在隔离工作区内 branch、commit、push、开/更新 PR、处理 PR feedback 和等待 checks green。",
        "调度器不会把 commit、push、merge 做成内置业务动作；这些远端写操作只由 prompt、approval policy、token 权限和 GitHub tools 模式共同允许的 agent 执行。",
      ],
    },
    {
      title: "快速开始",
      paragraphs: [
        "先准备一个 GitHub Project v2，并确保它包含 Status single-select 字段。状态名称可以完全自定义，例如 Ready、Coding、Human Review、Rework、Shipped；App 只关心你把这些状态分配成哪些角色。",
      ],
      items: [
        "在 Settings / GitHub Project 顶部粘贴 PAT，点击 Connect PAT，然后选择 owner 和 Project。",
        "点击 Load Project Details，让 App 自动读取 Status 字段、状态选项和 Project 中出现过的仓库。",
        "在 GitHub Project 页把阶段分成 Active、Handoff、Terminal 三类，并在 Tools 里选择哪些阶段会受 issue dependencies 阻塞。",
        "默认状态建议为 Todo、In Progress、Rework、Human Review、Merging、Done、Closed、Cancelled；Merging 是 active land 阶段，Human Review 是人工交接阶段。",
        "只读观测需要 Project 和仓库读取权限；允许 agent 写 GitHub、push 分支或更新 PR 时需要相应写权限。",
        "在 Workspace 设置 root 和 after_create hook。常见 hook 是 git clone 目标仓库到当前工作区。",
        "在 Prompt 中保留 Workpad、branch/commit/push/PR、PR feedback sweep、checks green、Human Review 和 Merging land 的规则。",
        "点击 Save & Apply，然后回到 Dashboard 看候选任务、运行中 agent 和事件流。",
      ],
    },
    {
      title: "GitHub Projects v2 概念",
      paragraphs: [
        "Project number 是 GitHub Project URL 中的数字，不是仓库 Issue 编号。Status 字段必须是 Project 的 single-select 字段，本 App 用它判断任务是否可派发、等待交接或已经结束。",
      ],
      items: [
        "Active states：允许派发给 Codex agent 的状态，例如 Todo、In Progress、Rework、Merging。",
        "Handoff states：不再自动派发、等待人工或外部系统接手的状态，例如 Human Review、QA Review、Waiting for Approval。",
        "Terminal states：真正结束、可用于未来清理策略的状态，例如 Shipped、Closed、Cancelled。",
        "Priority 字段可选；如果配置，候选任务会按 priority 升序、创建时间、identifier 排序。",
        "Issue dependencies 只会阻塞你在 Blocked States 中勾选的阶段；API 不可用时按 blocker policy 处理。",
      ],
    },
    {
      title: "Settings 字段怎么填",
      paragraphs: [
        "大多数字段都有保守默认值。第一次配置时优先填 GitHub Project、Workspace 和 Prompt；Codex 和 Tools 可先保持默认。",
      ],
      items: [
        "GitHub REST/GraphQL API 默认指向 github.com；GitHub Enterprise 后续可改这里。",
        "GitHub Project 页会通过 PAT discovery 填充 owner、project number、Status 字段、状态和 repositories。",
        "Workspace root 可以使用 ~，每个任务会在 root 下创建独立目录。",
        "Max concurrent agents 控制并发，建议从 1 到 3 开始。",
        "Completion 默认使用 agent_managed；agent 在 PR 前置门禁达标后把 Project Status 移到 Human Review，App 不在成功 turn 后自动改状态。",
        "approval_policy: never 或 high_trust preset 是高信任模式，会自动批准 app-server 的命令、文件变更和可识别工具 approval prompt；只应配合隔离工作区、受限 token 和可信 prompt 使用。",
        "Logging 默认 DEBUG、保留 14 天，可在 Logs 页面查询和导出诊断包。",
        "Tools mode 为 read_only 时会拒绝 REST 写操作、GraphQL mutation 和 github_update_project_status；read_write 会允许这些写入口。",
      ],
    },
    {
      title: "Prompt 编写指南",
      paragraphs: [
        "Prompt 是每个任务交给 Codex agent 的任务说明。它支持 Jinja2 模板变量，缺失变量会报错，因此变量名要准确。",
      ],
      items: [
        "常用变量：{{ issue.identifier }}、{{ issue.title }}、{{ issue.repository }}、{{ issue.url }}。",
        "阶段策略变量：{{ workflow.status_policy_markdown }}、{{ workflow.active_states }}、{{ workflow.success_state }}。",
        "明确要求 agent 先阅读 Issue/PR 描述和相关代码，再做最小必要修改。",
        "明确验证方式，例如运行哪些测试、如何汇报失败原因。",
        "如果 completion policy 是 agent_managed，要在 prompt 中要求 agent 使用 GitHub 工具把 Project Status 移到目标交接阶段。",
        "进入 Human Review 前要求 Workpad 已更新、PR 已链接、checks green、PR feedback sweep 无未处理 actionable comments。",
        "进入 Merging 后只执行 land 流程；确认人工批准、checks green、分支同步和必要验证后再 squash merge，并把 Project Status 移到 Done。",
        "高信任 prompt 必须明确禁止 force push、直接 push 默认分支、删除远端分支、自动关闭 issue。",
      ],
    },
    {
      title: "Dashboard 怎么看",
      paragraphs: [
        "Dashboard 展示当前调度器状态，不是 GitHub Project 的完整替代品。它重点回答：现在有哪些候选任务、哪些 agent 在跑、最近发生了什么。",
      ],
      items: [
        "Refresh 会请求后端尽快 poll 一次 GitHub。",
        "Stop 只停止本地 run，不修改 GitHub 状态。",
        "Restart 会停止本地 run，并在任务仍可派发时重新启动。",
        "Recent Events 是本地内存事件流，用于排查调度和配置问题。",
        "更完整的错误堆栈、Codex stderr、GitHub API 摘要和 Electron 启动信息在 Logs 页面查看。",
      ],
    },
    {
      title: "常见问题排查",
      paragraphs: [
        "如果没有候选任务，通常不是 App 坏了，而是 Project 状态、仓库 allowlist、token 权限或 blocker policy 不匹配。",
      ],
      items: [
        "Failed to fetch：后端未启动、端口被占用或本地防火墙阻止 127.0.0.1。",
        "token missing：保存 PAT 后点击 Save & Apply，或在环境变量中提供 GITHUB_TOKEN。",
        "Project not found：检查 owner type、owner、project number 和 PAT 的 Project 权限。",
        "No candidate work：确认 Issue/PR 已加入 Project，Status 属于 active states，仓库在 repositories 列表内。",
        "Codex CLI missing：安装并确认 codex app-server 可在终端运行。",
        "Workspace hook 失败：先在目标 workspace root 下手动运行同等 git clone 命令验证 SSH key 和权限。",
      ],
    },
  ];
}

// 函数说明：渲染错误提示。
function Banner({ message }: { message: string }): JSX.Element {
  return (
    <div className="banner" role="alert">
      <AlertCircle size={16} aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

// 函数说明：渲染成功提示。
function SuccessBanner({ message }: { message: string }): JSX.Element {
  return (
    <div className="successBanner" role="status">
      <Check size={16} aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
}

// 函数说明：渲染设置区标题说明。
function SectionIntro({ title, text }: { title: string; text: string }): JSX.Element {
  return (
    <div className="sectionIntro">
      <h2>{title}</h2>
      <p>{text}</p>
    </div>
  );
}

// 函数说明：渲染指标块。
function Metric({ label, value }: { label: string; value: string | number }): JSX.Element {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

// 函数说明：渲染通用面板。
function Panel({
  title,
  icon,
  children,
}: {
  title: string;
  icon: JSX.Element;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="panel">
      <header className="panelHeader">
        {icon}
        <h2>{title}</h2>
      </header>
      {children}
    </section>
  );
}

// 函数说明：渲染运行中 agent 列表。
function RunList({
  runs,
  onStop,
  onRestart,
}: {
  runs: RunRecord[];
  onStop: (issueId: string) => void;
  onRestart: (issueId: string) => void;
}): JSX.Element {
  if (runs.length === 0) {
    return <EmptyState text="No active agents" />;
  }

  return (
    <div className="table" role="table">
      <div className="row headerRow" role="row">
        <span>Issue</span>
        <span>Attempt</span>
        <span>Workspace</span>
        <span>Actions</span>
      </div>
      {runs.map((run) => (
        <div className="row" role="row" key={run.issue_id}>
          <span className="strongText">{run.identifier}</span>
          <span>{run.attempt}</span>
          <span className="monoText">{run.workspace || "-"}</span>
          <span className="actions">
            <button className="iconButton" type="button" title="Restart" onClick={() => onRestart(run.issue_id)}>
              <RotateCcw size={15} aria-hidden="true" />
            </button>
            <button className="iconButton danger" type="button" title="Stop" onClick={() => onStop(run.issue_id)}>
              <CircleStop size={15} aria-hidden="true" />
            </button>
          </span>
        </div>
      ))}
    </div>
  );
}

// 函数说明：渲染候选任务列表。
function CandidateList({ items }: { items: WorkItem[] }): JSX.Element {
  if (items.length === 0) {
    return <EmptyState text="No candidate work" />;
  }

  return (
    <div className="issueList">
      {items.map((item) => (
        <article className="issueItem" key={item.id}>
          <div>
            <a href={item.url} target="_blank" rel="noreferrer">
              {item.identifier}
              <ExternalLink size={13} aria-hidden="true" />
            </a>
            <h3>{item.title}</h3>
          </div>
          <div className="badges">
            <span>{item.state}</span>
            {item.priority == null ? null : <span>P{item.priority}</span>}
            {item.blocked_by_open_count ? <span>Blocked {item.blocked_by_open_count}</span> : null}
          </div>
        </article>
      ))}
    </div>
  );
}

// 函数说明：过滤 Dashboard 上展示的事件，避免常规诊断 poll 淹没真正需要关注的事件。
function dashboardRecentEvents(events: EventRecord[]): EventRecord[] {
  return events.filter(isDashboardEvent);
}

// 函数说明：判断事件是否适合放在 Dashboard 摘要列表中。
function isDashboardEvent(event: EventRecord): boolean {
  // 逻辑说明：`github.debug` 是正常轮询诊断，详情仍可在 Logs 页面按类型查询。
  if (event.event_type.endsWith(".debug")) {
    return false;
  }

  // 逻辑说明：Codex notification 是 app-server 流式协议细节，Dashboard 只保留汇总事件。
  if (event.event_type === "codex.notification") {
    return false;
  }

  return true;
}

// 函数说明：渲染最近事件。
function EventList({
  events,
  emptyText = "No events",
}: {
  events: EventRecord[];
  emptyText?: string;
}): JSX.Element {
  if (events.length === 0) {
    return <EmptyState text={emptyText} />;
  }

  return (
    <ol className="events">
      {events.slice().reverse().map((event) => (
        <li key={event.cursor}>
          <time>{formatTime(event.created_at)}</time>
          <strong>{event.event_type}</strong>
          <span>{event.message}</span>
        </li>
      ))}
    </ol>
  );
}

// 函数说明：渲染空状态。
function EmptyState({ text }: { text: string }): JSX.Element {
  return <div className="emptyState">{text}</div>;
}

// 函数说明：渲染文本输入框。
function TextField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}): JSX.Element {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

// 函数说明：渲染数字输入框。
function NumberField({
  label,
  value,
  min,
  step = 1,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  step?: number;
  onChange: (value: number) => void;
}): JSX.Element {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

// 函数说明：渲染下拉选择框。
function SelectField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}): JSX.Element {
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option value={option} key={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

// 函数说明：渲染布尔开关。
function ToggleField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}): JSX.Element {
  return (
    <label className="toggleField">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  );
}

// 函数说明：渲染多行文本输入。
function TextAreaField({
  label,
  value,
  minRows = 4,
  onChange,
}: {
  label: string;
  value: string;
  minRows?: number;
  onChange: (value: string) => void;
}): JSX.Element {
  return (
    <label className="field fullWidth">
      <span>{label}</span>
      <textarea rows={minRows} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

// 函数说明：渲染 JSON 编辑器；只有 JSON 合法时才写回 settings。
function JsonField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
}): JSX.Element {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2));
  const [invalid, setInvalid] = useState<string | null>(null);

  // 逻辑说明：外部 settings 被导入或重置时，同步 JSON 文本。
  useEffect(() => {
    setText(JSON.stringify(value, null, 2));
    setInvalid(null);
  }, [value]);

  return (
    <label className="field fullWidth">
      <span>{label}</span>
      <textarea
        rows={8}
        value={text}
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          try {
            onChange(JSON.parse(next));
            setInvalid(null);
          } catch (caught) {
            setInvalid(caught instanceof Error ? caught.message : String(caught));
          }
        }}
      />
      {invalid ? <span className="fieldError">{invalid}</span> : null}
    </label>
  );
}

// 函数说明：渲染可增删的字符串列表编辑器。
function ListEditor({
  label,
  values,
  placeholder,
  onChange,
}: {
  label: string;
  values: string[];
  placeholder: string;
  onChange: (values: string[]) => void;
}): JSX.Element {
  const [draft, setDraft] = useState("");

  // 函数说明：把输入框内容添加到列表。
  const addValue = useCallback(() => {
    const value = draft.trim();
    if (!value || values.includes(value)) {
      setDraft("");
      return;
    }
    onChange([...values, value]);
    setDraft("");
  }, [draft, onChange, values]);

  return (
    <div className="listEditor">
      <span>{label}</span>
      <div className="chips">
        {values.map((value) => (
          <button
            className="chip"
            type="button"
            key={value}
            title="Remove"
            onClick={() => onChange(values.filter((item) => item !== value))}
          >
            {value}
            <Trash2 size={12} aria-hidden="true" />
          </button>
        ))}
      </div>
      <div className="listInputRow">
        <input
          type="text"
          value={draft}
          placeholder={placeholder}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              addValue();
            }
          }}
        />
        <button className="iconButton" type="button" title="Add" onClick={addValue}>
          <Plus size={15} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

// 函数说明：渲染从 GitHub Status options 派生出的多选控件。
function OptionChecklist({
  label,
  options,
  selected,
  onChange,
}: {
  label: string;
  options: string[];
  selected: string[];
  onChange: (values: string[]) => void;
}): JSX.Element {
  const selectedSet = new Set(selected);

  // 函数说明：切换单个选项，并保持列表顺序与 GitHub Project 字段选项一致。
  const toggleOption = useCallback(
    (option: string) => {
      const next = selectedSet.has(option)
        ? selected.filter((item) => item !== option)
        : options.filter((item) => selectedSet.has(item) || item === option);
      onChange(next);
    },
    [onChange, options, selected, selectedSet],
  );

  return (
    <div className="optionChecklist">
      <span>{label}</span>
      <div className="checkRows">
        {options.map((option) => (
          <label className="checkRow" key={option}>
            <input
              type="checkbox"
              checked={selectedSet.has(option)}
              onChange={() => toggleOption(option)}
            />
            <span>{option}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

// 函数说明：展示尚未分配角色的 GitHub Status 选项，提醒用户补齐阶段策略。
function StateRoleSummary({ ungroupedStates }: { ungroupedStates: string[] }): JSX.Element {
  return (
    <div className="optionChecklist">
      <span>Ungrouped States</span>
      <div className="checkRows">
        {ungroupedStates.length ? (
          ungroupedStates.map((state) => (
            <span className="checkRow passiveCheckRow" key={state}>
              {state}
            </span>
          ))
        ) : (
          <span className="mutedText">所有已发现阶段都已分组</span>
        )}
      </div>
    </div>
  );
}

// 函数说明：选择最合适的 Status 字段，优先沿用当前配置，其次使用名为 Status 的字段。
function chooseStatusField(
  fields: GitHubProjectFieldOption[],
  currentName: string,
): GitHubProjectFieldOption | null {
  return (
    fields.find((field) => field.name === currentName)
    || fields.find((field) => field.name.toLowerCase() === "status")
    || fields[0]
    || null
  );
}

// 函数说明：选择最合适的 Priority 字段，优先沿用当前配置，其次使用名为 Priority 的字段。
function choosePriorityField(
  fields: GitHubProjectFieldOption[],
  currentName: string | null,
): GitHubProjectFieldOption | null {
  return (
    fields.find((field) => field.name === currentName)
    || fields.find((field) => field.name.toLowerCase() === "priority")
    || null
  );
}

// 函数说明：根据 GitHub Status options 和常见命名推荐 active/terminal 状态集合。
function chooseStates(options: string[], preferred: string[], fallback: "first" | "last"): string[] {
  const preferredSet = new Set(preferred);
  const matched = options.filter((option) => preferredSet.has(option));
  if (matched.length) {
    return matched;
  }
  const fallbackValue = fallback === "first" ? options[0] : options[options.length - 1];
  return fallbackValue ? [fallbackValue] : [];
}

// 函数说明：根据已有配置和常见 review/QA 命名推荐 handoff 阶段。
function chooseHandoffStates(
  options: string[],
  activeStates: string[],
  terminalStates: string[],
  currentHandoffStates: string[],
): string[] {
  const reusable = normalizeToKnownOrder(options, currentHandoffStates).filter(
    (state) => !activeStates.includes(state) && !terminalStates.includes(state),
  );
  if (reusable.length) {
    return reusable;
  }
  return options.filter(
    (state) => isReviewLikeState(state)
      && !activeStates.includes(state)
      && !terminalStates.includes(state),
  );
}

// 函数说明：为自动完成策略选择一个非 active 的目标/交接阶段。
function chooseSuccessState(
  options: string[],
  activeStates: string[],
  handoffStates: string[],
  terminalStates: string[],
  currentSuccessState: string,
): string {
  const nonActiveStates = options.filter((state) => !activeStates.includes(state));
  if (currentSuccessState && nonActiveStates.includes(currentSuccessState)) {
    return currentSuccessState;
  }
  const humanReview = handoffStates.find((state) => state.toLowerCase() === "human review");
  if (humanReview) {
    return humanReview;
  }
  const reviewState = handoffStates.find((state) => isReviewLikeState(state));
  if (reviewState) {
    return reviewState;
  }
  const doneState = terminalStates.find((state) => state.toLowerCase() === "done");
  if (doneState) {
    return doneState;
  }
  if (terminalStates[0]) {
    return terminalStates[0];
  }
  return nonActiveStates[nonActiveStates.length - 1] || options[options.length - 1] || currentSuccessState || "";
}

// 函数说明：为 issue dependencies 选择适用的阻塞阶段，优先沿用当前配置。
function chooseBlockedStates(
  options: string[],
  activeStates: string[],
  currentBlockedStates: string[],
): string[] {
  const reusable = normalizeToKnownOrder(options, currentBlockedStates);
  if (reusable.length) {
    return reusable;
  }
  if (options.includes("Todo")) {
    return ["Todo"];
  }
  return activeStates[0] ? [activeStates[0]] : [];
}

// 函数说明：按 GitHub Status option 顺序保留已知状态，避免保存拼错或陈旧状态。
function normalizeToKnownOrder(options: string[], values: string[]): string[] {
  const selected = new Set(values);
  return options.filter((option) => selected.has(option));
}

// 函数说明：识别常见人工检查、QA、审批、验证类阶段名称。
function isReviewLikeState(state: string): boolean {
  const normalized = state.toLowerCase();
  return ["review", "qa", "approval", "approve", "verify", "verification", "validation"].some(
    (keyword) => normalized.includes(keyword),
  );
}

// 函数说明：汇总当前配置中可用于下拉框的所有阶段名称，优先使用 discovery 缓存。
function knownStatusStates(settings: AppSettings): string[] {
  return Array.from(
    new Set(
      [
        ...settings.tracker.status_options,
        ...settings.tracker.active_states,
        ...settings.tracker.handoff_states,
        ...settings.tracker.terminal_states,
        settings.completion_policy.success_state,
        settings.completion_policy.failure_state || "",
      ].filter(Boolean),
    ),
  );
}

// 函数说明：判断 Codex approval policy 是否明确进入高信任 unattended 模式。
function isNeverApprovalPolicy(value: unknown): boolean {
  if (value === "never") {
    return true;
  }
  if (typeof value === "string") {
    return isHighTrustPresetName(value);
  }
  if (value && typeof value === "object") {
    const candidate = value as Record<string, unknown>;
    return ["preset", "autonomy_preset", "mode"].some((key) => {
      const preset = candidate[key];
      return typeof preset === "string" && isHighTrustPresetName(preset);
    });
  }
  return false;
}

// 函数说明：归一化 high-trust preset 名称，和后端配置解析保持一致。
function isHighTrustPresetName(value: string): boolean {
  const normalized = value.trim().toLowerCase().replaceAll(" ", "_");
  return ["high_trust", "high-trust", "pr_full_auto", "pr-before-full-auto"].includes(normalized);
}

// 函数说明：根据表单状态生成 token 更新语义。
function tokenUpdateFromForm(mode: TokenUpdate["mode"], value: string): TokenUpdate {
  if (mode === "clear") {
    return { mode: "clear" };
  }
  if (mode === "set" && value.trim()) {
    return { mode: "set", value: value.trim() };
  }
  return { mode: "unchanged" };
}

// 函数说明：以不可变方式更新 settings 深层字段。
function updateSettings(
  onChange: (settings: AppSettings) => void,
  settings: AppSettings,
  mutate: (draft: AppSettings) => void,
): void {
  const draft = structuredClone(settings) as AppSettings;
  mutate(draft);
  onChange(draft);
}

// 函数说明：汇总顶部指标。
function summarizeState(state: StateSnapshot | null): {
  running: number;
  candidates: number;
  blocked: number;
  lastPoll: string;
} {
  if (!state) {
    return { running: 0, candidates: 0, blocked: 0, lastPoll: "-" };
  }

  const blocked = state.candidates.filter((item) => (item.blocked_by_open_count || 0) > 0).length;
  return {
    running: state.running.length,
    candidates: state.candidates.length,
    blocked,
    lastPoll: state.last_poll_at ? formatTime(state.last_poll_at) : "-",
  };
}

// 函数说明：生成 stop 回调并在操作后刷新状态。
function stopRunAndReload(load: () => Promise<void>): (issueId: string) => void {
  return (issueId: string) => {
    void stopRun(issueId).then(load);
  };
}

// 函数说明：生成 restart 回调并在操作后刷新状态。
function restartRunAndReload(load: () => Promise<void>): (issueId: string) => void {
  return (issueId: string) => {
    void restartRun(issueId).then(load);
  };
}

// 函数说明：格式化事件时间。
function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
