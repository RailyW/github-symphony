"""GitHub Symphony 命令行入口。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from symphony_github.api.server import run_app
from symphony_github.core.events import EventStore
from symphony_github.core.orchestrator import Orchestrator
from symphony_github.core.runner import AgentRunner
from symphony_github.core.workflow import load_workflow
from symphony_github.integrations.github.client import GitHubClient
from symphony_github.integrations.github.dynamic_tools import GitHubDynamicTools
from symphony_github.integrations.github.tracker import GitHubProjectsV2Tracker


# 函数说明：CLI 主入口，负责解析参数并分派子命令。
def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        return run_command(args)
    if args.command == "doctor":
        return doctor_command(args)
    if args.command == "init-workflow":
        return init_workflow_command(args)

    parser.print_help()
    return 1


# 函数说明：构建 argparse 参数结构。
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="symphony-github")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run GitHub Symphony service")
    run_parser.add_argument("workflow", nargs="?", default="WORKFLOW.md")
    run_parser.add_argument("--host", default="127.0.0.1")
    run_parser.add_argument("--port", type=int, default=8765)
    run_parser.add_argument("--log-level", default="info")

    subparsers.add_parser("doctor", help="Check local runtime requirements")

    init_parser = subparsers.add_parser("init-workflow", help="Create WORKFLOW.md example")
    init_parser.add_argument("--tracker", default="github_projects_v2")
    init_parser.add_argument("--path", default="WORKFLOW.md")
    init_parser.add_argument("--force", action="store_true")

    return parser


# 函数说明：执行 run 子命令，装配 tracker、runner 和 API server。
def run_command(args: argparse.Namespace) -> int:
    workflow = load_workflow(args.workflow)
    events = EventStore()
    client = GitHubClient(
        token=workflow.config.tracker.api_token,
        api_base_url=workflow.config.tracker.api_base_url,
        graphql_url=workflow.config.tracker.graphql_url,
    )
    tracker = GitHubProjectsV2Tracker(
        config=workflow.config.tracker,
        blocker_policy=workflow.config.blocker_policy,
        client=client,
        events=events,
    )
    github_tools = GitHubDynamicTools(client, workflow.config.tracker, workflow.config.tools.github)

    # 函数说明：为每个 dispatch 创建新的 runner，避免跨任务共享 Codex client 状态。
    def runner_factory() -> AgentRunner:
        return AgentRunner(
            config=workflow.config,
            prompt_template=workflow.prompt_template,
            tracker=tracker,
            events=events,
            github_tools=github_tools,
        )

    orchestrator = Orchestrator(
        config=workflow.config,
        prompt_template=workflow.prompt_template,
        tracker=tracker,
        runner_factory=runner_factory,
        events=events,
    )
    run_app(orchestrator, host=args.host, port=args.port)
    return 0


# 函数说明：执行 doctor 子命令，检查本地环境。
def doctor_command(args: argparse.Namespace) -> int:
    checks = {
        "python": sys.version.split()[0],
        "node": _command_version("node", "--version"),
        "npm": _command_version("npm", "--version"),
        "codex": _command_version("codex", "--version"),
        "gh": _command_version("gh", "--version"),
        "GITHUB_TOKEN": "set" if os.environ.get("GITHUB_TOKEN") else "missing",
    }

    for name, value in checks.items():
        print(f"{name}: {value}")

    # 逻辑说明：缺少 gh 不算失败，因为设计上 gh CLI 只是可选辅助。
    required_missing = [
        name
        for name in ("node", "npm", "codex")
        if not checks[name] or checks[name] == "missing"
    ]
    return 1 if required_missing else 0


# 函数说明：生成 WORKFLOW.md 示例。
def init_workflow_command(args: argparse.Namespace) -> int:
    if args.tracker != "github_projects_v2":
        print("目前只支持 --tracker github_projects_v2", file=sys.stderr)
        return 1

    target = Path(args.path)
    if target.exists() and not args.force:
        print(f"{target} 已存在；如需覆盖请传 --force", file=sys.stderr)
        return 1

    source = Path(__file__).resolve().parents[3] / "WORKFLOW.example.md"
    if source.exists():
        content = source.read_text(encoding="utf-8")
    else:
        content = _embedded_workflow_example()

    target.write_text(content, encoding="utf-8")
    print(f"已写入 {target}")
    return 0


# 函数说明：读取命令版本，命令不存在时返回 missing。
def _command_version(command: str, *args: str) -> str:
    if shutil.which(command) is None:
        return "missing"

    result = subprocess.run(
        [command, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return (result.stdout or "").splitlines()[0] if result.stdout else "unknown"


# 函数说明：当包内无法定位根目录示例时使用内嵌最小模板。
def _embedded_workflow_example() -> str:
    return """---
tracker:
  kind: github_projects_v2
  owner_type: org
  owner: your-org
  project_number: 12
  repositories: [your-org/your-repo]
  api_token: $GITHUB_TOKEN
workspace:
  root: ~/code/github-symphony-workspaces
---
请处理 `{{ issue.identifier }}`。
"""


# 入口说明：脚本直接运行时返回进程退出码。
if __name__ == "__main__":
    raise SystemExit(main())
