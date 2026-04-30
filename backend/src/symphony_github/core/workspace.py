"""本地工作区管理。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Tuple

from .config import WorkspaceConfig
from .models import WorkItem


class WorkspaceError(RuntimeError):
    """工作区创建或 hook 执行失败。"""


class WorkspaceManager:
    """为每个 work item 创建和复用隔离工作区。"""

    # 函数说明：保存工作区根目录配置，并确保根目录路径是绝对路径。
    def __init__(self, config: WorkspaceConfig) -> None:
        self.config = config
        self.root = Path(config.root).expanduser().resolve()

    # 函数说明：准备工作区；新建时执行 after_create hook。
    def prepare(self, item: WorkItem) -> str:
        workspace_name = sanitize_identifier(item.identifier)
        workspace = (self.root / workspace_name).resolve()
        _ensure_contained(self.root, workspace)

        existed = workspace.exists()
        workspace.mkdir(parents=True, exist_ok=True)

        # 逻辑说明：hook 只在首次创建时执行，避免重试时覆盖 agent 已完成的文件。
        if not existed and self.config.hooks.after_create:
            self._run_after_create_hook(workspace, item)

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
                f"exit={result.returncode}, stderr={result.stderr.strip()}"
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


# 函数说明：校验目标路径位于根目录内，防止符号或路径拼接越界。
def _ensure_contained(root: Path, child: Path) -> Tuple[Path, Path]:
    root_resolved = root.resolve()
    child_resolved = child.resolve()

    try:
        child_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspaceError(f"工作区路径越界：{child_resolved}") from exc

    return root_resolved, child_resolved
