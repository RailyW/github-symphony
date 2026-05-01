"""运行配置模型和归一化逻辑。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TrackerConfig:
    """GitHub Projects v2 tracker 配置。"""

    kind: str
    owner_type: str
    owner: str
    project_number: int
    repositories: List[str]
    api_token: Optional[str]
    status_field: str = "Status"
    active_states: List[str] = field(default_factory=lambda: ["Todo", "In Progress"])
    terminal_states: List[str] = field(
        default_factory=lambda: ["Done", "Closed", "Cancelled"]
    )
    priority_field: Optional[str] = None
    api_base_url: str = "https://api.github.com"
    graphql_url: str = "https://api.github.com/graphql"


@dataclass
class BlockerPolicyConfig:
    """阻塞关系读取配置。"""

    kind: str = "github_issue_dependencies"
    unavailable_behavior: str = "treat_unblocked"


@dataclass
class WorkspaceHooksConfig:
    """工作区 hook 配置。"""

    after_create: Optional[str] = None


@dataclass
class WorkspaceConfig:
    """本地工作区配置。"""

    root: str
    hooks: WorkspaceHooksConfig = field(default_factory=WorkspaceHooksConfig)
    cleanup_terminal_workspaces: bool = False


@dataclass
class AgentConfig:
    """agent 调度配置。"""

    max_concurrent_agents: int = 3
    max_turns: int = 20
    poll_interval_ms: int = 10000
    max_retry_backoff_ms: int = 300000


@dataclass
class CodexConfig:
    """Codex app-server 配置。"""

    command: str = "codex app-server"
    model: Optional[str] = None
    approval_policy: Any = field(
        default_factory=lambda: {
            "granular": {
                "sandbox_approval": True,
                "rules": True,
                "mcp_elicitations": True,
            }
        }
    )
    thread_sandbox: str = "workspace-write"
    turn_sandbox_policy: Dict[str, Any] = field(
        default_factory=lambda: {"type": "workspaceWrite", "networkAccess": True}
    )


@dataclass
class GithubToolConfig:
    """注入给 Codex 的 GitHub 动态工具配置。"""

    enabled: bool = True
    mode: str = "read_write"


@dataclass
class ToolsConfig:
    """动态工具总配置。"""

    github: GithubToolConfig = field(default_factory=GithubToolConfig)


@dataclass
class CompletionPolicyConfig:
    """Codex turn 成功后的本地完成策略配置。"""

    kind: str = "update_project_status"
    success_state: str = "Done"
    failure_state: Optional[str] = "Rework"
    mark_done_after_successful_turn: bool = True
    close_issue: bool = False


@dataclass
class LoggingConfig:
    """持久诊断日志配置。"""

    level: str = "DEBUG"
    retention_days: int = 14
    max_file_mb: int = 10


@dataclass
class SymphonyConfig:
    """完整运行配置。"""

    tracker: TrackerConfig
    blocker_policy: BlockerPolicyConfig
    workspace: WorkspaceConfig
    agent: AgentConfig = field(default_factory=AgentConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    completion_policy: CompletionPolicyConfig = field(default_factory=CompletionPolicyConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    workflow_path: Optional[str] = None


# 函数说明：从 WORKFLOW front matter 字典创建完整配置，并执行必要校验。
def build_config(raw: Dict[str, Any], workflow_path: Optional[str] = None) -> SymphonyConfig:
    # 逻辑说明：tracker 是唯一必填块；没有 tracker 时服务无法知道任务来源。
    tracker_raw = _expect_mapping(raw.get("tracker"), "tracker")
    workspace_raw = _expect_mapping(raw.get("workspace"), "workspace")

    tracker = TrackerConfig(
        kind=_expect_string(tracker_raw.get("kind"), "tracker.kind"),
        owner_type=_expect_string(tracker_raw.get("owner_type", "org"), "tracker.owner_type"),
        owner=_expect_string(tracker_raw.get("owner"), "tracker.owner"),
        project_number=int(
            _expect_present(tracker_raw.get("project_number"), "tracker.project_number")
        ),
        repositories=_string_list(tracker_raw.get("repositories"), "tracker.repositories"),
        api_token=_expand_optional_secret(
            tracker_raw.get("api_token") or os.environ.get("GITHUB_TOKEN")
        ),
        status_field=str(tracker_raw.get("status_field") or "Status"),
        active_states=_string_list(
            tracker_raw.get("active_states", ["Todo", "In Progress"]),
            "tracker.active_states",
        ),
        terminal_states=_string_list(
            tracker_raw.get("terminal_states", ["Done", "Closed", "Cancelled"]),
            "tracker.terminal_states",
        ),
        priority_field=_optional_string(tracker_raw.get("priority_field")),
        api_base_url=str(tracker_raw.get("api_base_url") or "https://api.github.com"),
        graphql_url=str(tracker_raw.get("graphql_url") or "https://api.github.com/graphql"),
    )
    _validate_tracker(tracker)

    blocker_policy = _build_blocker_policy(raw.get("blocker_policy"))
    workspace = _build_workspace(workspace_raw, workflow_path)
    agent = _build_agent(raw.get("agent"))
    codex = _build_codex(raw.get("codex"))
    tools = _build_tools(raw.get("tools"))
    completion_policy = _build_completion_policy(raw.get("completion_policy"))
    _validate_completion_policy(completion_policy, tracker)
    logging = _build_logging(raw.get("logging"))
    _validate_logging_level(logging.level)

    return SymphonyConfig(
        tracker=tracker,
        blocker_policy=blocker_policy,
        workspace=workspace,
        agent=agent,
        codex=codex,
        tools=tools,
        completion_policy=completion_policy,
        logging=logging,
        workflow_path=workflow_path,
    )


# 函数说明：构建阻塞策略配置，缺失时使用 GitHub issue dependencies。
def _build_blocker_policy(raw: Any) -> BlockerPolicyConfig:
    mapping = raw if isinstance(raw, dict) else {}
    return BlockerPolicyConfig(
        kind=str(mapping.get("kind") or "github_issue_dependencies"),
        unavailable_behavior=str(mapping.get("unavailable_behavior") or "treat_unblocked"),
    )


# 函数说明：构建工作区配置，并把相对路径解析到 WORKFLOW 所在目录。
def _build_workspace(raw: Dict[str, Any], workflow_path: Optional[str]) -> WorkspaceConfig:
    root_value = _expect_string(raw.get("root"), "workspace.root")
    expanded_root = os.path.expanduser(_expand_string(root_value))
    root_path = Path(expanded_root)

    # 逻辑说明：相对 root 以 WORKFLOW.md 所在目录为基准，便于配置随仓库移动。
    if not root_path.is_absolute() and workflow_path:
        root_path = Path(workflow_path).resolve().parent / root_path

    hooks_raw = raw.get("hooks") if isinstance(raw.get("hooks"), dict) else {}
    hooks = WorkspaceHooksConfig(after_create=_optional_string(hooks_raw.get("after_create")))
    return WorkspaceConfig(
        root=str(root_path),
        hooks=hooks,
        cleanup_terminal_workspaces=bool(raw.get("cleanup_terminal_workspaces", False)),
    )


# 函数说明：构建 agent 调度配置，并约束并发和轮询参数的下限。
def _build_agent(raw: Any) -> AgentConfig:
    mapping = raw if isinstance(raw, dict) else {}
    return AgentConfig(
        max_concurrent_agents=max(1, int(mapping.get("max_concurrent_agents", 3))),
        max_turns=max(1, int(mapping.get("max_turns", 20))),
        poll_interval_ms=max(1000, int(mapping.get("poll_interval_ms", 10000))),
        max_retry_backoff_ms=max(1000, int(mapping.get("max_retry_backoff_ms", 300000))),
    )


# 函数说明：构建 Codex 配置，保留 app-server 支持的透传字段。
def _build_codex(raw: Any) -> CodexConfig:
    mapping = raw if isinstance(raw, dict) else {}
    default = CodexConfig()
    return CodexConfig(
        command=str(mapping.get("command") or default.command),
        model=_optional_string(mapping.get("model")),
        approval_policy=mapping.get("approval_policy", default.approval_policy),
        thread_sandbox=str(mapping.get("thread_sandbox") or default.thread_sandbox),
        turn_sandbox_policy=dict(mapping.get("turn_sandbox_policy") or default.turn_sandbox_policy),
    )


# 函数说明：构建动态工具配置。
def _build_tools(raw: Any) -> ToolsConfig:
    mapping = raw if isinstance(raw, dict) else {}
    github_raw = mapping.get("github") if isinstance(mapping.get("github"), dict) else {}
    return ToolsConfig(
        github=GithubToolConfig(
            enabled=bool(github_raw.get("enabled", True)),
            mode=str(github_raw.get("mode") or "read_write"),
        )
    )


# 函数说明：构建 Codex turn 成功后的 Project 完成策略。
def _build_completion_policy(raw: Any) -> CompletionPolicyConfig:
    mapping = raw if isinstance(raw, dict) else {}
    return CompletionPolicyConfig(
        kind=str(mapping.get("kind") or "update_project_status"),
        success_state=str(mapping.get("success_state") or "Done").strip(),
        failure_state=_optional_string(mapping.get("failure_state", "Rework")),
        mark_done_after_successful_turn=bool(
            mapping.get("mark_done_after_successful_turn", True)
        ),
        close_issue=bool(mapping.get("close_issue", False)),
    )


# 函数说明：构建持久日志策略，并把日志级别规整为 Python logging 识别的大写形式。
def _build_logging(raw: Any) -> LoggingConfig:
    mapping = raw if isinstance(raw, dict) else {}
    level = str(mapping.get("level") or "DEBUG").upper()
    return LoggingConfig(
        level=level,
        retention_days=max(1, int(mapping.get("retention_days", 14))),
        max_file_mb=max(1, int(mapping.get("max_file_mb", 10))),
    )


# 函数说明：校验 tracker 的枚举和必要字段。
def _validate_tracker(tracker: TrackerConfig) -> None:
    if tracker.kind != "github_projects_v2":
        raise ValueError("tracker.kind 目前只支持 github_projects_v2")

    if tracker.owner_type not in {"org", "user"}:
        raise ValueError("tracker.owner_type 必须是 org 或 user")

    if not tracker.repositories:
        raise ValueError("tracker.repositories 至少需要一个仓库")

    for repository in tracker.repositories:
        if "/" not in repository:
            raise ValueError("tracker.repositories 必须使用 owner/repo 格式")

    if not tracker.active_states:
        raise ValueError("tracker.active_states 不能为空")

    if not tracker.terminal_states:
        raise ValueError("tracker.terminal_states 不能为空")


# 函数说明：校验完成策略，避免成功状态仍在 active states 中导致重复派发。
def _validate_completion_policy(
    policy: CompletionPolicyConfig,
    tracker: TrackerConfig,
) -> None:
    if policy.kind not in {"update_project_status", "none"}:
        raise ValueError("completion_policy.kind 目前只支持 update_project_status 或 none")

    if not policy.success_state:
        raise ValueError("completion_policy.success_state 不能为空")

    # 逻辑说明：自动完成的目标状态必须脱离 active states，否则成功 turn 后仍会被调度器再次派发。
    if policy.mark_done_after_successful_turn and policy.success_state in tracker.active_states:
        raise ValueError("completion_policy.success_state 不能同时出现在 tracker.active_states")

    # 逻辑说明：终态集合是调度器和用户理解“已完成”的共同边界，默认要求成功状态属于终态。
    if (
        policy.mark_done_after_successful_turn
        and policy.kind == "update_project_status"
        and policy.success_state not in tracker.terminal_states
    ):
        raise ValueError("completion_policy.success_state 必须包含在 tracker.terminal_states")


# 函数说明：校验日志级别，防止拼写错误导致日志静默。
def _validate_logging_level(level: str) -> None:
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("logging.level 必须是 DEBUG、INFO、WARNING、ERROR 或 CRITICAL")


# 函数说明：要求某个配置块是字典。
def _expect_mapping(value: Any, name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是对象")
    return value


# 函数说明：要求配置值存在。
def _expect_present(value: Any, name: str) -> Any:
    if value is None:
        raise ValueError(f"{name} 是必填项")
    return value


# 函数说明：要求配置值是字符串。
def _expect_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串")
    return _expand_string(value.strip())


# 函数说明：把可选值转换为字符串。
def _optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# 函数说明：解析字符串列表，支持 YAML 列表和逗号分隔字符串两种形式。
def _string_list(value: Any, name: str) -> List[str]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        raise ValueError(f"{name} 必须是字符串列表")

    result = [item for item in items if item]
    if not result:
        raise ValueError(f"{name} 不能为空")
    return result


# 函数说明：展开普通字符串中的 `~` 和 `$VAR`。
def _expand_string(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


# 函数说明：展开 token 配置；空环境变量视为未配置。
def _expand_optional_secret(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    expanded = _expand_string(text)

    # 逻辑说明：`$MISSING_ENV` 未展开时不能被当成真实 token 使用。
    if text.startswith("$") and expanded == text:
        return None
    return expanded or None
