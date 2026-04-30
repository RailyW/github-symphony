export type WorkItem = {
  id: string;
  project_item_id: string;
  identifier: string;
  kind: string;
  title: string;
  body: string | null;
  state: string;
  url: string;
  repository: string;
  number: number;
  labels: string[];
  assignees: string[];
  created_at: string | null;
  updated_at: string | null;
  priority: number | null;
  blocked_by_open_count: number | null;
};

export type RunRecord = {
  issue_id: string;
  identifier: string;
  state: string;
  workspace: string;
  attempt: number;
  thread_id: string | null;
  turn_id: string | null;
  started_at: string;
  updated_at: string;
  last_error: string | null;
};

export type EventRecord = {
  cursor: number;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type StateSnapshot = {
  service: string;
  workflow_path: string | null;
  config_error: string | null;
  running: RunRecord[];
  candidates: WorkItem[];
  recent_events: EventRecord[];
  last_poll_at: string | null;
  settings_generation: number;
  settings_error: string | null;
};

export type AppSettings = {
  tracker: {
    owner_type: "org" | "user";
    owner: string;
    project_number: number;
    repositories: string[];
    status_field: string;
    active_states: string[];
    terminal_states: string[];
    priority_field: string | null;
    api_base_url: string;
    graphql_url: string;
  };
  blocker_policy: {
    kind: string;
    unavailable_behavior: string;
  };
  workspace: {
    root: string;
    cleanup_terminal_workspaces: boolean;
    hooks: {
      after_create: string | null;
    };
  };
  agent: {
    max_concurrent_agents: number;
    max_turns: number;
    poll_interval_ms: number;
    max_retry_backoff_ms: number;
  };
  codex: {
    command: string;
    model: string | null;
    approval_policy: unknown;
    thread_sandbox: string;
    turn_sandbox_policy: unknown;
  };
  tools: {
    github: {
      enabled: boolean;
      mode: "read_only" | "read_write";
    };
  };
  prompt_template: string;
};

export type TokenStatus = {
  configured: boolean;
  encryptionAvailable: boolean;
};

export type TokenUpdate =
  | { mode: "unchanged" }
  | { mode: "set"; value: string }
  | { mode: "clear" };

export type SettingsLoadResult = {
  settings: AppSettings;
  token: TokenStatus;
  settingsPath: string;
};

export type GitHubDiscoveryRequest = {
  github_token?: string;
  use_saved_token?: boolean;
  api_base_url?: string;
  graphql_url?: string;
};

export type GitHubOwnerOption = {
  owner_type: "org" | "user";
  login: string;
  display_name: string | null;
};

export type GitHubDiscoveryConnectResult = {
  viewer: {
    login: string;
    name: string | null;
  };
  owners: GitHubOwnerOption[];
  warnings: string[];
};

export type GitHubProjectOption = {
  id: string;
  number: number;
  title: string;
  owner: string;
  owner_type: "org" | "user";
  closed: boolean;
  updated_at: string | null;
};

export type GitHubProjectFieldOption = {
  id: string;
  name: string;
  data_type: string;
  kind: "single_select" | "number" | "text" | "other";
  options: Array<{
    id: string;
    name: string;
    color: string | null;
  }>;
};

export type GitHubProjectDiscoveryResult = {
  fields: GitHubProjectFieldOption[];
  status_fields: GitHubProjectFieldOption[];
  priority_fields: GitHubProjectFieldOption[];
  repositories: string[];
  item_sample_count: number;
  warnings: string[];
};

declare global {
  interface Window {
    symphony?: {
      apiBaseUrl: string;
    };
    symphonySettings?: {
      load: () => Promise<SettingsLoadResult>;
      save: (settings: AppSettings, tokenUpdate: TokenUpdate) => Promise<SettingsLoadResult>;
      apply: (settings: AppSettings) => Promise<{ status: string; generation: number }>;
      importWorkflow: () => Promise<
        | { canceled: true }
        | {
            canceled: false;
            sourcePath: string;
            settings: AppSettings;
            token_hint: string | null;
            warnings: string[];
          }
      >;
      exportWorkflow: (settings: AppSettings) => Promise<
        | { canceled: true }
        | {
            canceled: false;
            filePath: string;
          }
      >;
      tokenStatus: () => Promise<TokenStatus>;
      discoverConnect: (
        request: GitHubDiscoveryRequest,
      ) => Promise<GitHubDiscoveryConnectResult>;
      discoverProjects: (
        request: GitHubDiscoveryRequest & { owner_type: "org" | "user"; owner: string },
      ) => Promise<{ projects: GitHubProjectOption[]; warnings: string[] }>;
      discoverProject: (
        request: GitHubDiscoveryRequest & {
          owner_type: "org" | "user";
          owner: string;
          project_number: number;
        },
      ) => Promise<GitHubProjectDiscoveryResult>;
    };
  }
}
