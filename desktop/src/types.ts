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
};

declare global {
  interface Window {
    symphony?: {
      apiBaseUrl: string;
    };
  }
}
