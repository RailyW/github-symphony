"""后端核心单元测试。"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch

from symphony_github.codex.app_server import (
    CodexAppServerClient,
    auto_approved_request_response,
    build_codex_path,
    codex_subprocess_env,
    default_request_response,
)
from symphony_github.core.config import build_config
from symphony_github.core.diagnostics import (
    configure_diagnostics,
    export_diagnostics_bundle,
    query_logs,
    redact_data,
    redact_text,
)
from symphony_github.core.events import EventStore
from symphony_github.core.models import RunRecord, WorkItem
from symphony_github.core.orchestrator import Orchestrator
from symphony_github.core.prompt import PromptRenderError, render_prompt
from symphony_github.core.runner import AgentRunner
from symphony_github.core.settings import (
    default_app_settings,
    export_workflow_text,
    import_workflow_text,
    normalize_app_settings,
)
from symphony_github.core.state_policy import build_workflow_prompt_context
from symphony_github.core.workflow import load_workflow
from symphony_github.core.workspace import WorkspaceError, WorkspaceManager, build_checkout_plan
from symphony_github.integrations.github.client import GitHubClient
from symphony_github.integrations.github.discovery import GitHubDiscoveryService
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
    async def rest(
        self,
        method: str,
        path: str,
        query: Dict | None = None,
        body: Dict | None = None,
    ):
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


class AppSettingsTest(unittest.IsolatedAsyncioTestCase):
    """验证 App 内配置和 WORKFLOW.md 兼容能力。"""

    # 函数说明：测试 WORKFLOW.md 导入不会丢失 prompt，也不会保存明文 token。
    def test_import_workflow_text_keeps_prompt_and_warns_for_plain_token(self) -> None:
        result = import_workflow_text(
            """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: acme
  project_number: 3
  repositories: [acme/demo]
  api_token: plain-secret
workspace:
  root: /tmp/github-symphony-test
---
请处理 {{ issue.identifier }}
"""
        )

        self.assertEqual(result.settings["tracker"]["owner"], "acme")
        self.assertEqual(result.settings["prompt_template"], "请处理 {{ issue.identifier }}")
        self.assertEqual(result.token_hint, "plain-secret")
        self.assertTrue(result.warnings)

    # 函数说明：测试 App settings 可导出为可再次解析的 WORKFLOW.md，且不泄露真实 token。
    def test_export_workflow_text_uses_token_placeholder(self) -> None:
        imported = import_workflow_text(
            """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: acme
  project_number: 3
  repositories: [acme/demo]
workspace:
  root: /tmp/github-symphony-test
---
Prompt body
"""
        )

        text = export_workflow_text(imported.settings)

        self.assertIn("api_token: $GITHUB_TOKEN", text)
        self.assertIn("Prompt body", text)
        self.assertNotIn("plain-secret", text)
        with tempfile.TemporaryDirectory() as tmp:
            workflow_path = Path(tmp) / "WORKFLOW.md"
            workflow_path.write_text(text, encoding="utf-8")
            document = load_workflow(str(workflow_path))
            self.assertEqual(document.config.tracker.owner, "acme")

    # 函数说明：测试 App settings 归一化会覆盖默认值并复用原配置校验。
    def test_normalize_app_settings_builds_full_config(self) -> None:
        imported = import_workflow_text(
            """---
tracker:
  kind: github_projects_v2
  owner_type: user
  owner: octo
  project_number: 9
  repositories:
    - octo/demo
workspace:
  root: /tmp/github-symphony-test
---
Prompt body
"""
        )

        document = normalize_app_settings(imported.settings, github_token="token")

        self.assertEqual(document.config.tracker.owner_type, "user")
        self.assertEqual(document.config.tracker.api_token, "token")
        self.assertEqual(document.config.agent.max_concurrent_agents, 3)
        self.assertEqual(document.config.completion_policy.kind, "agent_managed")
        self.assertEqual(document.config.completion_policy.success_state, "Human Review")
        self.assertFalse(document.config.completion_policy.mark_done_after_successful_turn)
        self.assertEqual(document.config.logging.level, "DEBUG")

    # 函数说明：测试默认 App settings 使用内置动态 checkout，不再写死单仓库 clone hook。
    def test_default_app_settings_uses_dynamic_checkout(self) -> None:
        settings = default_app_settings()
        document = normalize_app_settings(settings)

        self.assertEqual(document.config.workspace.checkout.mode, "clone")
        self.assertEqual(document.config.workspace.checkout.protocol, "ssh")
        self.assertEqual(document.config.workspace.checkout.depth, 1)
        self.assertIsNone(document.config.workspace.hooks.after_create)
        self.assertEqual(settings["workspace"]["checkout"]["repositories"], {})

    # 函数说明：测试只有 after_create 的旧 WORKFLOW 会保持 hook-only checkout 兼容模式。
    def test_hook_only_workflow_defaults_checkout_mode_to_hook(self) -> None:
        result = import_workflow_text(
            """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: acme
  project_number: 3
  repositories: [acme/demo]
workspace:
  root: /tmp/github-symphony-test
  hooks:
    after_create: git clone git@github.com:acme/demo.git .
---
Prompt body
"""
        )

        self.assertEqual(result.settings["workspace"]["checkout"]["mode"], "hook")
        self.assertIn("git clone", result.settings["workspace"]["hooks"]["after_create"])

    # 函数说明：测试 checkout 覆盖配置可导入、归一化并导出。
    def test_checkout_repository_overrides_round_trip(self) -> None:
        imported = import_workflow_text(
            """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: acme
  project_number: 3
  repositories: [acme/demo, acme/api]
workspace:
  root: /tmp/github-symphony-test
  checkout:
    mode: clone
    protocol: https
    depth: 5
    repositories:
      acme/api:
        clone_url: https://example.com/acme/api.git
        branch: develop
        path: src/api
---
Prompt body
"""
        )
        checkout = imported.settings["workspace"]["checkout"]

        self.assertEqual(checkout["protocol"], "https")
        self.assertEqual(checkout["depth"], 5)
        self.assertEqual(checkout["repositories"]["acme/api"]["branch"], "develop")
        exported = export_workflow_text(imported.settings)
        self.assertIn("checkout:", exported)
        self.assertIn("acme/api:", exported)
        self.assertIn("branch: develop", exported)

    # 函数说明：测试仓库 allowlist 必须严格使用单层 owner/repo，避免 REST 与 checkout 漂移。
    def test_repository_names_must_be_strict_owner_repo(self) -> None:
        with self.assertRaisesRegex(ValueError, "owner/repo"):
            build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo/extra"],
                    },
                    "workspace": {"root": "/tmp/github-symphony-test"},
                }
            )

        with self.assertRaisesRegex(ValueError, "重复"):
            build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo", "acme/demo"],
                    },
                    "workspace": {"root": "/tmp/github-symphony-test"},
                }
            )

    # 函数说明：测试默认状态机支持 PR 前自治，Merging 可派发而 Human Review 只用于交接。
    def test_default_config_uses_pr_autonomy_state_machine(self) -> None:
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

        self.assertEqual(
            config.tracker.status_options,
            [
                "Todo",
                "In Progress",
                "Rework",
                "Human Review",
                "Merging",
                "Done",
                "Closed",
                "Cancelled",
            ],
        )
        self.assertIn("Merging", config.tracker.active_states)
        self.assertNotIn("Human Review", config.tracker.active_states)
        self.assertEqual(config.tracker.handoff_states, ["Human Review"])
        self.assertEqual(config.tracker.terminal_states, ["Done", "Closed", "Cancelled"])
        self.assertEqual(config.completion_policy.kind, "agent_managed")
        self.assertEqual(config.completion_policy.success_state, "Human Review")
        self.assertEqual(config.completion_policy.failure_state, "Rework")
        self.assertFalse(config.completion_policy.mark_done_after_successful_turn)

    # 函数说明：测试显式 high-trust approval preset 会归一化为 Codex app-server 的 never。
    def test_high_trust_approval_preset_normalizes_to_never(self) -> None:
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
                "codex": {"approval_policy": {"preset": "high-trust"}},
            }
        )

        self.assertEqual(config.codex.approval_policy, "never")

    # 函数说明：测试自定义 Project 阶段可导入、归一化和导出，且成功目标不必属于 terminal。
    def test_custom_status_policy_allows_handoff_success_state(self) -> None:
        imported = import_workflow_text(
            """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: acme
  project_number: 3
  repositories: [acme/demo]
  status_options: [Backlog, Ready, Coding, Human Review, Rework, Shipped]
  active_states: [Ready, Coding, Rework]
  handoff_states: [Human Review]
  terminal_states: [Shipped]
workspace:
  root: /tmp/github-symphony-test
blocker_policy:
  kind: github_issue_dependencies
  unavailable_behavior: treat_unblocked
  blocked_states: [Ready]
completion_policy:
  kind: update_project_status
  success_state: Human Review
  failure_state: Rework
---
{{ workflow.status_policy_markdown }}
"""
        )

        document = normalize_app_settings(imported.settings, github_token="token")
        config = document.config

        self.assertEqual(config.tracker.status_options[3], "Human Review")
        self.assertEqual(config.tracker.handoff_states, ["Human Review"])
        self.assertEqual(config.blocker_policy.blocked_states, ["Ready"])
        self.assertEqual(config.completion_policy.success_state, "Human Review")
        exported = export_workflow_text(document.settings)
        self.assertIn("handoff_states", exported)
        self.assertIn("success_state: Human Review", exported)

    # 函数说明：测试自动完成目标不能仍处于 active，否则会造成成功后重复派发。
    def test_completion_target_cannot_be_active_when_app_updates_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "success_state"):
            build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                        "status_options": ["Ready", "Human Review", "Shipped"],
                        "active_states": ["Ready"],
                        "terminal_states": ["Shipped"],
                    },
                    "workspace": {"root": "/tmp/github-symphony-test"},
                    "completion_policy": {
                        "kind": "update_project_status",
                        "success_state": "Ready",
                        "mark_done_after_successful_turn": True,
                    },
                }
            )

    # 函数说明：测试阶段角色互斥，避免同一个状态既被派发又被视为交接。
    def test_status_roles_cannot_overlap(self) -> None:
        with self.assertRaisesRegex(ValueError, "handoff_states"):
            build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                        "status_options": ["Ready", "Human Review", "Shipped"],
                        "active_states": ["Ready", "Human Review"],
                        "handoff_states": ["Human Review"],
                        "terminal_states": ["Shipped"],
                    },
                    "workspace": {"root": "/tmp/github-symphony-test"},
                }
            )

    # 函数说明：测试已知 status_options 时拼错的阶段会被明确拒绝。
    def test_unknown_state_fails_when_status_options_known(self) -> None:
        with self.assertRaisesRegex(ValueError, "不存在"):
            build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                        "status_options": ["Ready", "Human Review", "Shipped"],
                        "active_states": ["Ready"],
                        "terminal_states": ["Shipped"],
                    },
                    "workspace": {"root": "/tmp/github-symphony-test"},
                    "blocker_policy": {"blocked_states": ["Typo"]},
                }
            )

    # 函数说明：测试没有 status_options 时仍兼容旧 WORKFLOW 中的任意状态名。
    def test_unknown_state_is_allowed_without_status_options(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                    "status_options": [],
                    "active_states": ["Ready"],
                    "terminal_states": ["Shipped"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
                "blocker_policy": {"blocked_states": ["Ready"]},
                "completion_policy": {"success_state": "Human Review"},
            }
        )

        self.assertEqual(config.completion_policy.success_state, "Human Review")

    # 函数说明：测试 prompt 阶段策略上下文可渲染给 agent 使用。
    def test_prompt_context_includes_workflow_status_policy(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                    "status_options": ["Ready", "Human Review", "Shipped"],
                    "active_states": ["Ready"],
                    "handoff_states": ["Human Review"],
                    "terminal_states": ["Shipped"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
                "completion_policy": {"success_state": "Human Review"},
            }
        )
        workflow = build_workflow_prompt_context(config)

        rendered = render_prompt(
            "{{ workflow.success_state }}\n{{ workflow.status_policy_markdown }}",
            {"workflow": workflow},
        )

        self.assertIn("Human Review", rendered)
        self.assertIn("active 阶段", rendered)

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
        result = await tools.execute(
            "github_rest",
            {"method": "GET", "path": "/repos/other/repo/issues"},
        )

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

    # 函数说明：测试专用 Project Status 工具会注册给 Codex app-server。
    def test_tool_specs_include_project_status_update(self) -> None:
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

        self.assertIn(
            "github_update_project_status",
            [tool["name"] for tool in tools.tool_specs()],
        )

    # 函数说明：测试 read_write 模式下专用工具复用 tracker 的 Project Status mutation。
    async def test_update_project_status_tool_uses_tracker_status_update(self) -> None:
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
        tools = GitHubDynamicTools(client, config.tracker, config.tools.github)

        result = await tools.execute(
            "github_update_project_status",
            {"project_item_id": "PVTI_1", "state_name": "Done"},
        )

        self.assertTrue(result.success)
        self.assertEqual(client.mutation_variables["itemId"], "PVTI_1")
        self.assertEqual(client.mutation_variables["optionId"], "done-id")

    # 函数说明：测试 read_only 模式下专用 Project Status 工具返回结构化失败。
    async def test_update_project_status_tool_requires_read_write(self) -> None:
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
        tools = GitHubDynamicTools(FakeProjectClient(), config.tracker, config.tools.github)

        result = await tools.execute(
            "github_update_project_status",
            {"project_item_id": "PVTI_1", "state_name": "Done"},
        )

        self.assertFalse(result.success)
        self.assertIn("read_write", result.content_items[0]["text"])

    # 函数说明：测试专用 Project Status 工具会校验空 item id 和未知状态。
    async def test_update_project_status_tool_returns_structured_failures(self) -> None:
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
        tools = GitHubDynamicTools(FakeProjectClient(), config.tracker, config.tools.github)

        empty_result = await tools.execute(
            "github_update_project_status",
            {"project_item_id": "", "state_name": "Done"},
        )
        unknown_result = await tools.execute(
            "github_update_project_status",
            {"project_item_id": "PVTI_1", "state_name": "Missing"},
        )

        self.assertFalse(empty_result.success)
        self.assertIn("project_item_id", empty_result.content_items[0]["text"])
        self.assertFalse(unknown_result.success)
        self.assertIn("不存在选项", unknown_result.content_items[0]["text"])


class CodexAppServerClientTest(unittest.TestCase):
    """验证 Codex app-server 客户端的环境辅助逻辑。"""

    # 函数说明：测试 PATH 构造会保留系统路径，并过滤重复路径。
    def test_build_codex_path_keeps_existing_system_path_without_duplicates(self) -> None:
        existing = "/usr/bin:/bin:/usr/bin"
        merged = build_codex_path(existing)
        parts = merged.split(":")

        self.assertIn("/usr/bin", parts)
        self.assertIn("/bin", parts)
        self.assertEqual(parts.count("/usr/bin"), 1)

    # 函数说明：测试配置的 GitHub token 会注入 Codex 子进程环境变量。
    def test_codex_subprocess_env_injects_github_tokens(self) -> None:
        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "parent-token", "GH_TOKEN": "parent-gh-token"},
            clear=False,
        ):
            env = codex_subprocess_env("ghp_1234567890abcdefghijklmnopqrstuvwxyz")

        self.assertEqual(env["GITHUB_TOKEN"], "ghp_1234567890abcdefghijklmnopqrstuvwxyz")
        self.assertEqual(env["GH_TOKEN"], "ghp_1234567890abcdefghijklmnopqrstuvwxyz")

    # 函数说明：测试未配置 tracker token 时不会把父进程 GitHub token 泄露给 agent。
    def test_codex_subprocess_env_does_not_inherit_github_tokens_without_configured_token(
        self,
    ) -> None:
        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "parent-token", "GH_TOKEN": "parent-gh-token"},
            clear=False,
        ):
            env = codex_subprocess_env(None)

        self.assertNotIn("GITHUB_TOKEN", env)
        self.assertNotIn("GH_TOKEN", env)

    # 函数说明：测试默认 approval 响应保持保守拒绝。
    def test_default_approval_response_declines_requests(self) -> None:
        self.assertEqual(
            default_request_response("item/commandExecution/requestApproval"),
            {"decision": "decline"},
        )
        self.assertEqual(default_request_response("applyPatchApproval"), {"decision": "denied"})

    # 函数说明：测试 approval_policy=never 会生成高信任自动批准响应。
    def test_never_approval_policy_auto_approves_known_requests(self) -> None:
        self.assertEqual(
            auto_approved_request_response("item/commandExecution/requestApproval", {}),
            {"decision": "acceptForSession"},
        )
        self.assertEqual(
            auto_approved_request_response("execCommandApproval", {}),
            {"decision": "approved_for_session"},
        )
        tool_response = auto_approved_request_response(
            "item/tool/requestUserInput",
            {
                "questions": [
                    {
                        "id": "mcp_tool_call_approval_1",
                        "options": [
                            {"label": "Approve Once"},
                            {"label": "Approve this Session"},
                            {"label": "Deny"},
                        ],
                    }
                ]
            },
        )

        self.assertEqual(
            tool_response["answers"]["mcp_tool_call_approval_1"]["answers"],
            ["Approve this Session"],
        )

    # 函数说明：测试 app-server 处理自动批准时会写入可观测事件。
    def test_never_approval_policy_emits_auto_approved_event(self) -> None:
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
                "codex": {"approval_policy": "never"},
            }
        )
        events = EventStore()
        client = CapturingCodexAppServerClient(
            config=config.codex,
            workspace="/tmp/github-symphony-test",
            events=events,
        )

        asyncio.run(
            client._handle_approval_or_input_request(
                {"id": 99, "method": "item/fileChange/requestApproval", "params": {}}
            )
        )

        self.assertEqual(client.writes[0]["result"], {"decision": "acceptForSession"})
        self.assertTrue(
            any(event.event_type == "codex.request.auto_approved" for event in events.recent())
        )

    # 函数说明：测试动态工具执行器异常会转成结构化失败，不会中断 app-server 读取循环。
    def test_dynamic_tool_executor_exception_returns_structured_failure(self) -> None:
        secret = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
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

        # 函数说明：模拟工具执行器内部 bug；异常文本包含 PAT，用于验证响应脱敏。
        async def failing_executor(_tool: str, _arguments: dict) -> dict:
            raise RuntimeError(f"boom {secret}")

        events = EventStore()
        client = CapturingCodexAppServerClient(
            config=config.codex,
            workspace="/tmp/github-symphony-test",
            events=events,
            dynamic_tool_executor=failing_executor,
        )

        asyncio.run(
            client._handle_dynamic_tool_call(
                {
                    "id": 101,
                    "method": "item/tool/call",
                    "params": {
                        "tool": "github_update_project_status",
                        "arguments": {"project_item_id": "PVTI_1", "state_name": "Done"},
                    },
                }
            )
        )

        result = client.writes[0]["result"]
        error_text = result["contentItems"][0]["text"]

        self.assertFalse(result["success"])
        self.assertIn("Dynamic tool failed", json.loads(error_text)["error"])
        self.assertNotIn(secret, error_text)
        self.assertTrue(
            any(event.event_type == "codex.dynamic_tool.called" for event in events.recent())
        )


class CapturingCodexAppServerClient(CodexAppServerClient):
    """用于测试 app-server request handler 的假客户端。"""

    # 函数说明：初始化写入记录，避免单元测试启动真实 Codex 进程。
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.writes: List[Dict] = []

    # 函数说明：截获 JSON-RPC 响应，供测试断言 approval 自动响应内容。
    async def _write(self, message: Dict) -> None:
        self.writes.append(message)


class FakeProjectClient(GitHubClient):
    """用于 Projects v2 tracker 测试的假 GitHub client。"""

    # 函数说明：初始化 fake payload。
    def __init__(self) -> None:
        super().__init__(token="fake")
        self.mutation_variables = None
        self.rest_paths: list[str] = []

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
    async def rest(
        self,
        method: str,
        path: str,
        query: Dict | None = None,
        body: Dict | None = None,
    ):
        self.rest_paths.append(path)
        if path.endswith("/issues/2/dependencies/blocked_by"):
            return [{"state": "open"}]
        return []


class WorkspaceCheckoutTest(unittest.TestCase):
    """验证工作区内置 checkout 行为。"""

    # 函数说明：测试默认 checkout plan 会使用当前 work item 的仓库生成 SSH clone 命令。
    def test_checkout_plan_uses_current_work_item_repository(self) -> None:
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
        plan = build_checkout_plan(
            config.workspace.checkout,
            Path("/tmp/github-symphony-test/acme-demo-1"),
            _item("I_1", "acme/demo#1", blocked_by_open_count=0),
        )

        self.assertIsNotNone(plan)
        self.assertEqual(
            plan.command,
            ["git", "clone", "--depth", "1", "git@github.com:acme/demo.git", "."],
        )

    # 函数说明：测试新建工作区 clone 时默认 URL 来自当前 Project item 的 repository。
    def test_prepare_new_workspace_clones_current_item_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/api"],
                    },
                    "workspace": {"root": tmp},
                }
            )
            calls = []

            # 函数说明：截获新建 checkout，避免单元测试访问真实 GitHub。
            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("symphony_github.core.workspace.subprocess.run", side_effect=fake_run):
                WorkspaceManager(config.workspace).prepare(
                    _item("I_8", "acme/api#8", blocked_by_open_count=0)
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(
                calls[0][0],
                ["git", "clone", "--depth", "1", "git@github.com:acme/api.git", "."],
            )

    # 函数说明：测试自定义 clone_url、branch、path 会进入 clone 命令并先于 hook 执行。
    def test_prepare_runs_checkout_before_after_create_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/api"],
                    },
                    "workspace": {
                        "root": tmp,
                        "checkout": {
                            "mode": "clone",
                            "protocol": "https",
                            "depth": 5,
                            "repositories": {
                                "acme/api": {
                                    "clone_url": "https://example.com/acme/api.git",
                                    "branch": "develop",
                                    "path": "src/api",
                                }
                            },
                        },
                        "hooks": {"after_create": "echo setup"},
                    },
                }
            )
            calls = []

            # 函数说明：截获 subprocess.run，避免单元测试访问真实 git 或 shell。
            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("symphony_github.core.workspace.subprocess.run", side_effect=fake_run):
                workspace = WorkspaceManager(config.workspace).prepare(
                    _item("I_7", "acme/api#7", blocked_by_open_count=0)
                )

            self.assertEqual(
                calls[0][0],
                [
                    "git",
                    "clone",
                    "--depth",
                    "5",
                    "--branch",
                    "develop",
                    "https://example.com/acme/api.git",
                    "src/api",
                ],
            )
            self.assertEqual(calls[1][0], "echo setup")
            self.assertEqual(calls[0][1]["cwd"], workspace)
            self.assertEqual(calls[1][1]["cwd"], workspace)
            self.assertEqual(calls[0][1]["env"]["SYMPHONY_REPOSITORY"], "acme/api")

    # 函数说明：测试旧 hook-only 配置不会自动执行内置 checkout。
    def test_hook_only_workspace_runs_only_after_create_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                    },
                    "workspace": {
                        "root": tmp,
                        "hooks": {"after_create": "git clone git@github.com:acme/demo.git ."},
                    },
                }
            )
            calls = []

            # 函数说明：截获 hook 执行，验证没有额外 git clone checkout 调用。
            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("symphony_github.core.workspace.subprocess.run", side_effect=fake_run):
                WorkspaceManager(config.workspace).prepare(
                    _item("I_1", "acme/demo#1", blocked_by_open_count=0)
                )

            self.assertEqual(config.workspace.checkout.mode, "hook")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "git clone git@github.com:acme/demo.git .")

    # 函数说明：测试已存在工作区 origin 匹配当前仓库时允许复用，且不会再次 clone。
    def test_existing_matching_remote_allows_workspace_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                    },
                    "workspace": {"root": tmp},
                }
            )
            workspace = Path(tmp) / "acme-demo-1"
            workspace.mkdir(parents=True)
            calls = []

            # 函数说明：模拟 git remote get-url origin 返回匹配当前 item.repository 的远端。
            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="git@github.com:acme/demo.git\n",
                    stderr="",
                )

            with patch("symphony_github.core.workspace.subprocess.run", side_effect=fake_run):
                result = WorkspaceManager(config.workspace).prepare(
                    _item("I_1", "acme/demo#1", blocked_by_open_count=0)
                )

            self.assertEqual(result, str(workspace.resolve()))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0][:4], ["git", "-C", str(workspace.resolve()), "remote"])

    # 函数说明：测试已存在工作区若仍指向旧模板占位仓库，会明确报错而不删除目录。
    def test_existing_placeholder_remote_rejects_workspace_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                    },
                    "workspace": {"root": tmp},
                }
            )
            workspace = Path(tmp) / "acme-demo-1"
            workspace.mkdir(parents=True)

            # 函数说明：模拟旧 settings 占位 hook 曾经克隆出的错误 origin。
            def fake_run(command, **kwargs):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="git@github.com:your-org/your-repo.git\n",
                    stderr="",
                )

            with patch("symphony_github.core.workspace.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(WorkspaceError, "仓库不匹配"):
                    WorkspaceManager(config.workspace).prepare(
                        _item("I_1", "acme/demo#1", blocked_by_open_count=0)
                    )

            self.assertTrue(workspace.exists())

    # 函数说明：测试 checkout path 不能逃出单个 work item 工作区。
    def test_checkout_path_cannot_escape_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                    },
                    "workspace": {
                        "root": tmp,
                        "checkout": {
                            "mode": "clone",
                            "repositories": {"acme/demo": {"path": "../outside"}},
                        },
                    },
                }
            )

            with self.assertRaisesRegex(WorkspaceError, "越界"):
                WorkspaceManager(config.workspace).prepare(
                    _item("I_1", "acme/demo#1", blocked_by_open_count=0)
                )

    # 函数说明：测试 clone 失败会清理新工作区并脱敏错误，确保下一轮重试会重新 checkout。
    def test_failed_checkout_is_redacted_and_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                    },
                    "workspace": {
                        "root": tmp,
                        "checkout": {
                            "mode": "clone",
                            "repositories": {
                                "acme/demo": {
                                    "clone_url": "https://user:topsecret@example.com/acme/demo.git"
                                }
                            },
                        },
                    },
                }
            )
            manager = WorkspaceManager(config.workspace)
            item = _item("I_1", "acme/demo#1", blocked_by_open_count=0)
            workspace = Path(tmp) / "acme-demo-1"
            calls = []

            # 函数说明：第一次 clone 模拟鉴权失败，第二次模拟调度器重试时成功。
            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                if len(calls) == 1:
                    return subprocess.CompletedProcess(
                        command,
                        128,
                        stdout="",
                        stderr=(
                            "fatal: could not read Username for "
                            "'https://user:topsecret@example.com/acme/demo.git'"
                        ),
                    )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with patch("symphony_github.core.workspace.subprocess.run", side_effect=fake_run):
                with self.assertRaises(WorkspaceError) as captured:
                    manager.prepare(item)

                self.assertNotIn("topsecret", str(captured.exception))
                self.assertNotIn("user:topsecret", str(captured.exception))
                self.assertFalse(workspace.exists())
                self.assertEqual(manager.prepare(item), str(workspace.resolve()))

            self.assertEqual(len(calls), 2)


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
        client = FakeProjectClient()
        tracker = GitHubProjectsV2Tracker(
            config.tracker,
            config.blocker_policy,
            client,
            EventStore(),
        )

        items = await tracker.fetch_candidate_issues()

        self.assertEqual([item.identifier for item in items], ["acme/demo#2"])
        self.assertEqual(items[0].blocked_by_open_count, 1)
        self.assertEqual(items[0].priority, 2.0)
        self.assertEqual(client.rest_paths, ["/repos/acme/demo/issues/2/dependencies/blocked_by"])

    # 函数说明：测试 tracker.repositories 会作为 Project item 仓库 allowlist。
    async def test_fetch_candidate_issues_skips_items_outside_repository_allowlist(self) -> None:
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
        events = EventStore()
        tracker = GitHubProjectsV2Tracker(
            config.tracker,
            config.blocker_policy,
            FakeProjectClient(),
            events,
        )

        items = await tracker.fetch_candidate_issues()
        skipped_events = [
            event
            for event in events.recent()
            if event.message
            == "GitHub Project item 仓库不在 tracker.repositories allowlist，已跳过"
        ]

        self.assertEqual([item.repository for item in items], ["acme/demo"])
        self.assertEqual(skipped_events[0].payload["repository"], "other/repo")
        self.assertEqual(skipped_events[0].payload["allowed_repositories"], ["acme/demo"])

    # 函数说明：测试运行中任务状态回查不会额外读取 dependencies。
    async def test_fetch_issue_states_by_ids_skips_dependency_lookup(self) -> None:
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
        client = FakeProjectClient()
        tracker = GitHubProjectsV2Tracker(
            config.tracker,
            config.blocker_policy,
            client,
            EventStore(),
        )

        states = await tracker.fetch_issue_states_by_ids(["I_1"])

        self.assertEqual(states["I_1"].state, "Done")
        self.assertEqual(client.rest_paths, [])

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
        tracker = GitHubProjectsV2Tracker(
            config.tracker,
            config.blocker_policy,
            client,
            EventStore(),
        )

        await tracker.update_project_status("PVTI_1", "Done")

        self.assertEqual(client.mutation_variables["projectId"], "PVT_1")
        self.assertEqual(client.mutation_variables["fieldId"], "PVTSSF_status")
        self.assertEqual(client.mutation_variables["optionId"], "done-id")


class FakeDiscoveryClient(GitHubClient):
    """用于 Settings 向导 discovery 测试的假 GitHub client。"""

    # 函数说明：初始化假 client 并保存调用记录，便于断言分页和查询类型。
    def __init__(self) -> None:
        super().__init__(token="fake-token")
        self.calls = []

    # 函数说明：根据 discovery GraphQL query 名称返回对应假响应。
    async def graphql(self, query: str, variables: Dict | None = None) -> Dict:
        variables = variables or {}
        self.calls.append((query, variables))
        if "GithubSymphonyDiscoveryConnect" in query:
            return _discovery_connect_payload()
        if "GithubSymphonyDiscoveryProjects" in query:
            return _discovery_projects_payload()
        if "GithubSymphonyDiscoveryProjectFields" in query:
            return _discovery_project_fields_payload()
        if "GithubSymphonyDiscoveryProjectRepositories" in query:
            return _discovery_project_repositories_payload()
        raise AssertionError(f"unexpected discovery query: {query}")


class GitHubDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    """验证 Settings PAT 向导的 GitHub 只读发现能力。"""

    # 函数说明：测试 connect 能返回当前用户和组织 owner 选择项。
    async def test_connect_returns_viewer_and_owner_options(self) -> None:
        service = GitHubDiscoveryService(FakeDiscoveryClient())

        result = await service.connect()

        self.assertEqual(result["viewer"]["login"], "octo")
        self.assertEqual(
            [(owner["owner_type"], owner["login"]) for owner in result["owners"]],
            [("user", "octo"), ("org", "acme")],
        )

    # 函数说明：测试 Project 列表 discovery 会归一化 number/title/owner 信息。
    async def test_list_projects_returns_project_options(self) -> None:
        service = GitHubDiscoveryService(FakeDiscoveryClient())

        result = await service.list_projects("org", "acme")

        self.assertEqual(result["projects"][0]["number"], 12)
        self.assertEqual(result["projects"][0]["title"], "Roadmap")
        self.assertEqual(result["projects"][0]["owner"], "acme")

    # 函数说明：测试 Project 详情 discovery 会返回字段、状态选项和推断仓库。
    async def test_inspect_project_returns_fields_and_repositories(self) -> None:
        service = GitHubDiscoveryService(FakeDiscoveryClient())

        result = await service.inspect_project("org", "acme", 12)

        self.assertEqual(result["status_fields"][0]["name"], "Status")
        self.assertEqual(
            [option["name"] for option in result["status_fields"][0]["options"]],
            ["Todo", "In Progress", "Done"],
        )
        self.assertIn("Priority", [field["name"] for field in result["priority_fields"]])
        self.assertEqual(result["repositories"], ["acme/api", "acme/web"])
        self.assertEqual(result["item_sample_count"], 3)


class FakeTracker:
    """用于调度器测试的假 tracker。"""

    # 函数说明：保存候选任务列表。
    def __init__(self, items: List[WorkItem]) -> None:
        self.items = items
        self.status_updates = []
        self.fail_status_update = False

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

    # 函数说明：模拟 Project Status 更新；默认直接修改内存 WorkItem 状态。
    async def update_project_status(self, project_item_id: str, state_name: str) -> Dict:
        self.status_updates.append((project_item_id, state_name))
        if self.fail_status_update:
            raise RuntimeError("status update failed")
        for item in self.items:
            if item.project_item_id == project_item_id:
                item.state = state_name
        return {"ok": True}


class FailingTracker(FakeTracker):
    """用于验证调度主循环异常隔离的假 tracker。"""

    # 函数说明：模拟 GitHub API 或配置错误导致候选任务读取失败。
    async def fetch_candidate_issues(self) -> List[WorkItem]:
        raise RuntimeError("boom from tracker")


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


class FakeCodexTurnResult:
    """用于 AgentRunner 测试的假 Codex turn 结果。"""

    thread_id = "thread-1"
    turn_id = "turn-1"
    completed = True
    final_state = "completed"


class FakeCodexClient:
    """用于 AgentRunner 测试的假 Codex app-server client。"""

    # 函数说明：初始化调用记录，便于断言 runner 确实发起了一次 turn。
    def __init__(self) -> None:
        self.prompts: List[str] = []
        self.closed = False

    # 函数说明：模拟一次成功 Codex turn。
    async def run_turn(self, prompt: str) -> FakeCodexTurnResult:
        self.prompts.append(prompt)
        return FakeCodexTurnResult()

    # 函数说明：记录 close 调用，验证 runner 不泄漏 app-server client。
    async def close(self) -> None:
        self.closed = True


class FakeAgentRunnerWithCodex(AgentRunner):
    """允许测试替换 Codex client 的 AgentRunner。"""

    # 函数说明：保存 fake Codex client，避免单元测试启动真实 codex app-server。
    def __init__(self, *args, fake_codex: FakeCodexClient, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fake_codex = fake_codex

    # 函数说明：返回测试提供的 fake Codex client。
    def _build_codex_client(self, workspace: str) -> FakeCodexClient:
        return self.fake_codex


class AgentRunnerCompletionTest(unittest.IsolatedAsyncioTestCase):
    """验证成功 turn 后的 Project Status 完成策略。"""

    # 函数说明：测试成功 turn 会把 Project item 状态更新到 Done，并停止 continuation。
    async def test_successful_turn_marks_project_item_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                        "api_token": "fake-token",
                        "active_states": ["Todo"],
                        "terminal_states": ["Done"],
                    },
                    "workspace": {"root": tmp, "checkout": {"mode": "none"}},
                    "completion_policy": {
                        "kind": "update_project_status",
                        "success_state": "Done",
                        "mark_done_after_successful_turn": True,
                    },
                }
            )
            item = _item("I_1", "acme/demo#1", blocked_by_open_count=0)
            tracker = FakeTracker([item])
            events = EventStore()
            fake_codex = FakeCodexClient()
            runner = FakeAgentRunnerWithCodex(
                config=config,
                prompt_template="{{ issue.identifier }}",
                tracker=tracker,
                events=events,
                fake_codex=fake_codex,
            )
            run_record = RunRecord(
                issue_id=item.id,
                identifier=item.identifier,
                state="running",
                workspace="",
            )

            result = await runner.run(item, run_record)

            self.assertFalse(result.should_continue)
            self.assertEqual(item.state, "Done")
            self.assertEqual(tracker.status_updates, [(item.project_item_id, "Done")])
            self.assertEqual(run_record.thread_id, "thread-1")
            self.assertTrue(fake_codex.closed)
            self.assertTrue(
                any(
                    event.event_type == "orchestrator.completion_status_updated"
                    for event in events.recent()
                )
            )

    # 函数说明：测试成功 turn 可更新到非 terminal 的 handoff 状态，并通过 prompt 告知 agent。
    async def test_successful_turn_can_move_to_handoff_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                        "api_token": "fake-token",
                        "status_options": ["Ready", "Human Review", "Shipped"],
                        "active_states": ["Ready"],
                        "handoff_states": ["Human Review"],
                        "terminal_states": ["Shipped"],
                    },
                    "workspace": {"root": tmp, "checkout": {"mode": "none"}},
                    "completion_policy": {
                        "kind": "update_project_status",
                        "success_state": "Human Review",
                        "mark_done_after_successful_turn": True,
                    },
                }
            )
            item = _item("I_1", "acme/demo#1", blocked_by_open_count=0)
            item.state = "Ready"
            tracker = FakeTracker([item])
            fake_codex = FakeCodexClient()
            runner = FakeAgentRunnerWithCodex(
                config=config,
                prompt_template="{{ workflow.status_policy_markdown }}",
                tracker=tracker,
                events=EventStore(),
                fake_codex=fake_codex,
            )
            run_record = RunRecord(
                issue_id=item.id,
                identifier=item.identifier,
                state="running",
                workspace="",
            )

            result = await runner.run(item, run_record)

            self.assertFalse(result.should_continue)
            self.assertEqual(item.state, "Human Review")
            self.assertEqual(tracker.status_updates, [(item.project_item_id, "Human Review")])
            self.assertIn("Human Review", fake_codex.prompts[0])

    # 函数说明：测试 agent_managed 完成策略不会由 App 自动写 Project Status。
    async def test_agent_managed_completion_does_not_update_project_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                        "api_token": "fake-token",
                        "active_states": ["Todo"],
                        "terminal_states": ["Done"],
                    },
                    "workspace": {"root": tmp, "checkout": {"mode": "none"}},
                    "agent": {"max_turns": 1},
                    "completion_policy": {
                        "kind": "agent_managed",
                        "success_state": "Done",
                        "mark_done_after_successful_turn": True,
                    },
                }
            )
            item = _item("I_1", "acme/demo#1", blocked_by_open_count=0)
            tracker = FakeTracker([item])
            runner = FakeAgentRunnerWithCodex(
                config=config,
                prompt_template="{{ workflow.completion_kind }}",
                tracker=tracker,
                events=EventStore(),
                fake_codex=FakeCodexClient(),
            )
            run_record = RunRecord(
                issue_id=item.id,
                identifier=item.identifier,
                state="running",
                workspace="",
            )

            result = await runner.run(item, run_record)

            self.assertFalse(result.should_continue)
            self.assertEqual(item.state, "Todo")
            self.assertEqual(tracker.status_updates, [])

    # 函数说明：测试 Project Status 更新失败会标记 run failed，并交给调度器重试。
    async def test_completion_status_update_failure_requests_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(
                {
                    "tracker": {
                        "kind": "github_projects_v2",
                        "owner_type": "org",
                        "owner": "acme",
                        "project_number": 1,
                        "repositories": ["acme/demo"],
                        "api_token": "fake-token",
                        "active_states": ["Todo"],
                        "terminal_states": ["Done"],
                    },
                    "workspace": {"root": tmp, "checkout": {"mode": "none"}},
                    "completion_policy": {
                        "kind": "update_project_status",
                        "success_state": "Done",
                        "mark_done_after_successful_turn": True,
                    },
                }
            )
            item = _item("I_1", "acme/demo#1", blocked_by_open_count=0)
            tracker = FakeTracker([item])
            tracker.fail_status_update = True
            events = EventStore()
            runner = FakeAgentRunnerWithCodex(
                config=config,
                prompt_template="{{ issue.identifier }}",
                tracker=tracker,
                events=events,
                fake_codex=FakeCodexClient(),
            )
            run_record = RunRecord(
                issue_id=item.id,
                identifier=item.identifier,
                state="running",
                workspace="",
            )

            result = await runner.run(item, run_record)

            self.assertTrue(result.should_continue)
            self.assertEqual(run_record.state, "failed")
            self.assertIn("status update failed", run_record.last_error or "")
            self.assertTrue(
                any(
                    event.event_type == "orchestrator.completion_status_update_failed"
                    for event in events.recent()
                )
            )


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
                    "api_token": "fake-token",
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

    # 函数说明：测试 blocked_states 可配置；只有指定阶段会因为 dependencies 被跳过。
    async def test_dispatch_uses_configured_blocked_states(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                    "api_token": "fake-token",
                    "status_options": ["Ready", "Coding", "Human Review", "Shipped"],
                    "active_states": ["Ready", "Coding"],
                    "handoff_states": ["Human Review"],
                    "terminal_states": ["Shipped"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
                "blocker_policy": {"blocked_states": ["Ready"]},
                "agent": {"max_concurrent_agents": 2},
                "completion_policy": {"success_state": "Human Review"},
            }
        )
        ready = _item("I_1", "acme/demo#1", blocked_by_open_count=1)
        ready.state = "Ready"
        coding = _item("I_2", "acme/demo#2", blocked_by_open_count=1)
        coding.state = "Coding"
        release = asyncio.Event()
        orchestrator = Orchestrator(
            config=config,
            prompt_template="",
            tracker=FakeTracker([ready, coding]),
            runner_factory=lambda: FakeRunner(release),
            events=EventStore(),
        )

        await orchestrator.poll_once()

        self.assertNotIn("I_1", orchestrator.running)
        self.assertIn("I_2", orchestrator.running)
        release.set()
        await asyncio.sleep(0)


class SettingsApiTest(unittest.IsolatedAsyncioTestCase):
    """验证 Settings API 响应结构和热应用入口。"""

    # 函数说明：测试 validate/apply/state 三个接口能协同更新 generation。
    def test_settings_api_validate_apply_and_state(self) -> None:
        from fastapi.testclient import TestClient

        from symphony_github.api.server import create_app

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
        orchestrator = Orchestrator(
            config=config,
            prompt_template="old",
            tracker=FakeTracker([]),
            runner_factory=lambda: FakeRunner(asyncio.Event()),
            events=EventStore(),
        )
        client = TestClient(create_app(orchestrator))
        settings = import_workflow_text(
            """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: acme
  project_number: 2
  repositories: [acme/demo]
workspace:
  root: /tmp/github-symphony-test
---
Prompt body
"""
        ).settings

        validate_response = client.post("/api/v1/settings/validate", json={"settings": settings})
        self.assertEqual(validate_response.status_code, 200)
        self.assertTrue(validate_response.json()["ok"])

        apply_response = client.post("/api/v1/settings/apply", json={"settings": settings})
        self.assertEqual(apply_response.status_code, 400)
        self.assertIn("GitHub token 未配置", apply_response.json()["detail"])

        apply_response = client.post(
            "/api/v1/settings/apply",
            json={"settings": settings, "github_token": "fake-token"},
        )
        self.assertEqual(apply_response.status_code, 200)
        self.assertEqual(apply_response.json()["generation"], 2)

        state_response = client.get("/api/v1/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(state_response.json()["settings_generation"], 2)

    # 函数说明：测试后台 run_forever 捕获 poll 异常，不让服务任务直接退出。
    async def test_run_forever_keeps_running_after_poll_error(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                    "api_token": "fake-token",
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
                "agent": {"poll_interval_ms": 1000},
            }
        )
        orchestrator = Orchestrator(
            config=config,
            prompt_template="old",
            tracker=FailingTracker([]),
            runner_factory=lambda: FakeRunner(asyncio.Event()),
            events=EventStore(),
        )

        task = asyncio.create_task(orchestrator.run_forever())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertFalse(task.done())
        self.assertIn("boom from tracker", orchestrator.snapshot().settings_error or "")
        self.assertTrue(
            any(
                event.event_type == "orchestrator.poll_error"
                for event in orchestrator.events.recent(10)
            )
        )

        await orchestrator.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    # 函数说明：测试热重配不会取消已经运行的旧 generation agent。
    async def test_reconfigure_keeps_existing_running_agents(self) -> None:
        config = build_config(
            {
                "tracker": {
                    "kind": "github_projects_v2",
                    "owner_type": "org",
                    "owner": "acme",
                    "project_number": 1,
                    "repositories": ["acme/demo"],
                    "api_token": "fake-token",
                    "active_states": ["Todo"],
                    "terminal_states": ["Done"],
                },
                "workspace": {"root": "/tmp/github-symphony-test"},
            }
        )
        release = asyncio.Event()
        orchestrator = Orchestrator(
            config=config,
            prompt_template="old",
            tracker=FakeTracker([_item("I_1", "acme/demo#1", blocked_by_open_count=0)]),
            runner_factory=lambda: FakeRunner(release),
            events=EventStore(),
        )

        await orchestrator.poll_once()
        generation = orchestrator.reconfigure(
            config=config,
            prompt_template="new",
            tracker=FakeTracker([]),
            runner_factory=lambda: FakeRunner(release),
        )
        await orchestrator.poll_once()

        self.assertEqual(generation, 2)
        self.assertIn("I_1", orchestrator.running)
        self.assertEqual(orchestrator.snapshot().settings_generation, 2)
        release.set()
        await asyncio.sleep(0)


class DiagnosticsLoggingTest(unittest.TestCase):
    """验证持久诊断日志、脱敏和诊断包导出。"""

    # 函数说明：测试 provider key 命名的字段会按字段名脱敏，避免泄露 Codex/OpenAI key。
    def test_provider_key_names_are_redacted(self) -> None:
        redacted = redact_data(
            {
                "codex_rin977_key": "codex-secret-value",
                "openai_api_key": "openai-secret-value",
                "api_key": "generic-secret-value",
                "nested": {"custom_key": "nested-secret-value"},
                "path": "/tmp/plain-path",
                "workspace_path": "/tmp/workspace-path",
            }
        )
        serialized = str(redacted)

        self.assertNotIn("codex-secret-value", serialized)
        self.assertNotIn("openai-secret-value", serialized)
        self.assertNotIn("generic-secret-value", serialized)
        self.assertNotIn("nested-secret-value", serialized)
        self.assertIn("/tmp/plain-path", serialized)
        self.assertIn("/tmp/workspace-path", serialized)
        self.assertIn("***", serialized)

    # 函数说明：测试普通文本中的 provider key 赋值会脱敏，但 path 字段不会被误伤。
    def test_provider_key_text_is_redacted_without_redacting_path(self) -> None:
        redacted = redact_text(
            "path=/tmp/demo openai_api_key=sk-secret api_key='generic-secret' custom_key=hidden"
        )

        self.assertIn("path=/tmp/demo", redacted)
        self.assertNotIn("sk-secret", redacted)
        self.assertNotIn("generic-secret", redacted)
        self.assertNotIn("hidden", redacted)

    # 函数说明：测试事件流会写入 JSONL，且 token 不会出现在查询结果或诊断包中。
    def test_jsonl_logs_are_redacted_and_exportable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                configure_diagnostics(log_dir=tmp, level="DEBUG", retention_days=14, max_file_mb=1)
                events = EventStore()
                secret = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"

                events.append(
                    "test.error",
                    f"失败 token={secret}",
                    {
                        "identifier": "acme/demo#1",
                        "github_token": secret,
                        "Authorization": f"Bearer {secret}",
                        "error": secret,
                    },
                )

                result = query_logs(q="test.error")
                serialized = str(result)

                self.assertEqual(len(result["entries"]), 1)
                self.assertNotIn(secret, serialized)
                self.assertIn("***", serialized)

                bundle_path = export_diagnostics_bundle(
                    state={"recent_events": [event.to_dict() for event in events.recent()]},
                    settings_summary={"tracker": {"api_token": secret}},
                )
                with zipfile.ZipFile(bundle_path) as archive:
                    for name in archive.namelist():
                        content = archive.read(name).decode("utf-8")
                        self.assertNotIn(secret, content)
            finally:
                stable_dir = Path(tempfile.gettempdir()) / "github-symphony-test-logs"
                configure_diagnostics(log_dir=str(stable_dir), level="DEBUG")

    # 函数说明：测试 Logs API 能返回配置、查询日志并导出诊断包路径。
    def test_logs_api_config_query_and_export(self) -> None:
        from fastapi.testclient import TestClient

        from symphony_github.api.server import create_app

        with tempfile.TemporaryDirectory() as tmp:
            try:
                configure_diagnostics(log_dir=tmp, level="DEBUG", retention_days=14, max_file_mb=1)
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
                orchestrator = Orchestrator(
                    config=config,
                    prompt_template="old",
                    tracker=FakeTracker([]),
                    runner_factory=lambda: FakeRunner(asyncio.Event()),
                    events=EventStore(),
                )
                orchestrator.events.append(
                    "api.test",
                    "日志 API 测试事件",
                    {"identifier": "acme/demo#1"},
                )
                client = TestClient(create_app(orchestrator))

                config_response = client.get("/api/v1/logs/config")
                self.assertEqual(config_response.status_code, 200)
                self.assertEqual(config_response.json()["log_dir"], str(Path(tmp).resolve()))

                query_response = client.get("/api/v1/logs/query?q=api.test")
                self.assertEqual(query_response.status_code, 200)
                self.assertEqual(len(query_response.json()["entries"]), 1)

                export_response = client.post("/api/v1/logs/export")
                self.assertEqual(export_response.status_code, 200)
                self.assertTrue(Path(export_response.json()["path"]).exists())
            finally:
                stable_dir = Path(tempfile.gettempdir()) / "github-symphony-test-logs"
                configure_diagnostics(log_dir=str(stable_dir), level="DEBUG")


# 函数说明：创建测试 WorkItem。
def _item(issue_id: str, identifier: str, blocked_by_open_count: int) -> WorkItem:
    repository, number_text = identifier.rsplit("#", 1)
    return WorkItem(
        id=issue_id,
        project_item_id=f"PVTI_{issue_id}",
        identifier=identifier,
        kind="issue",
        title="Test",
        body=None,
        state="Todo",
        url=f"https://github.com/{identifier.replace('#', '/issues/')}",
        repository=repository,
        number=int(number_text),
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
                            {
                                "id": "PVTI_3",
                                "isArchived": False,
                                "statusValue": {"name": "Todo", "optionId": "todo-id"},
                                "priorityValue": {"number": 3},
                                "content": {
                                    "__typename": "Issue",
                                    "id": "I_3",
                                    "number": 3,
                                    "title": "Outside repo issue",
                                    "body": "Not in allowlist",
                                    "url": "https://github.com/other/repo/issues/3",
                                    "state": "OPEN",
                                    "createdAt": "2026-01-05T00:00:00Z",
                                    "updatedAt": "2026-01-06T00:00:00Z",
                                    "repository": {"nameWithOwner": "other/repo"},
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


# 函数说明：生成 Settings discovery connect GraphQL fake payload。
def _discovery_connect_payload() -> Dict:
    return {
        "data": {
            "viewer": {
                "login": "octo",
                "name": "Octo Cat",
                "organizations": {
                    "nodes": [
                        {"login": "acme", "name": "Acme Inc."},
                    ]
                },
            }
        }
    }


# 函数说明：生成 Settings discovery Project 列表 GraphQL fake payload。
def _discovery_projects_payload() -> Dict:
    return {
        "data": {
            "organization": {
                "projectsV2": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {
                            "id": "PVT_12",
                            "number": 12,
                            "title": "Roadmap",
                            "closed": False,
                            "updatedAt": "2026-04-01T00:00:00Z",
                        }
                    ],
                }
            }
        }
    }


# 函数说明：生成 Settings discovery Project 字段 GraphQL fake payload。
def _discovery_project_fields_payload() -> Dict:
    return {
        "data": {
            "organization": {
                "projectV2": {
                    "id": "PVT_12",
                    "title": "Roadmap",
                    "number": 12,
                    "fields": {
                        "nodes": [
                            {
                                "id": "PVTSSF_status",
                                "name": "Status",
                                "dataType": "SINGLE_SELECT",
                                "options": [
                                    {"id": "todo-id", "name": "Todo", "color": "GRAY"},
                                    {
                                        "id": "progress-id",
                                        "name": "In Progress",
                                        "color": "BLUE",
                                    },
                                    {"id": "done-id", "name": "Done", "color": "GREEN"},
                                ],
                            },
                            {
                                "id": "PVTF_priority",
                                "name": "Priority",
                                "dataType": "NUMBER",
                            },
                        ]
                    },
                }
            }
        }
    }


# 函数说明：生成 Settings discovery Project 仓库推断 GraphQL fake payload。
def _discovery_project_repositories_payload() -> Dict:
    return {
        "data": {
            "organization": {
                "projectV2": {
                    "items": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "content": {
                                    "__typename": "Issue",
                                    "repository": {"nameWithOwner": "acme/web"},
                                }
                            },
                            {
                                "content": {
                                    "__typename": "PullRequest",
                                    "repository": {"nameWithOwner": "acme/api"},
                                }
                            },
                            {"content": {"__typename": "DraftIssue"}},
                        ],
                    }
                }
            }
        }
    }


if __name__ == "__main__":
    unittest.main()
