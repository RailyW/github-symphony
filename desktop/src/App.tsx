import {
  AlertCircle,
  CircleStop,
  ExternalLink,
  Play,
  RefreshCw,
  RotateCcw,
  Server,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { fetchState, refreshState, restartRun, stopRun } from "./api";
import type { EventRecord, RunRecord, StateSnapshot, WorkItem } from "./types";

// 函数说明：桌面仪表盘根组件。
export function App(): JSX.Element {
  const [state, setState] = useState<StateSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 函数说明：从后端刷新状态，并把错误展示给用户。
  const load = useCallback(async () => {
    try {
      const next = await fetchState();
      setState(next);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, []);

  // 逻辑说明：启动后立即拉取状态，并保持轻量轮询。
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [load]);

  // 函数说明：触发后端 refresh 后立即刷新 UI。
  const handleRefresh = useCallback(async () => {
    setBusy(true);
    try {
      await refreshState();
      await load();
    } finally {
      setBusy(false);
    }
  }, [load]);

  const metrics = useMemo(() => summarizeState(state), [state]);

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1>GitHub Symphony</h1>
          <p>{state?.workflow_path || "WORKFLOW.md"}</p>
        </div>
        <button className="primaryButton" type="button" onClick={handleRefresh} disabled={busy}>
          <RefreshCw size={16} aria-hidden="true" />
          Refresh
        </button>
      </header>

      {error ? <Banner message={error} /> : null}
      {state?.config_error ? <Banner message={state.config_error} /> : null}

      <section className="metrics" aria-label="Service metrics">
        <Metric label="Running" value={metrics.running} />
        <Metric label="Candidates" value={metrics.candidates} />
        <Metric label="Blocked" value={metrics.blocked} />
        <Metric label="Last Poll" value={metrics.lastPoll} />
      </section>

      <section className="layout">
        <Panel title="Running Agents" icon={<Play size={17} aria-hidden="true" />}>
          <RunList runs={state?.running || []} onStop={stopRunAndReload(load)} onRestart={restartRunAndReload(load)} />
        </Panel>

        <Panel title="Candidate Work" icon={<Server size={17} aria-hidden="true" />}>
          <CandidateList items={state?.candidates || []} />
        </Panel>
      </section>

      <Panel title="Recent Events" icon={<AlertCircle size={17} aria-hidden="true" />}>
        <EventList events={state?.recent_events || []} />
      </Panel>
    </main>
  );
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

// 函数说明：渲染最近事件。
function EventList({ events }: { events: EventRecord[] }): JSX.Element {
  if (events.length === 0) {
    return <EmptyState text="No events" />;
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
