"""后端核心单元测试。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

from symphony_github.core.config import build_config
from symphony_github.core.events import EventStore
from symphony_github.core.models import RunRecord, WorkItem
from symphony_github.core.orchestrator import Orchestrator
from symphony_github.core.prompt import PromptRenderError, render_prompt
from symphony_github.core.workflow import load_workflow
from symphony_github.integrations.github.client import GitHubClient
from symphony_github.integrations.github.dynamic_tools import GitHubDynamicTools
from symphony_github.integrations.github.tracker import GitHubProjectsV2Tracker


class WorkflowParsingTest(unittest.TestCase):
    """验证 WORKFLOW.md 解析与 prompt 渲染。"""

    # 函数说明：测试 front matter、block scalar 和 prompt body 都能被解析。
    def test_load_workflow_with_fallback_yaml_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workflow_path = Path(tmp) / "WORKFLOW.md"
            workflow_path.write_text(
                """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: acme
  project_number: 7
  repositories:
    - acme/demo
  api_token: $MISSING_GITHUB_SYMPHONY_TEST_TOKEN
  active_states: [Todo, In Progress]
  terminal_states: [Done, Closed]
workspace:
  root: workspaces
  hooks:
    after_create: |
      echo hello
---
Issue: {{ issue.identifier }}
""",
                encoding="utf-8",
            )

            document = load_workflow(str(workflow_path))

            self.assertEqual(document.config.tracker.owner, "acme")
            self.assertEqual(document.config.tracker.repositories, ["acme/demo"])
            self.assertIsNone(document.config.tracker.api_token)
            self.assertTrue(document.config.workspace.root.endswith("workspaces"))
            self.assertIn("echo hello", document.config.workspace.hooks.after_create or "")

    # 函数说明：测试标准库模板 fallback 对缺失变量保持严格失败。
    def test_prompt_render_is_strict(self) -> None:
        item = WorkItem(
            id="I_1",
            project_item_id="PVTI_1",
            identifier="acme/demo#1",
            kind="issue",
            title="Test",
            body=None,
            state="Todo",
            url="https://github.com/acme/demo/issues/1",
            repository="acme/demo",
            number=1,
        )

        self.assertEqual(
            render_prompt("{{ issue.identifier }}", {"issue": item}),
            "acme/demo#1",
        )
        with self.assertRaises(PromptRenderError):
            render_prompt("{{ issue.missing }}", {"issue": item})


class FakeGitHubClient(GitHubClient):
    """用于动态工具测试的假 GitHub client。"""

    # 函数说明：初始化假 client，不需要真实 token。
    def __init__(self) -> None:
        super().__init__(token="fake")
        self.calls = []

    # 函数说明：记录 GraphQL 调用并返回固定响应。
    async def graphql(self, query: str, variables: Dict | None = None) -> Dict:
        self.calls.append(("graphql", query, variables or {}))
        return {"data": {"ok": True}}

    # 函数说明：记录 REST 调用并返回固定响应。
    async def rest(self, method: str, path: str, query: Dict | None = None, body: Dict | None = None):
        self.calls.append(("rest", method, path, query or {}, body))
        return {"ok": True}


class DynamicToolsTest(unittest.IsolatedAsyncioTestCase):
    """验证 GitHub 动态工具参数和权限限制。"""

    # 函数说明：测试 read_only 模式会拒绝 REST 写操作。
    async def test_rest_write_requires_read_write_mode(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
                "tools": {"github": {"enabled": True, "mode": "read_only"}},
            }
        )
        tools = GitHubDynamicTools(FakeGitHubClient(), config.tracker, config.tools.github)
        result = await tools.execute(
            "github_rest",
            {"method": "POST", "path": "/repos/acme/demo/issues/1/comments", "body": {"body": "x"}},
        )

        self.assertFalse(result.success)
        self.assertIn("read_write", result.content_items[0]["text"])

    # 函数说明：测试 REST 工具拒绝配置仓库之外的路径。
    async def test_rest_path_must_be_allowlisted(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
            }
        )
        tools = GitHubDynamicTools(FakeGitHubClient(), config.tracker, config.tools.github)
        result = await tools.execute("github_rest", {"method": "GET", "path": "/repos/other/repo/issues"})

        self.assertFalse(result.success)
        self.assertIn("not allowlisted", result.content_items[0]["text"])

    # 函数说明：测试 read_only 模式会拒绝 GraphQL mutation。
    async def test_graphql_mutation_requires_read_write_mode(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
                "tools": {"github": {"enabled": True, "mode": "read_only"}},
            }
        )
        tools = GitHubDynamicTools(FakeGitHubClient(), config.tracker, config.tools.github)
        result = await tools.execute("github_graphql", {"query": "mutation X { __typename }"})

        self.assertFalse(result.success)
        self.assertIn("read_write", result.content_items[0]["text"])


class FakeProjectClient(GitHubClient):
    """用于 Projects v2 tracker 测试的假 GitHub client。"""

    # 函数说明：初始化 fake payload。
    def __init__(self) -> None:
        super().__init__(token="fake")
        self.mutation_variables = None

    # 函数说明：根据 query 名称返回 fields、items 或 mutation 响应。
    async def graphql(self, query: str, variables: Dict | None = None) -> Dict:
        if "GithubSymphonyProjectFields" in query:
            return _project_fields_payload()
        if "GithubSymphonyProjectItems" in query:
            return _project_items_payload()
        if "GithubSymphonyUpdateProjectStatus" in query:
            self.mutation_variables = variables
            return {"data": {"updateProjectV2ItemFieldValue": {"projectV2Item": {"id": "PVTI_1"}}}}
        raise AssertionError(f"unexpected query: {query}")

    # 函数说明：模拟 issue dependencies REST 响应。
    async def rest(self, method: str, path: str, query: Dict | None = None, body: Dict | None = None):
        if path.endswith("/issues/2/dependencies/blocked_by"):
            return [{"state": "open"}]
        return []


class GitHubTrackerTest(unittest.IsolatedAsyncioTestCase):
    """验证 GitHub Projects v2 tracker 归一化。"""

    # 函数说明：测试候选任务读取、Issue/PR 归一化和阻塞计数。
    async def test_fetch_candidate_issues_normalizes_project_items(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                    "active_states": ["Todo"],
                    "terminal_states": ["Done"],
                    "priority_field": "Priority",
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
            }
        )
        tracker = GitHubProjectsV2Tracker(
            config.tracker,
            config.blocker_policy,
            FakeProjectClient(),
            EventStore(),
        )

        items = await tracker.fetch_candidate_issues()

        self.assertEqual([item.identifier for item in items], ["acme/demo#2"])
        self.assertEqual(items[0].blocked_by_open_count, 1)
        self.assertEqual(items[0].priority, 2.0)

    # 函数说明：测试 Status 名称能解析成 single-select option id 并生成 mutation。
    async def test_update_project_status_resolves_option(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
            }
        )
        client = FakeProjectClient()
        tracker = GitHubProjectsV2Tracker(config.tracker, config.blocker_policy, client, EventStore())

        await tracker.update_project_status("PVTI_1", "Done")

        self.assertEqual(client.mutation_variables["projectId"], "PVT_1")
        self.assertEqual(client.mutation_variables["fieldId"], "PVTSSF_status")
        self.assertEqual(client.mutation_variables["optionId"], "done-id")


class FakeTracker:
    """用于调度器测试的假 tracker。"""

    # 函数说明：保存候选任务列表。
    def __init__(self, items: List[WorkItem]) -> None:
        self.items = items

    # 函数说明：返回候选任务。
    async def fetch_candidate_issues(self) -> List[WorkItem]:
        return self.items

    # 函数说明：按状态过滤任务。
    async def fetch_issues_by_states(self, state_names: List[str]) -> List[WorkItem]:
        return [item for item in self.items if item.state in state_names]

    # 函数说明：按 ID 返回任务。
    async def fetch_issue_states_by_ids(self, issue_ids: List[str]) -> Dict[str, WorkItem]:
        wanted = set(issue_ids)
        return {item.id: item for item in self.items if item.id in wanted}


class FakeRunner:
    """用于调度器测试的假 runner。"""

    # 函数说明：创建一个会等待事件的 runner，防止任务马上结束。
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release

    # 函数说明：模拟 runner 执行。
    async def run(self, item: WorkItem, run_record: RunRecord):
        await self.release.wait()

        # 逻辑说明：返回最小对象，满足调度器读取 should_continue 字段。
        class Result:
            should_continue = False

        return Result()


class OrchestratorTest(unittest.IsolatedAsyncioTestCase):
    """验证调度器派发规则。"""

    # 函数说明：测试阻塞 Todo 不派发，非阻塞 Todo 可派发。
    async def test_dispatch_skips_blocked_todo(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                    "active_states": ["Todo"],
                    "terminal_states": ["Done"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
                "agent": {"max_concurrent_agents": 2},
            }
        )
        blocked = _item("I_1", "acme/demo#1", blocked_by_open_count=1)
        ready = _item("I_2", "acme/demo#2", blocked_by_open_count=0)
        tracker = FakeTracker([blocked, ready])
        release = asyncio.Event()
        orchestrator = Orchestrator(
            config=config,
            prompt_template="",
            tracker=tracker,
            runner_factory=lambda: FakeRunner(release),
            events=EventStore(),
        )

        await orchestrator.poll_once()

        self.assertIn("I_2", orchestrator.running)
        self.assertNotIn("I_1", orchestrator.running)
        release.set()
        await asyncio.sleep(0)


# 函数说明：创建测试 WorkItem。
def _item(issue_id: str, identifier: str, blocked_by_open_count: int) -> WorkItem:
    return WorkItem(
        id=issue_id,
        project_item_id=f"PVTI_{issue_id}",
        identifier=identifier,
        kind="issue",
        title="Test",
        body=None,
        state="Todo",
        url=f"https://github.com/{identifier.replace('#', '/issues/')}",
        repository="acme/demo",
        number=int(identifier.rsplit("#", 1)[1]),
        blocked_by_open_count=blocked_by_open_count,
    )


# 函数说明：生成 Project fields GraphQL fake payload。
def _project_fields_payload() -> Dict:
    return {
        "data": {
            "organization": {
                "projectV2": {
                    "id": "PVT_1",
                    "fields": {
                        "nodes": [
                            {
                                "id": "PVTSSF_status",
                                "name": "Status",
                                "dataType": "SINGLE_SELECT",
                                "options": [
                                    {"id": "todo-id", "name": "Todo"},
                                    {"id": "done-id", "name": "Done"},
                                ],
                            },
                            {"id": "PVTF_priority", "name": "Priority", "dataType": "NUMBER"},
                        ]
                    },
                }
            }
        }
    }


# 函数说明：生成 Project items GraphQL fake payload。
def _project_items_payload() -> Dict:
    return {
        "data": {
            "organization": {
                "projectV2": {
                    "id": "PVT_1",
                    "items": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PVTI_1",
                                "isArchived": False,
                                "statusValue": {"name": "Done", "optionId": "done-id"},
                                "priorityValue": {"number": 1},
                                "content": {
                                    "__typename": "Issue",
                                    "id": "I_1",
                                    "number": 1,
                                    "title": "Done issue",
                                    "body": "Already done",
                                    "url": "https://github.com/acme/demo/issues/1",
                                    "state": "CLOSED",
                                    "createdAt": "2026-01-01T00:00:00Z",
                                    "updatedAt": "2026-01-02T00:00:00Z",
                                    "repository": {"nameWithOwner": "acme/demo"},
                                    "labels": {"nodes": [{"name": "bug"}]},
                                    "assignees": {"nodes": [{"login": "octo"}]},
                                },
                            },
                            {
                                "id": "PVTI_2",
                                "isArchived": False,
                                "statusValue": {"name": "Todo", "optionId": "todo-id"},
                                "priorityValue": {"number": 2},
                                "content": {
                                    "__typename": "Issue",
                                    "id": "I_2",
                                    "number": 2,
                                    "title": "Todo issue",
                                    "body": "Needs work",
                                    "url": "https://github.com/acme/demo/issues/2",
                                    "state": "OPEN",
                                    "createdAt": "2026-01-03T00:00:00Z",
                                    "updatedAt": "2026-01-04T00:00:00Z",
                                    "repository": {"nameWithOwner": "acme/demo"},
                                    "labels": {"nodes": []},
                                    "assignees": {"nodes": []},
                                },
                            },
                        ],
                    },
                }
            }
        }
    }


if __name__ == "__main__":
    unittest.main()
