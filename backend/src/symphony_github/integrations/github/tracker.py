"""GitHub Projects v2 tracker adapter。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from symphony_github.core.config import BlockerPolicyConfig, TrackerConfig
from symphony_github.core.events import EventStore
from symphony_github.core.models import WorkItem

from .client import GitHubClient, GitHubClientError

PROJECT_PAGE_SIZE = 50


@dataclass
class ProjectFieldCache:
    """缓存 Project v2 字段 ID 和 single-select options。"""

    project_id: str
    status_field_id: str
    status_options: Dict[str, str]
    priority_field_name: Optional[str]


@dataclass
class ProjectItemsFetchResult:
    """Project item 读取结果。"""

    items: list[WorkItem]
    total_items: int


class GitHubProjectsV2Tracker:
    """基于 GitHub Projects v2 的 tracker 实现。"""

    # 函数说明：保存 tracker 配置、HTTP client 和可选事件流。
    def __init__(
        self,
        config: TrackerConfig,
        blocker_policy: BlockerPolicyConfig,
        client: GitHubClient,
        events: Optional[EventStore] = None,
    ) -> None:
        self.config = config
        self.blocker_policy = blocker_policy
        self.client = client
        self.events = events
        self._allowed_repositories = set(config.repositories)
        self._field_cache: Optional[ProjectFieldCache] = None

    # 函数说明：读取 active states 内的候选任务。
    async def fetch_candidate_issues(self) -> List[WorkItem]:
        self._debug(
            "读取 active states 内的 GitHub Project 候选任务",
            {"states": list(self.config.active_states)},
        )
        return await self.fetch_issues_by_states(self.config.active_states)

    # 函数说明：读取指定 Project Status 状态集合下的任务。
    async def fetch_issues_by_states(self, state_names: Sequence[str]) -> List[WorkItem]:
        allowed_states = set(state_names)
        result = await self._fetch_project_items(allowed_states=allowed_states)
        self._debug(
            "GitHub Project 状态过滤完成",
            {
                "states": list(state_names),
                "total_items": result.total_items,
                "matched_items": len(result.items),
            },
        )
        return result.items

    # 函数说明：刷新指定任务 ID 的状态；为了简单可靠，v1 通过重新读取 project items 完成。
    async def fetch_issue_states_by_ids(self, issue_ids: Iterable[str]) -> Dict[str, WorkItem]:
        wanted = set(issue_ids)
        result = await self._fetch_project_items(
            include_blockers=False,
            wanted_issue_ids=wanted,
        )
        return {item.id: item for item in result.items}

    # 函数说明：更新 Project v2 Status 字段，供测试和未来 UI 操作用。
    async def update_project_status(self, project_item_id: str, state_name: str) -> Dict[str, Any]:
        field_cache = await self._get_field_cache()
        option_id = field_cache.status_options.get(state_name)
        if option_id is None:
            raise ValueError(f"Project Status 不存在选项：{state_name}")

        variables = {
            "projectId": field_cache.project_id,
            "itemId": project_item_id,
            "fieldId": field_cache.status_field_id,
            "optionId": option_id,
        }
        result = await self.client.graphql(UPDATE_PROJECT_STATUS_MUTATION, variables)
        self._debug(
            "GitHub Project item Status 已更新",
            {"project_item_id": project_item_id, "state": state_name},
        )
        return result

    # 函数说明：读取并归一化 Project v2 item，内部处理分页和预过滤条件。
    async def _fetch_project_items(
        self,
        allowed_states: set[str] | None = None,
        include_blockers: bool = True,
        wanted_issue_ids: set[str] | None = None,
    ) -> ProjectItemsFetchResult:
        field_cache = await self._get_field_cache()
        items: List[WorkItem] = []
        total_items = 0
        after: Optional[str] = None

        while True:
            payload = await self.client.graphql(
                project_items_query(self.config.owner_type),
                {
                    "owner": self.config.owner,
                    "number": self.config.project_number,
                    "after": after,
                    "statusField": self.config.status_field,
                    "priorityField": self.config.priority_field or "__github_symphony_priority__",
                },
            )
            project = _project_from_payload(payload, self.config.owner_type)
            nodes = project["items"]["nodes"] or []
            total_items += len(nodes)

            for node in nodes:
                # 逻辑说明：状态过滤必须早于完整归一化和 dependencies 查询。
                # 非目标状态不会被派发，提前跳过可避免每轮 poll 对 Done/Handoff
                # 项发起 REST 请求。
                if allowed_states is not None:
                    status_name = _status_name_from_project_item(node)
                    if status_name is None or status_name not in allowed_states:
                        continue

                # 逻辑说明：状态回查只关心指定 Issue/PR，先按内容 ID 缩小范围。
                # 这能避免当前运行很少但 Project 很大时归一化大量无关 item。
                if wanted_issue_ids is not None:
                    content = node.get("content")
                    if (
                        not isinstance(content, dict)
                        or str(content.get("id")) not in wanted_issue_ids
                    ):
                        continue

                normalized = await self._normalize_project_item(
                    node,
                    field_cache,
                    include_blockers=include_blockers,
                )
                if normalized is not None:
                    items.append(normalized)

            page_info = project["items"]["pageInfo"]
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")

        return ProjectItemsFetchResult(items=items, total_items=total_items)

    # 函数说明：把单个 Project item 转成 WorkItem；draft item 和不支持内容会被忽略。
    async def _normalize_project_item(
        self,
        node: Dict[str, Any],
        field_cache: ProjectFieldCache,
        include_blockers: bool = True,
    ) -> Optional[WorkItem]:
        if node.get("isArchived"):
            return None

        content = node.get("content")
        if not isinstance(content, dict):
            return None

        typename = content.get("__typename")
        if typename not in {"Issue", "PullRequest"}:
            return None

        repository = (content.get("repository") or {}).get("nameWithOwner")
        number = content.get("number")
        if not repository or number is None:
            return None

        if str(repository) not in self._allowed_repositories:
            self._debug(
                "GitHub Project item 仓库不在 tracker.repositories allowlist，已跳过",
                {
                    "project_item_id": str(node.get("id") or ""),
                    "repository": str(repository),
                    "number": int(number),
                    "kind": "pull_request" if typename == "PullRequest" else "issue",
                    "allowed_repositories": sorted(self._allowed_repositories),
                },
            )
            return None

        status_value = node.get("statusValue") or {}
        state = status_value.get("name")
        if not state:
            return None

        priority = _priority_from_value(node.get("priorityValue"))
        item = WorkItem(
            id=str(content["id"]),
            project_item_id=str(node["id"]),
            identifier=f"{repository}#{number}",
            kind="pull_request" if typename == "PullRequest" else "issue",
            title=str(content.get("title") or ""),
            body=content.get("body"),
            state=str(state),
            url=str(content.get("url") or ""),
            repository=str(repository),
            number=int(number),
            labels=_names_from_nodes(content.get("labels")),
            assignees=_logins_from_nodes(content.get("assignees")),
            created_at=content.get("createdAt"),
            updated_at=content.get("updatedAt"),
            priority=priority,
        )

        # 逻辑说明：只有候选派发路径需要 dependencies 参与阻塞判断；
        # 状态回查等路径只需要 state。
        if include_blockers:
            item.blocked_by_open_count = await self._blocked_by_open_count(item)
        return item

    # 函数说明：读取 GitHub issue dependencies；不可用时根据配置降级。
    async def _blocked_by_open_count(self, item: WorkItem) -> Optional[int]:
        if self.blocker_policy.kind != "github_issue_dependencies":
            return None

        owner, repo = _split_repository(item.repository)
        path = f"/repos/{owner}/{repo}/issues/{item.number}/dependencies/blocked_by"

        try:
            dependencies = await self.client.rest("GET", path)
        except GitHubClientError as exc:
            if exc.status in {403, 404, 410, 422} and (
                self.blocker_policy.unavailable_behavior == "treat_unblocked"
            ):
                self._warn(
                    "GitHub issue dependencies 不可用，按未阻塞处理",
                    {"identifier": item.identifier, "status": exc.status},
                )
                return None
            raise

        if not isinstance(dependencies, list):
            return None

        # 逻辑说明：REST 返回的 issue state 为 open/closed，closed 视为不阻塞。
        return sum(1 for issue in dependencies if issue.get("state") != "closed")

    # 函数说明：读取并缓存 Project v2 字段信息。
    async def _get_field_cache(self) -> ProjectFieldCache:
        if self._field_cache is not None:
            return self._field_cache

        payload = await self.client.graphql(
            project_fields_query(self.config.owner_type),
            {"owner": self.config.owner, "number": self.config.project_number},
        )
        project = _project_from_payload(payload, self.config.owner_type)
        status_field = _find_status_field(project["fields"]["nodes"], self.config.status_field)

        self._field_cache = ProjectFieldCache(
            project_id=str(project["id"]),
            status_field_id=str(status_field["id"]),
            status_options={
                str(option["name"]): str(option["id"])
                for option in status_field.get("options", [])
            },
            priority_field_name=self.config.priority_field,
        )
        return self._field_cache

    # 函数说明：向事件流写入非致命 warning。
    def _warn(self, message: str, payload: Dict[str, Any]) -> None:
        if self.events is not None:
            self.events.append("github.warning", message, payload)

    # 函数说明：向事件流写入调试级 GitHub tracker 摘要，便于 Logs 页面追踪调度决策。
    def _debug(self, message: str, payload: Dict[str, Any]) -> None:
        if self.events is not None:
            self.events.append("github.debug", message, payload)


# 函数说明：根据 owner_type 返回 Project fields 查询。
def project_fields_query(owner_type: str) -> str:
    owner_field = "organization" if owner_type == "org" else "user"
    return f"""
query GithubSymphonyProjectFields($owner: String!, $number: Int!) {{
  {owner_field}(login: $owner) {{
    projectV2(number: $number) {{
      id
      fields(first: 100) {{
        nodes {{
          ... on ProjectV2Field {{
            id
            name
            dataType
          }}
          ... on ProjectV2SingleSelectField {{
            id
            name
            dataType
            options {{
              id
              name
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


# 函数说明：根据 owner_type 返回 Project item 分页查询。
def project_items_query(owner_type: str) -> str:
    owner_field = "organization" if owner_type == "org" else "user"
    return f"""
query GithubSymphonyProjectItems(
  $owner: String!,
  $number: Int!,
  $after: String,
  $statusField: String!,
  $priorityField: String!
) {{
  {owner_field}(login: $owner) {{
    projectV2(number: $number) {{
      id
      items(first: {PROJECT_PAGE_SIZE}, after: $after) {{
        pageInfo {{
          hasNextPage
          endCursor
        }}
        nodes {{
          id
          isArchived
          statusValue: fieldValueByName(name: $statusField) {{
            ... on ProjectV2ItemFieldSingleSelectValue {{
              name
              optionId
            }}
          }}
          priorityValue: fieldValueByName(name: $priorityField) {{
            ... on ProjectV2ItemFieldNumberValue {{
              number
            }}
            ... on ProjectV2ItemFieldSingleSelectValue {{
              name
              optionId
            }}
            ... on ProjectV2ItemFieldTextValue {{
              text
            }}
          }}
          content {{
            __typename
            ... on Issue {{
              id
              number
              title
              body
              url
              state
              createdAt
              updatedAt
              repository {{ nameWithOwner }}
              labels(first: 20) {{ nodes {{ name }} }}
              assignees(first: 20) {{ nodes {{ login }} }}
            }}
            ... on PullRequest {{
              id
              number
              title
              body
              url
              state
              createdAt
              updatedAt
              repository {{ nameWithOwner }}
              labels(first: 20) {{ nodes {{ name }} }}
              assignees(first: 20) {{ nodes {{ login }} }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


UPDATE_PROJECT_STATUS_MUTATION = """
mutation GithubSymphonyUpdateProjectStatus(
  $projectId: ID!,
  $itemId: ID!,
  $fieldId: ID!,
  $optionId: String!
) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId,
    itemId: $itemId,
    fieldId: $fieldId,
    value: { singleSelectOptionId: $optionId }
  }) {
    projectV2Item { id }
  }
}
"""


# 函数说明：从 GraphQL payload 中取出 projectV2 节点。
def _project_from_payload(payload: Dict[str, Any], owner_type: str) -> Dict[str, Any]:
    owner_field = "organization" if owner_type == "org" else "user"
    owner = (payload.get("data") or {}).get(owner_field)
    if not owner or not owner.get("projectV2"):
        raise GitHubClientError("GitHub Project v2 不存在或当前 token 无权读取")
    return owner["projectV2"]


# 函数说明：在 fields 列表中查找 Status single-select 字段。
def _find_status_field(fields: List[Dict[str, Any]], field_name: str) -> Dict[str, Any]:
    for field in fields:
        if field.get("name") == field_name and field.get("options") is not None:
            return field
    raise ValueError(f"Project v2 中找不到 single-select 字段：{field_name}")


# 函数说明：从 Project item 节点中读取 Status single-select 名称。
def _status_name_from_project_item(node: dict[str, Any]) -> str | None:
    status_value = node.get("statusValue")
    if not isinstance(status_value, dict):
        return None
    name = status_value.get("name")
    return str(name) if name else None


# 函数说明：从 Project priority 字段值中提取可排序数字。
def _priority_from_value(value: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(value, dict):
        return None

    if value.get("number") is not None:
        return float(value["number"])

    text = value.get("name") or value.get("text")
    if text is None:
        return None

    try:
        return float(str(text).strip())
    except ValueError:
        return None


# 函数说明：从 GitHub connection nodes 提取 label name。
def _names_from_nodes(connection: Optional[Dict[str, Any]]) -> List[str]:
    nodes = (connection or {}).get("nodes") or []
    return [str(node["name"]) for node in nodes if node and node.get("name")]


# 函数说明：从 GitHub connection nodes 提取 assignee login。
def _logins_from_nodes(connection: Optional[Dict[str, Any]]) -> List[str]:
    nodes = (connection or {}).get("nodes") or []
    return [str(node["login"]) for node in nodes if node and node.get("login")]


# 函数说明：拆分 `owner/repo` 仓库标识。
def _split_repository(repository: str) -> Tuple[str, str]:
    parts = repository.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"无效 GitHub 仓库名：{repository}")
    return parts[0], parts[1]
