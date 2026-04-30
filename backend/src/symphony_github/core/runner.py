"""单任务 Codex agent runner。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from symphony_github.codex.app_server import CodexAppServerClient
from symphony_github.integrations.github.dynamic_tools import GitHubDynamicTools

from .config import SymphonyConfig
from .events import EventStore
from .models import RunRecord, WorkItem
from .prompt import render_prompt
from .workspace import WorkspaceManager


class TrackerProtocol(Protocol):
    """runner 需要的 tracker 最小接口。"""

    # 函数说明：刷新指定 issue id 的当前状态。
    async def fetch_issue_states_by_ids(self, issue_ids: list) -> Dict[str, WorkItem]:
        ...


@dataclass
class RunnerResult:
    """runner 完成后的结果。"""

    should_continue: bool
    error: Optional[str] = None


class AgentRunner:
    """执行单个 GitHub work item。"""

    # 函数说明：保存运行依赖，便于测试时替换 tracker 或 Codex client。
    def __init__(
        self,
        config: SymphonyConfig,
        prompt_template: str,
        tracker: TrackerProtocol,
        events: EventStore,
        github_tools: Optional[GitHubDynamicTools] = None,
    ) -> None:
        self.config = config
        self.prompt_template = prompt_template
        self.tracker = tracker
        self.events = events
        self.github_tools = github_tools
        self.workspace_manager = WorkspaceManager(config.workspace)

    # 函数说明：准备工作区并运行 Codex，返回是否需要 continuation retry。
    async def run(self, item: WorkItem, run_record: RunRecord) -> RunnerResult:
        try:
            workspace = self.workspace_manager.prepare(item)
            run_record.workspace = workspace
            run_record.touch()
            self.events.append(
                "runner.workspace.ready",
                "任务工作区已准备",
                {"identifier": item.identifier, "workspace": workspace},
            )

            prompt = render_prompt(
                self.prompt_template,
                {
                    "issue": item,
                    "tracker": self.config.tracker,
                    "workspace": workspace,
                    "env": {},
                },
            )
            codex = self._build_codex_client(workspace)

            try:
                # 逻辑说明：一个 runner 内最多运行 max_turns 次，避免 agent 无限循环。
                for turn_index in range(self.config.agent.max_turns):
                    run_record.attempt = turn_index + 1
                    result = await codex.run_turn(prompt)
                    run_record.thread_id = result.thread_id
                    run_record.turn_id = result.turn_id
                    run_record.touch()

                    refreshed = await self.tracker.fetch_issue_states_by_ids([item.id])
                    latest = refreshed.get(item.id)

                    # 逻辑说明：如果 tracker 已把任务移出 active states，runner 就结束。
                    if latest is None or latest.state not in self.config.tracker.active_states:
                        return RunnerResult(should_continue=False)

                    self.events.append(
                        "runner.continuation",
                        "Codex turn 正常结束但任务仍处于 active state，准备 continuation",
                        {"identifier": item.identifier, "state": latest.state},
                    )
                    await asyncio.sleep(1)
            finally:
                await codex.close()

            return RunnerResult(should_continue=False)
        except Exception as exc:  # noqa: BLE001 - runner 边界统一错误，调度器负责重试。
            run_record.last_error = str(exc)
            run_record.touch()
            self.events.append(
                "runner.error",
                "任务 runner 失败",
                {"identifier": item.identifier, "error": str(exc)},
            )
            return RunnerResult(should_continue=True, error=str(exc))

    # 函数说明：按配置创建 Codex app-server client。
    def _build_codex_client(self, workspace: str) -> CodexAppServerClient:
        specs = self.github_tools.tool_specs() if self.github_tools is not None else []

        # 函数说明：把 GitHubDynamicTools 返回值适配成 app-server response 字典。
        async def execute_tool(tool: str, arguments: Any) -> Dict[str, Any]:
            if self.github_tools is None:
                return {
                    "success": False,
                    "contentItems": [{"type": "inputText", "text": "GitHub tools disabled"}],
                }
            return (await self.github_tools.execute(tool, arguments)).to_rpc_result()

        return CodexAppServerClient(
            config=self.config.codex,
            workspace=workspace,
            events=self.events,
            dynamic_tool_specs=specs,
            dynamic_tool_executor=execute_tool,
        )
