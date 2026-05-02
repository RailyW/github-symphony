"""App 内设置模型、校验和 WORKFLOW.md 导入导出。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import (
    DEFAULT_ACTIVE_STATES,
    DEFAULT_FAILURE_STATE,
    DEFAULT_HANDOFF_STATES,
    DEFAULT_STATUS_OPTIONS,
    DEFAULT_TERMINAL_STATES,
    DEFAULT_WORKSPACE_CHECKOUT_DEPTH,
    DEFAULT_WORKSPACE_CHECKOUT_MODE,
    DEFAULT_WORKSPACE_CHECKOUT_PROTOCOL,
    SymphonyConfig,
    build_config,
)
from .models import dataclass_to_dict
from .workflow import split_front_matter


@dataclass
class AppSettingsDocument:
    """归一化后的 App 设置文档。"""

    settings: Dict[str, Any]
    config: SymphonyConfig
    prompt_template: str


@dataclass
class WorkflowImportResult:
    """WORKFLOW.md 导入结果。"""

    settings: Dict[str, Any]
    token_hint: Optional[str]
    warnings: List[str]


DEFAULT_PROMPT_TEMPLATE = """你正在处理 GitHub 任务：

- 标识：`{{ issue.identifier }}`
- 标题：`{{ issue.title }}`
- 仓库：`{{ issue.repository }}`
- 链接：`{{ issue.url }}`

{{ workflow.status_policy_markdown }}

## 默认自治边界：PR 前全自动

你在隔离工作区内执行完整实现循环。调度器只负责派发任务、准备工作区、注入 GitHub 工具和记录事件；代码流转动作由你根据本 prompt、token 权限、GitHub tools 模式和 Project Status 执行。

### 通用规则

1. 先读取 issue/PR 描述、现有评论、关联 PR 和仓库代码，再开始修改。
2. 使用单个 issue comment 作为 `## Codex Workpad`。如果已存在 Workpad，就更新它；不要新建多个进度评论。
3. Workpad 至少记录：当前计划、实现摘要、验证命令与结果、PR 链接、未处理风险或阻塞。
4. 除非遇到缺失权限、缺失 secret、仓库无法访问等真实外部阻塞，否则不要在 active 状态下结束 turn。
5. 失败或需要返工时，把 Project Status 移到 `{{ workflow.failure_state }}`，并在 Workpad 写清楚原因和下一步。

### 状态流转

- `Todo`：先使用 GitHub 工具把 Project Status 移到 `In Progress`，然后创建或更新 `## Codex Workpad`，再开始复现、计划和实现。
- `In Progress` / `Rework`：完成复现、计划、实现和验证。创建或复用任务分支，保持分支基于最新默认分支；按逻辑提交 commit，push 到远端，并创建或更新一个 PR。
- PR 前置门禁：验收项完成；必要验证已运行并记录；最新 pushed commit 的 checks 为 green；PR 已链接到当前 issue；PR feedback sweep 没有未处理的 actionable comments；Workpad 已记录验证结果、PR 链接和剩余风险。
- `Human Review`：这是非 active 交接状态。不要继续改代码，不要自行 merge；等待人工审批或把状态移到 `Rework` / `Merging`。
- `Merging`：这是 active land 状态。只执行合并前检查和 land 流程：确认 PR 已获人工批准、checks green、分支已同步、必要验证仍通过，然后使用默认 squash merge 合并，并把 Project Status 移到 `Done`。

### PR feedback sweep

在进入 `Human Review` 前必须检查并处理：

- PR 顶层评论、review summary、inline comments、requested changes。
- CI/checks/Actions 的最新状态和失败日志。
- 新反馈处理后必须重新验证、commit、push，并再次确认 checks green。
- 对非 actionable 或不同意的反馈，要在 PR 或 Workpad 中给出简短理由。

### 禁止事项

- 不要 force push。
- 不要直接修改或 push 到 `main` / 默认分支。
- 不要删除远端分支。
- 不要使用 PR body closing keywords 自动关闭 issue，也不要自动关闭 issue；任务结束以 GitHub Project Status `Done` 为准。
- 不要扩大 scope；发现有价值但超出本 issue 的工作时，在 Workpad 记录为 follow-up。"""


# 函数说明：把前端传入的 App settings 转成后端运行配置，并返回归一化 settings。
def normalize_app_settings(
    raw_settings: Dict[str, Any],
    github_token: Optional[str] = None,
    workflow_path: Optional[str] = "App Settings",
) -> AppSettingsDocument:
    # 逻辑说明：先转回现有 front matter 形状，再复用 build_config 的统一校验逻辑。
    raw_config = settings_to_raw_config(raw_settings, github_token=github_token)
    prompt_template = _expect_prompt(raw_settings.get("prompt_template"))
    config = build_config(raw_config, workflow_path=workflow_path)
    return AppSettingsDocument(
        settings=settings_from_config(config, prompt_template),
        config=config,
        prompt_template=prompt_template,
    )


# 函数说明：把 App settings 转成 WORKFLOW front matter 字典；token 永远由调用者显式传入。
def settings_to_raw_config(
    raw_settings: Dict[str, Any],
    github_token: Optional[str] = None,
    token_placeholder: Optional[str] = None,
) -> Dict[str, Any]:
    tracker_raw = _mapping(raw_settings.get("tracker"))
    blocker_raw = _mapping(raw_settings.get("blocker_policy"))
    workspace_raw = _mapping(raw_settings.get("workspace"))
    hooks_raw = _mapping(workspace_raw.get("hooks"))
    agent_raw = _mapping(raw_settings.get("agent"))
    codex_raw = _mapping(raw_settings.get("codex"))
    tools_raw = _mapping(raw_settings.get("tools"))
    github_tools_raw = _mapping(tools_raw.get("github"))
    completion_raw = _mapping(raw_settings.get("completion_policy"))
    logging_raw = _mapping(raw_settings.get("logging"))

    tracker: Dict[str, Any] = {
        "kind": "github_projects_v2",
        "owner_type": tracker_raw.get("owner_type", "org"),
        "owner": tracker_raw.get("owner"),
        "project_number": tracker_raw.get("project_number"),
        "repositories": tracker_raw.get("repositories", []),
        "status_field": tracker_raw.get("status_field", "Status"),
        "status_options": tracker_raw.get("status_options", DEFAULT_STATUS_OPTIONS),
        "active_states": tracker_raw.get("active_states", DEFAULT_ACTIVE_STATES),
        "handoff_states": tracker_raw.get("handoff_states", DEFAULT_HANDOFF_STATES),
        "terminal_states": tracker_raw.get("terminal_states", DEFAULT_TERMINAL_STATES),
        "priority_field": _optional_value(tracker_raw.get("priority_field")),
        "api_base_url": tracker_raw.get("api_base_url", "https://api.github.com"),
        "graphql_url": tracker_raw.get("graphql_url", "https://api.github.com/graphql"),
    }

    # 逻辑说明：运行时可注入真实 token；导出 WORKFLOW 时只写占位符，避免泄露明文。
    if github_token:
        tracker["api_token"] = github_token
    elif token_placeholder:
        tracker["api_token"] = token_placeholder

    blocker_policy: Dict[str, Any] = {
        "kind": blocker_raw.get("kind", "github_issue_dependencies"),
        "unavailable_behavior": blocker_raw.get("unavailable_behavior", "treat_unblocked"),
    }
    # 逻辑说明：旧版 App settings 没有 blocked_states。这里不强行写入 Todo，
    # 让 config 层可根据已发现 status_options 自动退到第一个 active state。
    if "blocked_states" in blocker_raw:
        blocker_policy["blocked_states"] = blocker_raw.get("blocked_states", [])

    return {
        "tracker": tracker,
        "blocker_policy": blocker_policy,
        "workspace": {
            "root": workspace_raw.get("root"),
            "cleanup_terminal_workspaces": bool(
                workspace_raw.get("cleanup_terminal_workspaces", False)
            ),
            "checkout": _checkout_settings_to_raw(workspace_raw, hooks_raw),
            "hooks": {"after_create": _optional_value(hooks_raw.get("after_create"))},
        },
        "agent": {
            "max_concurrent_agents": agent_raw.get("max_concurrent_agents", 3),
            "max_turns": agent_raw.get("max_turns", 20),
            "poll_interval_ms": agent_raw.get("poll_interval_ms", 10000),
            "max_retry_backoff_ms": agent_raw.get("max_retry_backoff_ms", 300000),
        },
        "codex": {
            "command": codex_raw.get("command", "codex app-server"),
            "model": _optional_value(codex_raw.get("model")),
            "approval_policy": codex_raw.get("approval_policy", _default_approval_policy()),
            "thread_sandbox": codex_raw.get("thread_sandbox", "workspace-write"),
            "turn_sandbox_policy": codex_raw.get(
                "turn_sandbox_policy",
                {"type": "workspaceWrite", "networkAccess": True},
            ),
        },
        "tools": {
            "github": {
                "enabled": bool(github_tools_raw.get("enabled", True)),
                "mode": github_tools_raw.get("mode", "read_write"),
            }
        },
        "completion_policy": {
            "kind": completion_raw.get("kind", "agent_managed"),
            "success_state": completion_raw.get("success_state", "Human Review"),
            "failure_state": _optional_value(
                completion_raw.get("failure_state", DEFAULT_FAILURE_STATE)
            ),
            "mark_done_after_successful_turn": bool(
                completion_raw.get("mark_done_after_successful_turn", False)
            ),
            "close_issue": bool(completion_raw.get("close_issue", False)),
        },
        "logging": {
            "level": logging_raw.get("level", "DEBUG"),
            "retention_days": logging_raw.get("retention_days", 14),
            "max_file_mb": logging_raw.get("max_file_mb", 10),
        },
    }


# 函数说明：把运行配置转回前端使用的 App settings 形状。
def settings_from_config(config: SymphonyConfig, prompt_template: str) -> Dict[str, Any]:
    tracker = config.tracker
    workspace = config.workspace
    return {
        "tracker": {
            "owner_type": tracker.owner_type,
            "owner": tracker.owner,
            "project_number": tracker.project_number,
            "repositories": list(tracker.repositories),
            "status_field": tracker.status_field,
            "status_options": list(tracker.status_options),
            "active_states": list(tracker.active_states),
            "handoff_states": list(tracker.handoff_states),
            "terminal_states": list(tracker.terminal_states),
            "priority_field": tracker.priority_field,
            "api_base_url": tracker.api_base_url,
            "graphql_url": tracker.graphql_url,
        },
        "blocker_policy": dataclass_to_dict(config.blocker_policy),
        "workspace": {
            "root": workspace.root,
            "cleanup_terminal_workspaces": workspace.cleanup_terminal_workspaces,
            "checkout": dataclass_to_dict(workspace.checkout),
            "hooks": dataclass_to_dict(workspace.hooks),
        },
        "agent": dataclass_to_dict(config.agent),
        "codex": dataclass_to_dict(config.codex),
        "tools": dataclass_to_dict(config.tools),
        "completion_policy": dataclass_to_dict(config.completion_policy),
        "logging": dataclass_to_dict(config.logging),
        "prompt_template": prompt_template,
    }


# 函数说明：把 WORKFLOW.md 文本导入为 App settings。
def import_workflow_text(text: str, workflow_path: Optional[str] = None) -> WorkflowImportResult:
    raw_config, prompt_template = split_front_matter(text)
    config = build_config(raw_config, workflow_path=workflow_path)
    token_hint = _token_hint_from_raw(raw_config)
    warnings: List[str] = []

    # 逻辑说明：导入时不迁移真实 token；用户需要在 Settings 页面单独保存 token。
    if token_hint and not token_hint.startswith("$"):
        warnings.append("已导入配置字段，但出于安全考虑没有保存 WORKFLOW.md 中的明文 token。")

    return WorkflowImportResult(
        settings=settings_from_config(config, prompt_template),
        token_hint=token_hint,
        warnings=warnings,
    )


# 函数说明：把 App settings 导出为可读的 WORKFLOW.md 文本。
def export_workflow_text(raw_settings: Dict[str, Any]) -> str:
    document = normalize_app_settings(raw_settings, github_token=None, workflow_path=None)
    raw_config = settings_to_raw_config(
        document.settings,
        github_token=None,
        token_placeholder="$GITHUB_TOKEN",
    )
    yaml_text = _dump_yaml(raw_config)
    return f"---\n{yaml_text}---\n\n{document.prompt_template.rstrip()}\n"


# 函数说明：返回前端首次启动可使用的默认 App settings。
def default_app_settings() -> Dict[str, Any]:
    return {
        "tracker": {
            "owner_type": "org",
            "owner": "your-org",
            "project_number": 12,
            "repositories": ["your-org/your-repo"],
            "status_field": "Status",
            "status_options": list(DEFAULT_STATUS_OPTIONS),
            "active_states": list(DEFAULT_ACTIVE_STATES),
            "handoff_states": list(DEFAULT_HANDOFF_STATES),
            "terminal_states": list(DEFAULT_TERMINAL_STATES),
            "priority_field": "Priority",
            "api_base_url": "https://api.github.com",
            "graphql_url": "https://api.github.com/graphql",
        },
        "blocker_policy": {
            "kind": "github_issue_dependencies",
            "unavailable_behavior": "treat_unblocked",
            "blocked_states": ["Todo"],
        },
        "workspace": {
            "root": "~/code/github-symphony-workspaces",
            "cleanup_terminal_workspaces": False,
            "checkout": {
                "mode": DEFAULT_WORKSPACE_CHECKOUT_MODE,
                "protocol": DEFAULT_WORKSPACE_CHECKOUT_PROTOCOL,
                "depth": DEFAULT_WORKSPACE_CHECKOUT_DEPTH,
                "repositories": {},
            },
            "hooks": {"after_create": None},
        },
        "agent": {
            "max_concurrent_agents": 3,
            "max_turns": 20,
            "poll_interval_ms": 10000,
            "max_retry_backoff_ms": 300000,
        },
        "codex": {
            "command": "codex app-server",
            "model": "gpt-5.5",
            "approval_policy": _default_approval_policy(),
            "thread_sandbox": "workspace-write",
            "turn_sandbox_policy": {"type": "workspaceWrite", "networkAccess": True},
        },
        "tools": {"github": {"enabled": True, "mode": "read_write"}},
        "completion_policy": {
            "kind": "agent_managed",
            "success_state": "Human Review",
            "failure_state": DEFAULT_FAILURE_STATE,
            "mark_done_after_successful_turn": False,
            "close_issue": False,
        },
        "logging": {
            "level": "DEBUG",
            "retention_days": 14,
            "max_file_mb": 10,
        },
        "prompt_template": DEFAULT_PROMPT_TEMPLATE,
    }


# 函数说明：把 App settings 中的 workspace.checkout 规整成 WORKFLOW front matter 形状。
def _checkout_settings_to_raw(
    workspace_raw: Dict[str, Any],
    hooks_raw: Dict[str, Any],
) -> Dict[str, Any]:
    checkout_raw = _mapping(workspace_raw.get("checkout"))
    has_explicit_checkout = isinstance(workspace_raw.get("checkout"), dict)
    has_legacy_hook = bool(_optional_value(hooks_raw.get("after_create")))

    # 逻辑说明：旧 WORKFLOW/App settings 只有 after_create hook 时保持 hook-only 语义；
    # 没有旧 hook 的新配置默认使用当前 work item repository 做内置 clone。
    default_mode = (
        "hook"
        if not has_explicit_checkout and has_legacy_hook
        else DEFAULT_WORKSPACE_CHECKOUT_MODE
    )
    return {
        "mode": checkout_raw.get("mode", default_mode),
        "protocol": checkout_raw.get("protocol", DEFAULT_WORKSPACE_CHECKOUT_PROTOCOL),
        "depth": (
            checkout_raw.get("depth")
            if "depth" in checkout_raw
            else DEFAULT_WORKSPACE_CHECKOUT_DEPTH
        ),
        "repositories": _checkout_repository_settings_to_raw(
            checkout_raw.get("repositories", checkout_raw.get("overrides"))
        ),
    }


# 函数说明：归一化 checkout repository overrides，导出时使用 repository 名称到覆盖对象的映射。
def _checkout_repository_settings_to_raw(value: Any) -> Dict[str, Dict[str, Any]]:
    if value is None:
        return {}

    if isinstance(value, list):
        result: Dict[str, Dict[str, Any]] = {}
        for entry in value:
            if not isinstance(entry, dict):
                continue
            repository = _optional_value(entry.get("repository") or entry.get("name"))
            if not repository:
                continue
            result[str(repository)] = _checkout_repository_override_to_raw(entry)
        return result

    if isinstance(value, dict):
        return {
            str(repository): _checkout_repository_override_to_raw(_mapping(override))
            for repository, override in value.items()
        }

    return {}


# 函数说明：归一化单个 checkout repository override，空字符串转为 None 或默认 path。
def _checkout_repository_override_to_raw(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "clone_url": _optional_value(value.get("clone_url")),
        "branch": _optional_value(value.get("branch")),
        "path": _optional_value(value.get("path")) or ".",
    }


# 函数说明：把任意值安全转换为字典。
def _mapping(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


# 函数说明：把空字符串规整为 None，便于 YAML 导出更清晰。
def _optional_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


# 函数说明：校验并读取 prompt 模板文本。
def _expect_prompt(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("prompt_template 必须是非空字符串")
    return value


# 函数说明：返回 Codex 默认 granular approval policy。
def _default_approval_policy() -> Dict[str, Any]:
    return {
        "granular": {
            "sandbox_approval": True,
            "rules": True,
            "mcp_elicitations": True,
        }
    }


# 函数说明：读取导入文件里的 token 线索；不会把它保存为 App secret。
def _token_hint_from_raw(raw_config: Dict[str, Any]) -> Optional[str]:
    tracker = _mapping(raw_config.get("tracker"))
    token = tracker.get("api_token")
    if token is None:
        return None
    return str(token)


# 函数说明：使用 PyYAML 输出稳定、可读、支持中文的 YAML。
def _dump_yaml(value: Dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # noqa: BLE001 - 打包环境缺依赖时需要清晰失败。
        raise RuntimeError("导出 WORKFLOW.md 需要 PyYAML 依赖") from exc

    return yaml.safe_dump(
        value,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
