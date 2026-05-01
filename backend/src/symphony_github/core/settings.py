"""App 内设置模型、校验和 WORKFLOW.md 导入导出。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .config import SymphonyConfig, build_config
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
        "status_options": tracker_raw.get("status_options", []),
        "active_states": tracker_raw.get("active_states", ["Todo", "In Progress", "Rework"]),
        "handoff_states": tracker_raw.get("handoff_states", []),
        "terminal_states": tracker_raw.get("terminal_states", ["Done", "Closed", "Cancelled"]),
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
            "kind": completion_raw.get("kind", "update_project_status"),
            "success_state": completion_raw.get("success_state", "Done"),
            "failure_state": _optional_value(completion_raw.get("failure_state", "Rework")),
            "mark_done_after_successful_turn": bool(
                completion_raw.get("mark_done_after_successful_turn", True)
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
            "status_options": ["Todo", "In Progress", "Rework", "Done", "Closed", "Cancelled"],
            "active_states": ["Todo", "In Progress", "Rework"],
            "handoff_states": [],
            "terminal_states": ["Done", "Closed", "Cancelled"],
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
            "hooks": {"after_create": "git clone git@github.com:your-org/your-repo.git ."},
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
            "kind": "update_project_status",
            "success_state": "Done",
            "failure_state": "Rework",
            "mark_done_after_successful_turn": True,
            "close_issue": False,
        },
        "logging": {
            "level": "DEBUG",
            "retention_days": 14,
            "max_file_mb": 10,
        },
        "prompt_template": (
            "你正在处理 GitHub 任务：\n\n"
            "- 标识：`{{ issue.identifier }}`\n"
            "- 标题：`{{ issue.title }}`\n"
            "- 仓库：`{{ issue.repository }}`\n"
            "- 链接：`{{ issue.url }}`\n\n"
            "{{ workflow.status_policy_markdown }}\n\n"
            "请先阅读 issue/PR 描述和仓库代码，再实施最小必要修改。"
            "完成后请根据上面的阶段策略交接任务，并在 GitHub 中留下清晰的工作说明、"
            "验证结果和剩余风险。"
        ),
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
