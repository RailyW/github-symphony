"""运行时组件装配工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import SymphonyConfig
from .events import EventStore
from .runner import AgentRunner


@dataclass
class RuntimeComponents:
    """一次配置对应的运行时依赖集合。"""

    config: SymphonyConfig
    prompt_template: str
    tracker: object
    runner_factory: Callable[[], AgentRunner]


# 函数说明：根据配置组装 GitHub client、tracker、动态工具和 runner factory。
def build_runtime_components(
    config: SymphonyConfig,
    prompt_template: str,
    events: EventStore,
) -> RuntimeComponents:
    # 逻辑说明：这些导入依赖 GitHub integration，延迟到运行时可减少基础配置测试的加载面。
    from symphony_github.integrations.github.client import GitHubClient
    from symphony_github.integrations.github.dynamic_tools import GitHubDynamicTools
    from symphony_github.integrations.github.tracker import GitHubProjectsV2Tracker

    client = GitHubClient(
        token=config.tracker.api_token,
        api_base_url=config.tracker.api_base_url,
        graphql_url=config.tracker.graphql_url,
    )
    tracker = GitHubProjectsV2Tracker(
        config=config.tracker,
        blocker_policy=config.blocker_policy,
        client=client,
        events=events,
    )
    github_tools = GitHubDynamicTools(client, config.tracker, config.tools.github)

    # 函数说明：为每个 dispatch 创建新的 runner，避免跨任务共享 Codex app-server 状态。
    def runner_factory() -> AgentRunner:
        return AgentRunner(
            config=config,
            prompt_template=prompt_template,
            tracker=tracker,
            events=events,
            github_tools=github_tools,
        )

    return RuntimeComponents(
        config=config,
        prompt_template=prompt_template,
        tracker=tracker,
        runner_factory=runner_factory,
    )
