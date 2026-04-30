"""长期运行的任务调度器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol, Set

from .config import SymphonyConfig
from .events import EventStore
from .models import RunRecord, StateSnapshot, WorkItem
from .runner import AgentRunner


class TrackerProtocol(Protocol):
    """调度器依赖的 tracker 接口。"""

    # 函数说明：读取可派发候选任务。
    async def fetch_candidate_issues(self) -> List[WorkItem]:
        ...

    # 函数说明：按状态读取任务，主要用于 terminal reconciliation。
    async def fetch_issues_by_states(self, state_names: List[str]) -> List[WorkItem]:
        ...

    # 函数说明：刷新运行中任务状态。
    async def fetch_issue_states_by_ids(self, issue_ids: List[str]) -> Dict[str, WorkItem]:
        ...


@dataclass
class RetryEntry:
    """失败重试状态。"""

    attempts: int = 0
    next_retry_at: float = 0.0


class Orchestrator:
    """读取 tracker 并按规则派发 Codex runner。"""

    # 函数说明：保存调度依赖和内存状态。
    def __init__(
        self,
        config: SymphonyConfig,
        prompt_template: str,
        tracker: TrackerProtocol,
        runner_factory,
        events: Optional[EventStore] = None,
    ) -> None:
        self.config = config
        self.prompt_template = prompt_template
        self.tracker = tracker
        self.runner_factory = runner_factory
        self.events = events or EventStore()
        self.running: Dict[str, RunRecord] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._claimed: Set[str] = set()
        self._retry: Dict[str, RetryEntry] = {}
        self._last_candidates: List[WorkItem] = []
        self._last_poll_at: Optional[str] = None
        self._stopped = asyncio.Event()
        self._refresh_requested = asyncio.Event()

    # 函数说明：启动主循环，直到 stop 被调用。
    async def run_forever(self) -> None:
        self.events.append("orchestrator.started", "GitHub Symphony 调度器已启动")

        while not self._stopped.is_set():
            await self.poll_once()

            # 逻辑说明：等待 poll interval 或手动 refresh，二者任一发生就进入下一轮。
            try:
                await asyncio.wait_for(
                    self._refresh_requested.wait(),
                    timeout=self.config.agent.poll_interval_ms / 1000,
                )
            except asyncio.TimeoutError:
                pass
            self._refresh_requested.clear()

    # 函数说明：请求调度器停止并取消所有运行中的本地任务。
    async def stop(self) -> None:
        self._stopped.set()
        for task in list(self._tasks.values()):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self.events.append("orchestrator.stopped", "GitHub Symphony 调度器已停止")

    # 函数说明：触发下一次 poll 尽快执行。
    def trigger_refresh(self) -> None:
        self._refresh_requested.set()
        self.events.append("orchestrator.refresh_requested", "已请求立即刷新")

    # 函数说明：执行一次 tracker poll、状态回查和派发。
    async def poll_once(self) -> None:
        self._last_poll_at = datetime.now(timezone.utc).isoformat()
        await self._reconcile_running()
        candidates = await self.tracker.fetch_candidate_issues()
        self._last_candidates = sorted(candidates, key=_dispatch_sort_key)

        for item in self._last_candidates:
            if not self._can_dispatch(item):
                continue
            self._dispatch(item)

    # 函数说明：停止某个本地运行，不改变 GitHub 状态。
    async def stop_run(self, issue_id: str) -> bool:
        task = self._tasks.get(issue_id)
        if task is None:
            return False
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._tasks.pop(issue_id, None)
        self.running.pop(issue_id, None)
        self._claimed.discard(issue_id)
        self.events.append("orchestrator.run_stopped", "已停止本地运行", {"issue_id": issue_id})
        return True

    # 函数说明：重启某个任务；如果当前不在候选列表中则只停止现有运行。
    async def restart_run(self, issue_id: str) -> bool:
        await self.stop_run(issue_id)
        for item in self._last_candidates:
            if item.id == issue_id and self._can_dispatch(item):
                self._dispatch(item)
                return True
        return False

    # 函数说明：生成当前服务状态快照。
    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(
            service="github-symphony",
            workflow_path=self.config.workflow_path,
            config_error=None,
            running=list(self.running.values()),
            candidates=self._last_candidates,
            recent_events=self.events.recent(100),
            last_poll_at=self._last_poll_at,
        )

    # 函数说明：按 tracker 状态回收已移出 active states 的运行。
    async def _reconcile_running(self) -> None:
        if not self.running:
            return

        refreshed = await self.tracker.fetch_issue_states_by_ids(list(self.running.keys()))
        for issue_id, run in list(self.running.items()):
            latest = refreshed.get(issue_id)

            # 逻辑说明：任务消失或离开 active states 时，停止本地运行记录。
            if latest is None or latest.state not in self.config.tracker.active_states:
                task = self._tasks.pop(issue_id, None)
                if task is not None:
                    task.cancel()
                self.running.pop(issue_id, None)
                self._claimed.discard(issue_id)
                self.events.append(
                    "orchestrator.reconciled",
                    "运行中任务已不再处于 active state",
                    {"issue_id": issue_id, "identifier": run.identifier},
                )

    # 函数说明：判断候选任务是否可派发。
    def _can_dispatch(self, item: WorkItem) -> bool:
        if item.state not in self.config.tracker.active_states:
            return False
        if item.state in self.config.tracker.terminal_states:
            return False
        if item.id in self.running or item.id in self._claimed:
            return False
        if len(self.running) + len(self._claimed) >= self.config.agent.max_concurrent_agents:
            return False
        if _is_blocked_todo(item):
            return False
        if not self._retry_ready(item.id):
            return False
        return True

    # 函数说明：派发任务并创建后台 asyncio task。
    def _dispatch(self, item: WorkItem) -> None:
        self._claimed.add(item.id)
        run_record = RunRecord(
            issue_id=item.id,
            identifier=item.identifier,
            state="running",
            workspace="",
        )
        self.running[item.id] = run_record
        self.events.append(
            "orchestrator.dispatched",
            "任务已派发给 Codex runner",
            {"identifier": item.identifier, "issue_id": item.id},
        )
        self._tasks[item.id] = asyncio.create_task(self._run_item(item, run_record))

    # 函数说明：执行后台 runner，并根据结果登记重试。
    async def _run_item(self, item: WorkItem, run_record: RunRecord) -> None:
        try:
            runner: AgentRunner = self.runner_factory()
            result = await runner.run(item, run_record)

            if result.should_continue:
                self._schedule_retry(item.id)
            else:
                self._retry.pop(item.id, None)
        finally:
            self._claimed.discard(item.id)
            self.running.pop(item.id, None)
            self._tasks.pop(item.id, None)

    # 函数说明：判断失败重试是否已经到时间。
    def _retry_ready(self, issue_id: str) -> bool:
        entry = self._retry.get(issue_id)
        if entry is None:
            return True
        return asyncio.get_running_loop().time() >= entry.next_retry_at

    # 函数说明：按照指数退避登记下一次重试时间。
    def _schedule_retry(self, issue_id: str) -> None:
        entry = self._retry.setdefault(issue_id, RetryEntry())
        entry.attempts += 1
        delay_ms = min(
            10000 * (2 ** max(0, entry.attempts - 1)),
            self.config.agent.max_retry_backoff_ms,
        )
        entry.next_retry_at = asyncio.get_running_loop().time() + delay_ms / 1000
        self.events.append(
            "orchestrator.retry_scheduled",
            "任务失败，已安排重试",
            {"issue_id": issue_id, "attempts": entry.attempts, "delay_ms": delay_ms},
        )


# 函数说明：判断 Todo 任务是否被未完成依赖阻塞。
def _is_blocked_todo(item: WorkItem) -> bool:
    return item.state == "Todo" and (item.blocked_by_open_count or 0) > 0


# 函数说明：生成派发排序 key：priority、创建时间、identifier。
def _dispatch_sort_key(item: WorkItem) -> tuple:
    priority = item.priority if item.priority is not None else float("inf")
    created = item.created_at or ""
    return (priority, created, item.identifier)
