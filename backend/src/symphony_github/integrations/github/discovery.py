"""GitHub Settings 向导使用的只读发现服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

from .client import GitHubClient, redact_token

PROJECT_DISCOVERY_PAGE_SIZE = 100
PROJECT_ITEM_DISCOVERY_PAGE_SIZE = 100


@dataclass
class GitHubOwnerOption:
    """Settings 向导中可选择的 GitHub owner。"""

    owner_type: str
    login: str
    display_name: Optional[str] = None

    # 函数说明：转换为 API JSON 响应使用的字典。
    def to_dict(self) -> Dict[str, Any]:
        return {
            "owner_type": self.owner_type,
            "login": self.login,
            "display_name": self.display_name,
        }


@dataclass
class GitHubProjectOption:
    """Settings 向导中可选择的 GitHub Project v2。"""

    id: str
    number: int
    title: str
    owner: str
    owner_type: str
    closed: bool
    updated_at: Optional[str]

    # 函数说明：转换为 API JSON 响应使用的字典。
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "number": self.number,
            "title": self.title,
            "owner": self.owner,
            "owner_type": self.owner_type,
            "closed": self.closed,
            "updated_at": self.updated_at,
        }


@dataclass
class GitHubProjectFieldOption:
    """Settings 向导中可选择的 Project 字段。"""

    id: str
    name: str
    data_type: str
    kind: str
    options: Optional[List[Dict[str, Any]]] = None

    # 函数说明：转换为 API JSON 响应使用的字典。
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "data_type": self.data_type,
            "kind": self.kind,
            "options": self.options or [],
        }


class GitHubDiscoveryService:
    """围绕 GitHub GraphQL API 的 Settings 只读发现服务。"""

    # 函数说明：保存 client；调用方负责传入临时 token 初始化的 GitHubClient。
    def __init__(self, client: GitHubClient) -> None:
        self.client = client

    # 函数说明：读取当前 token 对应的 viewer 和组织列表，作为 owner 选择项。
    async def connect(self) -> Dict[str, Any]:
        payload = await self.client.graphql(CONNECT_QUERY)
        viewer = (payload.get("data") or {}).get("viewer") or {}
        viewer_login = str(viewer.get("login") or "")
        owners = [
            GitHubOwnerOption(
                owner_type="user",
                login=viewer_login,
                display_name=_optional_text(viewer.get("name")),
            )
        ]

        # 逻辑说明：组织列表由 viewer.organizations 提供；只返回 login/name，
        # 不返回任何权限细节或 token 信息，避免前端持久化敏感数据。
        organizations = ((viewer.get("organizations") or {}).get("nodes")) or []
        for organization in organizations:
            login = _optional_text(organization.get("login"))
            if login:
                owners.append(
                    GitHubOwnerOption(
                        owner_type="org",
                        login=login,
                        display_name=_optional_text(organization.get("name")),
                    )
                )

        return {
            "viewer": {
                "login": viewer_login,
                "name": _optional_text(viewer.get("name")),
            },
            "owners": [owner.to_dict() for owner in owners if owner.login],
            "warnings": [],
        }

    # 函数说明：分页读取某个 owner 下的 Projects v2。
    async def list_projects(self, owner_type: str, owner: str) -> Dict[str, Any]:
        projects: List[GitHubProjectOption] = []
        after: Optional[str] = None

        while True:
            payload = await self.client.graphql(
                PROJECTS_QUERY(owner_type),
                {"owner": owner, "after": after},
            )
            owner_node = _owner_from_payload(payload, owner_type)
            connection = owner_node.get("projectsV2") or {}
            nodes = connection.get("nodes") or []

            for node in nodes:
                normalized = _project_option_from_node(node, owner_type, owner)
                if normalized is not None:
                    projects.append(normalized)

            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")

        return {
            "projects": [project.to_dict() for project in projects],
            "warnings": [],
        }

    # 函数说明：读取 Project 字段和 item 中出现过的仓库，供配置向导自动填表。
    async def inspect_project(
        self,
        owner_type: str,
        owner: str,
        project_number: int,
    ) -> Dict[str, Any]:
        project_payload = await self.client.graphql(
            PROJECT_FIELDS_QUERY(owner_type),
            {"owner": owner, "number": project_number},
        )
        project = _project_from_payload(project_payload, owner_type)
        fields = [_field_option_from_node(node) for node in (project["fields"]["nodes"] or [])]
        normalized_fields = [field for field in fields if field is not None]
        repositories, item_sample_count = await self._discover_project_repositories(
            owner_type,
            owner,
            project_number,
        )
        warnings: List[str] = []

        if not repositories:
            warnings.append("当前 Project 中没有可识别的 Issue/PR 仓库，请手动补充 repositories。")

        return {
            "fields": [field.to_dict() for field in normalized_fields],
            "status_fields": [
                field.to_dict() for field in normalized_fields if field.kind == "single_select"
            ],
            "priority_fields": [
                field.to_dict()
                for field in normalized_fields
                if field.kind in {"number", "single_select", "text"}
            ],
            "repositories": sorted(repositories),
            "item_sample_count": item_sample_count,
            "warnings": warnings,
        }

    # 函数说明：分页读取 Project items，并从 Issue/PR content 中抽取 repository nameWithOwner。
    async def _discover_project_repositories(
        self,
        owner_type: str,
        owner: str,
        project_number: int,
    ) -> tuple[Set[str], int]:
        repositories: Set[str] = set()
        item_count = 0
        after: Optional[str] = None

        while True:
            payload = await self.client.graphql(
                PROJECT_REPOSITORIES_QUERY(owner_type),
                {"owner": owner, "number": project_number, "after": after},
            )
            project = _project_from_payload(payload, owner_type)
            connection = project.get("items") or {}
            nodes = connection.get("nodes") or []

            for node in nodes:
                item_count += 1
                repository = _repository_from_project_item(node)
                if repository:
                    repositories.add(repository)

            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor")

        return repositories, item_count


# 函数说明：创建 discovery service，集中校验 token 和 API URL 默认值。
def build_discovery_service(
    github_token: Any,
    api_base_url: Any = None,
    graphql_url: Any = None,
) -> GitHubDiscoveryService:
    token = str(github_token or "").strip()
    if not token:
        raise ValueError("github_token 必须填写")

    return GitHubDiscoveryService(
        GitHubClient(
            token=token,
            api_base_url=str(api_base_url or "https://api.github.com"),
            graphql_url=str(graphql_url or "https://api.github.com/graphql"),
        )
    )


# 函数说明：把 discovery 异常转成前端可展示的安全错误文案。
def safe_discovery_error(exc: Exception, token: Optional[str]) -> str:
    return redact_token(str(exc), token)


# 函数说明：返回读取 viewer 和组织列表的 GraphQL 查询。
CONNECT_QUERY = """
query GithubSymphonyDiscoveryConnect {
  viewer {
    login
    name
    organizations(first: 100) {
      nodes {
        login
        name
      }
    }
  }
}
"""


# 函数说明：根据 owner_type 返回 Projects v2 列表查询。
def PROJECTS_QUERY(owner_type: str) -> str:
    owner_field = _owner_field(owner_type)
    return f"""
query GithubSymphonyDiscoveryProjects($owner: String!, $after: String) {{
  {owner_field}(login: $owner) {{
    projectsV2(first: {PROJECT_DISCOVERY_PAGE_SIZE}, after: $after) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        id
        number
        title
        closed
        updatedAt
      }}
    }}
  }}
}}
"""


# 函数说明：根据 owner_type 返回 Project 字段查询。
def PROJECT_FIELDS_QUERY(owner_type: str) -> str:
    owner_field = _owner_field(owner_type)
    return f"""
query GithubSymphonyDiscoveryProjectFields($owner: String!, $number: Int!) {{
  {owner_field}(login: $owner) {{
    projectV2(number: $number) {{
      id
      title
      number
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
              color
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


# 函数说明：根据 owner_type 返回 Project item 仓库发现查询。
def PROJECT_REPOSITORIES_QUERY(owner_type: str) -> str:
    owner_field = _owner_field(owner_type)
    return f"""
query GithubSymphonyDiscoveryProjectRepositories(
  $owner: String!,
  $number: Int!,
  $after: String
) {{
  {owner_field}(login: $owner) {{
    projectV2(number: $number) {{
      items(first: {PROJECT_ITEM_DISCOVERY_PAGE_SIZE}, after: $after) {{
        pageInfo {{
          hasNextPage
          endCursor
        }}
        nodes {{
          content {{
            __typename
            ... on Issue {{
              repository {{ nameWithOwner }}
            }}
            ... on PullRequest {{
              repository {{ nameWithOwner }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


# 函数说明：把 owner_type 转成 GraphQL owner 字段名，并校验枚举。
def _owner_field(owner_type: str) -> str:
    if owner_type == "org":
        return "organization"
    if owner_type == "user":
        return "user"
    raise ValueError("owner_type 必须是 org 或 user")


# 函数说明：从 GraphQL payload 中读取 owner 节点。
def _owner_from_payload(payload: Dict[str, Any], owner_type: str) -> Dict[str, Any]:
    owner = (payload.get("data") or {}).get(_owner_field(owner_type))
    if not isinstance(owner, dict):
        raise ValueError("GitHub owner 不存在或当前 token 无权读取")
    return owner


# 函数说明：从 GraphQL payload 中读取 Project v2 节点。
def _project_from_payload(payload: Dict[str, Any], owner_type: str) -> Dict[str, Any]:
    owner = _owner_from_payload(payload, owner_type)
    project = owner.get("projectV2")
    if not isinstance(project, dict):
        raise ValueError("GitHub Project v2 不存在或当前 token 无权读取")
    return project


# 函数说明：把 Project GraphQL node 归一化为前端选项。
def _project_option_from_node(
    node: Dict[str, Any],
    owner_type: str,
    owner: str,
) -> Optional[GitHubProjectOption]:
    if not isinstance(node, dict) or node.get("number") is None:
        return None
    return GitHubProjectOption(
        id=str(node.get("id") or ""),
        number=int(node["number"]),
        title=str(node.get("title") or f"Project {node['number']}"),
        owner=owner,
        owner_type=owner_type,
        closed=bool(node.get("closed", False)),
        updated_at=_optional_text(node.get("updatedAt")),
    )


# 函数说明：把 Project 字段 GraphQL node 归一化为前端选项。
def _field_option_from_node(node: Dict[str, Any]) -> Optional[GitHubProjectFieldOption]:
    if not isinstance(node, dict) or not node.get("id") or not node.get("name"):
        return None

    data_type = str(node.get("dataType") or "UNKNOWN")
    options_raw = node.get("options")
    options = _field_options(options_raw) if isinstance(options_raw, list) else None
    return GitHubProjectFieldOption(
        id=str(node["id"]),
        name=str(node["name"]),
        data_type=data_type,
        kind=_field_kind(data_type, options),
        options=options,
    )


# 函数说明：把 Project single-select options 归一化为前端可渲染列表。
def _field_options(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "id": str(option.get("id") or ""),
            "name": str(option.get("name") or ""),
            "color": _optional_text(option.get("color")),
        }
        for option in options
        if isinstance(option, dict) and option.get("name")
    ]


# 函数说明：根据 dataType 和 options 判断字段类型。
def _field_kind(data_type: str, options: Optional[List[Dict[str, Any]]]) -> str:
    if options is not None:
        return "single_select"
    if data_type == "NUMBER":
        return "number"
    if data_type == "TEXT":
        return "text"
    return "other"


# 函数说明：从 Project item content 中提取仓库名。
def _repository_from_project_item(node: Dict[str, Any]) -> Optional[str]:
    content = node.get("content") if isinstance(node, dict) else None
    if not isinstance(content, dict):
        return None
    if content.get("__typename") not in {"Issue", "PullRequest"}:
        return None
    return _optional_text((content.get("repository") or {}).get("nameWithOwner"))


# 函数说明：把任意值规整为非空字符串或 None。
def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
