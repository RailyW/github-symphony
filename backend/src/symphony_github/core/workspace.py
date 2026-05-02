"""本地工作区管理。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .config import WorkspaceCheckoutConfig, WorkspaceCheckoutRepositoryConfig, WorkspaceConfig
from .diagnostics import redact_text
from .models import WorkItem


class WorkspaceError(RuntimeError):
    """工作区创建或 hook 执行失败。"""


@dataclass
class CheckoutPlan:
    """一次内置 git checkout 的执行计划。"""

    command: list[str]
    destination: Path


class WorkspaceManager:
    """为每个 work item 创建和复用隔离工作区。"""

    # 函数说明：保存工作区根目录配置，并确保根目录路径是绝对路径。
    def __init__(self, config: WorkspaceConfig) -> None:
        self.config = config
        self.root = Path(config.root).expanduser().resolve()

    # 函数说明：准备工作区；新建时先执行内置 checkout，再执行 after_create hook。
    def prepare(self, item: WorkItem) -> str:
        workspace_name = sanitize_identifier(item.identifier)
        workspace = (self.root / workspace_name).resolve()
        _ensure_contained(self.root, workspace)

        existed = workspace.exists()
        workspace.mkdir(parents=True, exist_ok=True)

        # 逻辑说明：checkout 与 hook 都只在首次创建时执行，避免重试时覆盖 agent
        # 已完成的代码、分支和临时文件。
        if not existed:
            try:
                if self.config.checkout.mode == "clone":
                    self._run_checkout(workspace, item)
                if self.config.hooks.after_create:
                    self._run_after_create_hook(workspace, item)
            except Exception:
                # 逻辑说明：内置 checkout 流程失败时不能留下“已存在但未准备好”的工作区；
                # 否则调度器重试会跳过 checkout/hook，直接让 Codex 跑在错误目录里。
                if self.config.checkout.mode == "clone":
                    shutil.rmtree(workspace, ignore_errors=True)
                raise

        return str(workspace)

    # 函数说明：终端状态后按配置清理工作区，默认保留用于审计。
    def cleanup_if_configured(self, item: WorkItem) -> None:
        if not self.config.cleanup_terminal_workspaces:
            return

        workspace = (self.root / sanitize_identifier(item.identifier)).resolve()
        _ensure_contained(self.root, workspace)

        if workspace.exists():
            shutil.rmtree(workspace)

    # 函数说明：在工作区目录执行 after_create hook。
    def _run_after_create_hook(self, workspace: Path, item: WorkItem) -> None:
        env = os.environ.copy()
        env.update(_hook_environment(item, str(workspace)))

        # 逻辑说明：shell=True 是为了支持 WORKFLOW.md 中的多行脚本；脚本来源是用户仓库配置。
        result = subprocess.run(
            self.config.hooks.after_create,
            cwd=str(workspace),
            env=env,
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if result.returncode != 0:
            raise WorkspaceError(
                "workspace.after_create hook 失败："
                f"exit={result.returncode}, stderr={redact_text(result.stderr.strip())}"
            )

    # 函数说明：在新工作区内执行内置 git clone checkout。
    def _run_checkout(self, workspace: Path, item: WorkItem) -> None:
        plan = build_checkout_plan(self.config.checkout, workspace, item)
        if plan is None:
            return

        # 逻辑说明：自定义 path 可以是多级相对目录；git clone 会创建最终目录，
        # 但父目录必须先存在。
        plan.destination.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(_hook_environment(item, str(workspace)))
        result = subprocess.run(
            plan.command,
            cwd=str(workspace),
            env=env,
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        if result.returncode != 0:
            raise WorkspaceError(
                "workspace.checkout clone 失败："
                f"exit={result.returncode}, stderr={redact_text(result.stderr.strip())}"
            )


# 函数说明：把 GitHub identifier 转换为安全目录名。
def sanitize_identifier(identifier: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", identifier).strip("-")
    return sanitized or "work-item"


# 函数说明：生成 hook 环境变量，便于脚本知道当前任务上下文。
def _hook_environment(item: WorkItem, workspace: str) -> Dict[str, str]:
    return {
        "SYMPHONY_ISSUE_ID": item.id,
        "SYMPHONY_PROJECT_ITEM_ID": item.project_item_id,
        "SYMPHONY_IDENTIFIER": item.identifier,
        "SYMPHONY_REPOSITORY": item.repository,
        "SYMPHONY_NUMBER": str(item.number),
        "SYMPHONY_KIND": item.kind,
        "SYMPHONY_WORKSPACE": workspace,
    }


# 函数说明：根据 checkout 配置和 work item 生成 git clone 命令；非 clone 模式返回 None。
def build_checkout_plan(
    config: WorkspaceCheckoutConfig,
    workspace: Path,
    item: WorkItem,
) -> Optional[CheckoutPlan]:
    if config.mode != "clone":
        return None

    override = config.repositories.get(item.repository) or WorkspaceCheckoutRepositoryConfig()
    destination = _checkout_destination(workspace, override.path)
    destination_arg = _checkout_destination_arg(workspace, destination)
    clone_url = override.clone_url or _clone_url_for_repository(item.repository, config.protocol)
    command = ["git", "clone"]

    # 逻辑说明：depth 为 None 表示完整 clone；正整数才传给 git clone。
    if config.depth is not None:
        command.extend(["--depth", str(config.depth)])

    # 逻辑说明：branch 只在仓库覆盖中配置，避免全局分支误套到不同仓库。
    if override.branch:
        command.extend(["--branch", override.branch])

    command.extend([clone_url, destination_arg])
    return CheckoutPlan(command=command, destination=destination)


# 函数说明：把 checkout path 解析到工作区内，并拒绝任何越界路径。
def _checkout_destination(workspace: Path, checkout_path: str) -> Path:
    path_text = checkout_path.strip() or "."
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    destination = candidate.resolve()
    _ensure_contained(workspace, destination)
    return destination


# 函数说明：生成传给 git clone 的目标路径，默认 checkout 保持克隆到当前目录。
def _checkout_destination_arg(workspace: Path, destination: Path) -> str:
    workspace_resolved = workspace.resolve()
    destination_resolved = destination.resolve()
    if destination_resolved == workspace_resolved:
        return "."
    return str(destination_resolved.relative_to(workspace_resolved))


# 函数说明：根据 GitHub owner/repo 和协议生成默认 clone URL。
def _clone_url_for_repository(repository: str, protocol: str) -> str:
    owner, repo = _split_repository(repository)
    if protocol == "https":
        return f"https://github.com/{owner}/{repo}.git"
    return f"git@github.com:{owner}/{repo}.git"


# 函数说明：拆分 GitHub owner/repo 仓库名。
def _split_repository(repository: str) -> Tuple[str, str]:
    parts = repository.split("/")
    has_invalid_shape = len(parts) != 2 or not parts[0] or not parts[1]
    has_whitespace = any(character.isspace() for character in repository)
    if has_invalid_shape or has_whitespace:
        raise WorkspaceError(f"无效 GitHub 仓库名：{repository}")
    return parts[0], parts[1]


# 函数说明：校验目标路径位于根目录内，防止符号或路径拼接越界。
def _ensure_contained(root: Path, child: Path) -> Tuple[Path, Path]:
    root_resolved = root.resolve()
    child_resolved = child.resolve()

    try:
        child_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspaceError(f"工作区路径越界：{child_resolved}") from exc

    return root_resolved, child_resolved
