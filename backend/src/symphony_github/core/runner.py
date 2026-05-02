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
from .state_policy import build_workflow_prompt_context
from .workspace import WorkspaceManager


class TrackerProtocol(Protocol):
    """runner 需要的 tracker 最小接口。"""

    # 函数说明：刷新指定 issue id 的当前状态。
    async def fetch_issue_states_by_ids(self, issue_ids: list) -> Dict[str, WorkItem]:
        ...

    # 函数说明：把 Project v2 item 的 Status 更新到指定状态。
    async def update_project_status(self, project_item_id: str, state_name: str) -> Dict[str, Any]:
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
                    "workflow": build_workflow_prompt_context(self.config),
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

                    if _turn_state_is_failure(result.final_state):
                        message = f"Codex turn 结束状态异常：{result.final_state}"
                        run_record.state = "failed"
                        run_record.last_error = message
                        run_record.touch()
                        self.events.append(
                            "runner.turn_failed",
                            "Codex turn 未正常完成",
                            {
                                "identifier": item.identifier,
                                "thread_id": result.thread_id,
                                "turn_id": result.turn_id,
                                "final_state": result.final_state,
                            },
                        )
                        return RunnerResult(should_continue=True, error=message)

                    completion_error = await self._apply_success_completion_policy(item, run_record)
                    if completion_error is not None:
                        return RunnerResult(should_continue=True, error=completion_error)

                    refreshed = await self.tracker.fetch_issue_states_by_ids([item.id])
                    latest = refreshed.get(item.id)

                    # 逻辑说明：如果 tracker 已把任务移出 active states，runner 就结束。
                    if latest is None or latest.state not in self.config.tracker.active_states:
                        return RunnerResult(should_continue=False)

                    if turn_index + 1 >= self.config.agent.max_turns:
                        message = (
                            "Codex turn 正常结束但任务仍处于 active state，"
                            f"已耗尽 agent.max_turns={self.config.agent.max_turns}"
                        )
                        run_record.state = "failed"
                        run_record.last_error = message
                        run_record.touch()
                        self.events.append(
                            "runner.max_turns_exhausted",
                            "Codex continuation 达到最大轮数但任务仍处于 active state",
                            {
                                "identifier": item.identifier,
                                "state": latest.state,
                                "max_turns": self.config.agent.max_turns,
                            },
                        )
                        return RunnerResult(should_continue=True, error=message)

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

    # 函数说明：根据 completion_policy 在成功 turn 后自动更新 GitHub Project 状态。
    async def _apply_success_completion_policy(
        self,
        item: WorkItem,
        run_record: RunRecord,
    ) -> Optional[str]:
        policy = self.config.completion_policy
        if not policy.mark_done_after_successful_turn or policy.kind in {"none", "agent_managed"}:
            return None

        try:
            # 逻辑说明：只更新 Project item Status，不关闭 Issue、不 merge PR、不 push 代码。
            # 目标状态可以是 Human Review、Ready for QA、Done 等任意非 active 阶段；
            # 这样能让成功任务离开 active states，从源头阻止下一轮 poll 重复派发。
            await self.tracker.update_project_status(item.project_item_id, policy.success_state)
        except Exception as exc:  # noqa: BLE001 - 完成状态更新失败必须转成可重试 runner 结果。
            message = str(exc)
            run_record.state = "failed"
            run_record.last_error = message
            run_record.touch()
            self.events.append(
                "orchestrator.completion_status_update_failed",
                "Codex turn 成功，但更新 GitHub Project 完成状态失败",
                {
                    "issue_id": item.id,
                    "identifier": item.identifier,
                    "project_item_id": item.project_item_id,
                    "target_state": policy.success_state,
                    "error": message,
                },
            )
            return message

        self.events.append(
            "orchestrator.completion_status_updated",
            "Codex turn 成功，已更新 GitHub Project 完成状态",
            {
                "issue_id": item.id,
                "identifier": item.identifier,
                "project_item_id": item.project_item_id,
                "target_state": policy.success_state,
            },
        )
        return None

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
            github_token=self.config.tracker.api_token,
            dynamic_tool_specs=specs,
            dynamic_tool_executor=execute_tool,
        )


# 函数说明：判断 Codex turn final_state 是否表示失败或取消，避免把失败 turn 移到成功目标阶段。
def _turn_state_is_failure(final_state: Optional[str]) -> bool:
    if final_state is None:
        return False
    return final_state.lower() in {"failed", "failure", "error", "cancelled", "canceled"}
