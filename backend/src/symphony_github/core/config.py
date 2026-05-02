"""运行配置模型和归一化逻辑。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_STATUS_OPTIONS = [
    "Todo",
    "In Progress",
    "Rework",
    "Human Review",
    "Merging",
    "Done",
    "Closed",
    "Cancelled",
]
DEFAULT_ACTIVE_STATES = ["Todo", "In Progress", "Rework", "Merging"]
DEFAULT_HANDOFF_STATES = ["Human Review"]
DEFAULT_TERMINAL_STATES = ["Done", "Closed", "Cancelled"]
DEFAULT_FAILURE_STATE = "Rework"
HIGH_TRUST_APPROVAL_PRESETS = {"high_trust", "high-trust", "pr_full_auto", "pr-before-full-auto"}
DEFAULT_WORKSPACE_CHECKOUT_MODE = "clone"
DEFAULT_WORKSPACE_CHECKOUT_PROTOCOL = "ssh"
DEFAULT_WORKSPACE_CHECKOUT_DEPTH = 1


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
    status_options: List[str] = field(default_factory=lambda: list(DEFAULT_STATUS_OPTIONS))
    active_states: List[str] = field(default_factory=lambda: list(DEFAULT_ACTIVE_STATES))
    handoff_states: List[str] = field(default_factory=lambda: list(DEFAULT_HANDOFF_STATES))
    terminal_states: List[str] = field(default_factory=lambda: list(DEFAULT_TERMINAL_STATES))
    priority_field: Optional[str] = None
    api_base_url: str = "https://api.github.com"
    graphql_url: str = "https://api.github.com/graphql"


@dataclass
class BlockerPolicyConfig:
    """阻塞关系读取配置。"""

    kind: str = "github_issue_dependencies"
    unavailable_behavior: str = "treat_unblocked"
    blocked_states: List[str] = field(default_factory=lambda: ["Todo"])


@dataclass
class WorkspaceHooksConfig:
    """工作区 hook 配置。"""

    after_create: Optional[str] = None


@dataclass
class WorkspaceCheckoutRepositoryConfig:
    """单个仓库的 checkout 覆盖配置。"""

    clone_url: Optional[str] = None
    branch: Optional[str] = None
    path: str = "."


@dataclass
class WorkspaceCheckoutConfig:
    """内置工作区 checkout 配置。"""

    mode: str = DEFAULT_WORKSPACE_CHECKOUT_MODE
    protocol: str = DEFAULT_WORKSPACE_CHECKOUT_PROTOCOL
    depth: Optional[int] = DEFAULT_WORKSPACE_CHECKOUT_DEPTH
    repositories: Dict[str, WorkspaceCheckoutRepositoryConfig] = field(default_factory=dict)


@dataclass
class WorkspaceConfig:
    """本地工作区配置。"""

    root: str
    checkout: WorkspaceCheckoutConfig = field(default_factory=WorkspaceCheckoutConfig)
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

    kind: str = "agent_managed"
    success_state: str = "Human Review"
    failure_state: Optional[str] = DEFAULT_FAILURE_STATE
    mark_done_after_successful_turn: bool = False
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
        status_options=_optional_string_list(
            tracker_raw.get("status_options", DEFAULT_STATUS_OPTIONS),
            "tracker.status_options",
        ),
        active_states=_string_list(
            tracker_raw.get("active_states", DEFAULT_ACTIVE_STATES),
            "tracker.active_states",
        ),
        handoff_states=_optional_string_list(
            tracker_raw.get("handoff_states", DEFAULT_HANDOFF_STATES),
            "tracker.handoff_states",
        ),
        terminal_states=_string_list(
            tracker_raw.get("terminal_states", DEFAULT_TERMINAL_STATES),
            "tracker.terminal_states",
        ),
        priority_field=_optional_string(tracker_raw.get("priority_field")),
        api_base_url=str(tracker_raw.get("api_base_url") or "https://api.github.com"),
        graphql_url=str(tracker_raw.get("graphql_url") or "https://api.github.com/graphql"),
    )
    _validate_tracker(tracker)

    blocker_policy = _build_blocker_policy(raw.get("blocker_policy"), tracker)
    workspace = _build_workspace(workspace_raw, workflow_path)
    _validate_workspace_checkout_repositories(workspace.checkout, tracker)
    agent = _build_agent(raw.get("agent"))
    codex = _build_codex(raw.get("codex"))
    tools = _build_tools(raw.get("tools"))
    completion_policy = _build_completion_policy(raw.get("completion_policy"), tracker)
    _validate_status_policy(tracker, blocker_policy, completion_policy)
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
def _build_blocker_policy(raw: Any, tracker: TrackerConfig) -> BlockerPolicyConfig:
    mapping = raw if isinstance(raw, dict) else {}
    blocked_states_raw = mapping.get("blocked_states")
    return BlockerPolicyConfig(
        kind=str(mapping.get("kind") or "github_issue_dependencies"),
        unavailable_behavior=str(mapping.get("unavailable_behavior") or "treat_unblocked"),
        blocked_states=(
            _optional_string_list(blocked_states_raw, "blocker_policy.blocked_states")
            if blocked_states_raw is not None
            else _default_blocked_states(tracker)
        ),
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
    checkout = _build_workspace_checkout(raw.get("checkout"), hooks.after_create is not None)
    return WorkspaceConfig(
        root=str(root_path),
        checkout=checkout,
        hooks=hooks,
        cleanup_terminal_workspaces=bool(raw.get("cleanup_terminal_workspaces", False)),
    )


# 函数说明：构建内置 checkout 配置；旧 hook-only WORKFLOW 缺少 checkout 时保持 hook 语义。
def _build_workspace_checkout(raw: Any, has_after_create_hook: bool) -> WorkspaceCheckoutConfig:
    mapping = raw if isinstance(raw, dict) else {}
    default_mode = (
        "hook"
        if raw is None and has_after_create_hook
        else DEFAULT_WORKSPACE_CHECKOUT_MODE
    )
    depth = (
        _optional_positive_int(mapping.get("depth"), "workspace.checkout.depth")
        if "depth" in mapping
        else DEFAULT_WORKSPACE_CHECKOUT_DEPTH
    )
    checkout = WorkspaceCheckoutConfig(
        mode=str(mapping.get("mode") or default_mode),
        protocol=str(mapping.get("protocol") or DEFAULT_WORKSPACE_CHECKOUT_PROTOCOL),
        depth=depth,
        repositories=_workspace_checkout_repositories(
            mapping.get("repositories", mapping.get("overrides"))
        ),
    )
    _validate_workspace_checkout(checkout)
    return checkout


# 函数说明：解析 checkout 仓库覆盖配置，兼容 YAML 映射和 App settings 列表两种形状。
def _workspace_checkout_repositories(
    value: Any,
) -> Dict[str, WorkspaceCheckoutRepositoryConfig]:
    if value is None:
        return {}

    items: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        items = [(str(repository), override) for repository, override in value.items()]
    elif isinstance(value, list):
        for index, entry in enumerate(value):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"workspace.checkout.repositories[{index}] 必须是对象"
                )
            repository = entry.get("repository") or entry.get("name")
            if not repository:
                raise ValueError(
                    f"workspace.checkout.repositories[{index}].repository 是必填项"
                )
            items.append((str(repository), entry))
    else:
        raise ValueError("workspace.checkout.repositories 必须是对象或对象列表")

    repositories: Dict[str, WorkspaceCheckoutRepositoryConfig] = {}
    for repository, override in items:
        normalized_repository = repository.strip()
        _validate_repository_name(normalized_repository, "workspace.checkout.repositories")
        if normalized_repository in repositories:
            raise ValueError(
                f"workspace.checkout.repositories 不能重复配置：{normalized_repository}"
            )

        # 逻辑说明：空对象表示仅覆盖 path 默认值，其他字段仍按全局 checkout 生成。
        override_raw = override if isinstance(override, dict) else {}
        repositories[normalized_repository] = WorkspaceCheckoutRepositoryConfig(
            clone_url=_optional_string(override_raw.get("clone_url")),
            branch=_optional_string(override_raw.get("branch")),
            path=_optional_string(override_raw.get("path")) or ".",
        )
    return repositories


# 函数说明：校验 checkout 基础枚举和覆盖配置中的路径占位。
def _validate_workspace_checkout(checkout: WorkspaceCheckoutConfig) -> None:
    if checkout.mode not in {"clone", "hook", "none"}:
        raise ValueError("workspace.checkout.mode 必须是 clone、hook 或 none")

    if checkout.protocol not in {"ssh", "https"}:
        raise ValueError("workspace.checkout.protocol 必须是 ssh 或 https")

    for repository, override in checkout.repositories.items():
        if not override.path.strip():
            raise ValueError(f"workspace.checkout.repositories.{repository}.path 不能为空")


# 函数说明：校验 checkout 覆盖只面向 tracker allowlist 中的仓库，避免配置静默失效。
def _validate_workspace_checkout_repositories(
    checkout: WorkspaceCheckoutConfig,
    tracker: TrackerConfig,
) -> None:
    allowed = set(tracker.repositories)
    unknown = sorted(
        repository for repository in checkout.repositories if repository not in allowed
    )
    if unknown:
        raise ValueError(
            "workspace.checkout.repositories 只能配置 tracker.repositories 中的仓库："
            f"{', '.join(unknown)}"
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
        approval_policy=_normalize_approval_policy(
            mapping.get("approval_policy", default.approval_policy),
            default.approval_policy,
        ),
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
def _build_completion_policy(raw: Any, tracker: TrackerConfig) -> CompletionPolicyConfig:
    mapping = raw if isinstance(raw, dict) else {}
    success_state = (
        _optional_string(mapping.get("success_state")) or _default_success_state(tracker)
    )
    failure_state = (
        _optional_string(mapping.get("failure_state"))
        if "failure_state" in mapping
        else _default_failure_state(tracker)
    )
    return CompletionPolicyConfig(
        kind=str(mapping.get("kind") or "agent_managed"),
        success_state=success_state,
        failure_state=failure_state,
        mark_done_after_successful_turn=bool(
            mapping.get("mark_done_after_successful_turn", False)
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

    _validate_unique_strings(tracker.repositories, "tracker.repositories")
    for repository in tracker.repositories:
        _validate_repository_name(repository, "tracker.repositories")

    if not tracker.active_states:
        raise ValueError("tracker.active_states 不能为空")

    if not tracker.terminal_states:
        raise ValueError("tracker.terminal_states 不能为空")

    _validate_unique_strings(tracker.status_options, "tracker.status_options")
    _validate_unique_strings(tracker.active_states, "tracker.active_states")
    _validate_unique_strings(tracker.handoff_states, "tracker.handoff_states")
    _validate_unique_strings(tracker.terminal_states, "tracker.terminal_states")


# 函数说明：统一校验 GitHub Project Status 的角色分组和依赖完成策略。
def _validate_status_policy(
    tracker: TrackerConfig,
    blocker: BlockerPolicyConfig,
    completion: CompletionPolicyConfig,
) -> None:
    # 逻辑说明：active、handoff、terminal 是互斥角色；同一状态跨角色会让调度器
    # 无法判断它到底应该继续派发、等待人工，还是执行终态清理。
    _raise_if_overlap(
        tracker.active_states,
        tracker.terminal_states,
        "tracker.active_states",
        "tracker.terminal_states",
    )
    _raise_if_overlap(
        tracker.active_states,
        tracker.handoff_states,
        "tracker.active_states",
        "tracker.handoff_states",
    )
    _raise_if_overlap(
        tracker.handoff_states,
        tracker.terminal_states,
        "tracker.handoff_states",
        "tracker.terminal_states",
    )
    _validate_unique_strings(blocker.blocked_states, "blocker_policy.blocked_states")
    _validate_completion_policy(completion, tracker)

    # 逻辑说明：status_options 来自 GitHub discovery，是 UI/校验/prompt 的缓存。
    # 一旦已知，就主动拦截拼写错误；为空时保持旧 WORKFLOW.md 的自由配置兼容。
    if not tracker.status_options:
        return

    known = set(tracker.status_options)
    _validate_states_are_known(tracker.active_states, known, "tracker.active_states")
    _validate_states_are_known(tracker.handoff_states, known, "tracker.handoff_states")
    _validate_states_are_known(tracker.terminal_states, known, "tracker.terminal_states")
    _validate_states_are_known(blocker.blocked_states, known, "blocker_policy.blocked_states")
    _validate_states_are_known([completion.success_state], known, "completion_policy.success_state")
    if completion.failure_state:
        _validate_states_are_known(
            [completion.failure_state],
            known,
            "completion_policy.failure_state",
        )


# 函数说明：校验完成策略，避免应用托管完成时目标状态仍在 active states 中导致重复派发。
def _validate_completion_policy(
    policy: CompletionPolicyConfig,
    tracker: TrackerConfig,
) -> None:
    if policy.kind not in {"update_project_status", "agent_managed", "none"}:
        raise ValueError(
            "completion_policy.kind 目前只支持 update_project_status、agent_managed 或 none"
        )

    if not policy.success_state:
        raise ValueError("completion_policy.success_state 不能为空")

    # 逻辑说明：只有应用负责更新 Project Status 时，目标状态才必须脱离 active states。
    # agent_managed/none 允许用户在 prompt 中自行设计 continuation 行为。
    if (
        policy.kind == "update_project_status"
        and policy.mark_done_after_successful_turn
        and policy.success_state in tracker.active_states
    ):
        raise ValueError("completion_policy.success_state 不能同时出现在 tracker.active_states")


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


# 函数说明：解析可选正整数；空值表示禁用该数值型 checkout 参数。
def _optional_positive_int(value: Any, name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{name} 必须是正整数")
    return parsed


# 函数说明：解析可选字符串列表；字段缺失或为空时返回空列表，用于兼容旧配置。
def _optional_string_list(value: Any, name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str) and not value.strip():
        return []
    if isinstance(value, list) and not value:
        return []
    return _string_list(value, name)


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


# 函数说明：计算旧配置缺少 blocked_states 时的兼容默认值。
def _default_blocked_states(tracker: TrackerConfig) -> List[str]:
    # 逻辑说明：历史版本只阻塞 Todo；如果 discovery 显示 Project 没有 Todo，
    # 则退到第一个 active state，覆盖 Ready/Backlog 等自定义排队阶段。
    if not tracker.status_options or "Todo" in tracker.status_options:
        return ["Todo"]
    return tracker.active_states[:1]


# 函数说明：根据已发现阶段推断默认成功目标，避免固定依赖 Done。
def _default_success_state(tracker: TrackerConfig) -> str:
    if not tracker.status_options:
        return "Human Review"
    if "Human Review" in tracker.status_options and "Human Review" not in tracker.active_states:
        return "Human Review"
    if "Done" in tracker.status_options and "Done" not in tracker.active_states:
        return "Done"
    if tracker.handoff_states:
        return tracker.handoff_states[0]
    if tracker.terminal_states:
        return tracker.terminal_states[0]

    # 逻辑说明：极端情况下 terminal 为空会在 tracker 校验中失败；这里仍保留兜底，
    # 让错误信息尽量来自统一校验而不是索引异常。
    non_active = [state for state in tracker.status_options if state not in tracker.active_states]
    return non_active[-1] if non_active else tracker.status_options[-1]


# 函数说明：根据已发现阶段推断默认失败/返工目标，找不到 Rework 时保持未配置。
def _default_failure_state(tracker: TrackerConfig) -> Optional[str]:
    if not tracker.status_options:
        return DEFAULT_FAILURE_STATE
    return DEFAULT_FAILURE_STATE if DEFAULT_FAILURE_STATE in tracker.status_options else None


# 函数说明：把显式 high-trust preset 归一化为 Codex app-server 已支持的 never。
def _normalize_approval_policy(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if _approval_policy_is_high_trust_preset(value):
        return "never"
    return value


# 函数说明：识别用户明确写入的高信任 approval preset，不从普通 granular 配置中推断。
def _approval_policy_is_high_trust_preset(value: Any) -> bool:
    if isinstance(value, str):
        return _normalize_preset_name(value) in HIGH_TRUST_APPROVAL_PRESETS
    if isinstance(value, dict):
        for key in ("preset", "autonomy_preset", "mode"):
            candidate = value.get(key)
            if (
                isinstance(candidate, str)
                and _normalize_preset_name(candidate) in HIGH_TRUST_APPROVAL_PRESETS
            ):
                return True
    return False


# 函数说明：归一化 preset 名称，兼容用户输入中的空格和大小写差异。
def _normalize_preset_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


# 函数说明：校验字符串列表内没有重复值。
def _validate_unique_strings(values: List[str], name: str) -> None:
    seen: set[str] = set()
    duplicates: List[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"{name} 不能包含重复值：{', '.join(duplicates)}")


# 函数说明：校验 GitHub 仓库名使用 owner/repo 格式。
def _validate_repository_name(repository: str, name: str) -> None:
    parts = repository.split("/")
    has_invalid_shape = len(parts) != 2 or not parts[0] or not parts[1]
    has_whitespace = any(character.isspace() for character in repository)
    if has_invalid_shape or has_whitespace:
        raise ValueError(f"{name} 必须使用 owner/repo 格式")


# 函数说明：校验两个状态角色集合没有交集。
def _raise_if_overlap(left: List[str], right: List[str], left_name: str, right_name: str) -> None:
    overlap = sorted(set(left) & set(right))
    if overlap:
        raise ValueError(f"{left_name} 与 {right_name} 不能重叠：{', '.join(overlap)}")


# 函数说明：在已发现 GitHub Status options 时，校验配置状态名都来自 Project。
def _validate_states_are_known(values: List[str], known: set[str], name: str) -> None:
    unknown = [value for value in values if value not in known]
    if unknown:
        raise ValueError(f"{name} 包含 Project Status 中不存在的状态：{', '.join(unknown)}")


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
