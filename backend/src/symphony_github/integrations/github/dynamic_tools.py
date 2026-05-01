"""Codex app-server GitHub 动态工具。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from symphony_github.core.config import BlockerPolicyConfig, GithubToolConfig, TrackerConfig

from .client import GitHubClient, GitHubClientError
from .tracker import GitHubProjectsV2Tracker

READ_METHODS = {"GET", "HEAD"}
WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
ProjectStatusUpdater = Callable[[str, str], Awaitable[Dict[str, Any]]]


@dataclass
class DynamicToolResult:
    """动态工具执行结果。"""

    success: bool
    content_items: List[Dict[str, str]]

    # 函数说明：转换成 Codex app-server 期望的 DynamicToolCallResponse 形状。
    def to_rpc_result(self) -> Dict[str, Any]:
        return {"success": self.success, "contentItems": self.content_items}


class GitHubDynamicTools:
    """执行 GitHub 相关动态工具。"""

    # 函数说明：保存 client、tracker 配置、工具权限模式和可复用的 Project Status updater。
    def __init__(
        self,
        client: GitHubClient,
        tracker_config: TrackerConfig,
        tool_config: GithubToolConfig,
        project_status_updater: Optional[ProjectStatusUpdater] = None,
    ) -> None:
        self.client = client
        self.tracker_config = tracker_config
        self.tool_config = tool_config
        self.project_status_updater = project_status_updater

    # 函数说明：返回 Codex app-server `dynamicTools` 注册列表。
    def tool_specs(self) -> List[Dict[str, Any]]:
        if not self.tool_config.enabled:
            return []

        return [
            {
                "name": "github_graphql",
                "description": (
                    "Execute one GitHub GraphQL query or mutation using the configured "
                    "Symphony GitHub token."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "variables": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "github_rest",
                "description": (
                    "Call an allowlisted GitHub REST API relative path using the configured "
                    "Symphony GitHub token."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["method", "path"],
                    "properties": {
                        "method": {"type": "string"},
                        "path": {"type": "string"},
                        "query": {"type": "object"},
                        "body": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "github_update_project_status",
                "description": (
                    "Update the configured GitHub Project v2 Status field for one project item."
                ),
                "inputSchema": {
                    "type": "object",
                    "required": ["project_item_id", "state_name"],
                    "properties": {
                        "project_item_id": {"type": "string"},
                        "state_name": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
        ]

    # 函数说明：根据工具名分派执行逻辑。
    async def execute(self, tool: str, arguments: Any) -> DynamicToolResult:
        if not self.tool_config.enabled:
            return _failure("GitHub dynamic tools are disabled.")

        try:
            if tool == "github_graphql":
                return await self._execute_graphql(arguments)
            if tool == "github_rest":
                return await self._execute_rest(arguments)
            if tool == "github_update_project_status":
                return await self._execute_update_project_status(arguments)
            return _failure(f"Unsupported dynamic tool: {tool}")
        except GitHubClientError as exc:
            return _failure(f"GitHub API error: {exc}")
        except ValueError as exc:
            return _failure(str(exc))
        except Exception as exc:  # noqa: BLE001 - 工具边界必须把异常转成失败响应。
            return _failure(f"GitHub dynamic tool failed: {exc}")

    # 函数说明：执行 GraphQL 动态工具。
    async def _execute_graphql(self, arguments: Any) -> DynamicToolResult:
        args = _expect_object(arguments, "github_graphql")
        query = args.get("query")
        variables = args.get("variables") or {}

        if not isinstance(query, str) or not query.strip():
            return _failure("github_graphql.query must be a non-empty string.")
        if not isinstance(variables, dict):
            return _failure("github_graphql.variables must be an object.")
        if self.tool_config.mode != "read_write" and _looks_like_graphql_mutation(query):
            return _failure("github_graphql mutations require tools.github.mode=read_write.")

        response = await self.client.graphql(query, variables)
        return _success(response)

    # 函数说明：执行 REST 动态工具。
    async def _execute_rest(self, arguments: Any) -> DynamicToolResult:
        args = _expect_object(arguments, "github_rest")
        method = str(args.get("method") or "").upper()
        path = args.get("path")
        query = args.get("query") or {}
        body = args.get("body")

        if method not in READ_METHODS | WRITE_METHODS:
            return _failure("github_rest.method is not allowed.")
        if method in WRITE_METHODS and self.tool_config.mode != "read_write":
            return _failure("github_rest write methods require tools.github.mode=read_write.")
        if not isinstance(path, str) or not _path_is_relative_api_path(path):
            return _failure("github_rest.path must be a GitHub API relative path.")
        if not isinstance(query, dict):
            return _failure("github_rest.query must be an object when provided.")
        if body is not None and not isinstance(body, dict):
            return _failure("github_rest.body must be an object when provided.")

        allowed, reason = _is_allowlisted_path(path, self.tracker_config.repositories)
        if not allowed:
            return _failure(f"github_rest.path is not allowlisted: {reason}")

        response = await self.client.rest(method, path, query=query, body=body)
        return _success(response)

    # 函数说明：通过 tracker 的 Project v2 Status 更新能力执行专用状态流转工具。
    async def _execute_update_project_status(self, arguments: Any) -> DynamicToolResult:
        args = _expect_object(arguments, "github_update_project_status")
        project_item_id = _required_text(args.get("project_item_id"), "project_item_id")
        state_name = _required_text(args.get("state_name"), "state_name")

        if self.tool_config.mode != "read_write":
            return _failure(
                "github_update_project_status requires tools.github.mode=read_write."
            )

        updater = self.project_status_updater or self._update_project_status_with_tracker
        response = await updater(project_item_id, state_name)
        return _success(response)

    # 函数说明：没有显式 tracker 实例时，构造临时 tracker 复用同一套 Status mutation 逻辑。
    async def _update_project_status_with_tracker(
        self,
        project_item_id: str,
        state_name: str,
    ) -> Dict[str, Any]:
        tracker = GitHubProjectsV2Tracker(
            self.tracker_config,
            BlockerPolicyConfig(),
            self.client,
        )
        return await tracker.update_project_status(project_item_id, state_name)


# 函数说明：构造成功工具响应。
def _success(payload: Any) -> DynamicToolResult:
    return DynamicToolResult(
        success=True,
        content_items=[{"type": "inputText", "text": json.dumps(payload, ensure_ascii=False)}],
    )


# 函数说明：构造失败工具响应。
def _failure(message: str) -> DynamicToolResult:
    return DynamicToolResult(
        success=False,
        content_items=[
            {
                "type": "inputText",
                "text": json.dumps({"error": message}, ensure_ascii=False),
            }
        ],
    )


# 函数说明：校验工具参数必须是 JSON object。
def _expect_object(arguments: Any, tool: str) -> Dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    raise ValueError(f"{tool} arguments must be an object.")


# 函数说明：读取必填字符串参数，并在空值时返回清晰工具失败。
def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"github_update_project_status.{name} must be a non-empty string.")
    return value.strip()


# 函数说明：确认 REST path 是相对 API path，而不是绝对 URL 或协议相对 URL。
def _path_is_relative_api_path(path: str) -> bool:
    if not path.startswith("/"):
        return False
    if path.startswith("//"):
        return False
    lowered = path.lower()
    return not lowered.startswith(("http://", "https://"))


# 函数说明：用轻量启发式判断 GraphQL 文档是否是 mutation。
def _looks_like_graphql_mutation(query: str) -> bool:
    stripped = query.lstrip()

    # 逻辑说明：GraphQL 文档最常见的写操作以 mutation 开头；注释和片段场景后续可增强。
    return stripped.startswith("mutation") or "\nmutation" in stripped


# 函数说明：检查 REST path 是否落在 GitHub Symphony 允许的资源范围内。
def _is_allowlisted_path(path: str, repositories: Sequence[str]) -> Tuple[bool, str]:
    if path in {"/rate_limit", "/user"}:
        return True, "global read endpoint"

    if path.startswith("/search/issues"):
        return True, "issue search endpoint"

    for repository in repositories:
        owner, repo = repository.split("/", 1)
        prefix = f"/repos/{owner}/{repo}/"
        if not path.startswith(prefix):
            continue

        rest = path[len(prefix) :]
        allowed_prefixes = (
            "issues",
            "pulls",
            "actions",
            "check-runs",
            "check-suites",
            "commits",
            "statuses",
            "branches",
            "contents",
            "labels",
            "milestones",
        )

        # 逻辑说明：仓库路径只允许与 Issue/PR/CI/代码读取相关的 REST 资源。
        if rest.startswith(allowed_prefixes):
            return True, "repository endpoint"

    return False, "path is outside configured repositories"
