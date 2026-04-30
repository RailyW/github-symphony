"""核心数据模型。

本模块只使用标准库 dataclass，避免基础模型依赖 Web 框架或第三方校验库。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class WorkItem:
    """归一化后的 GitHub 工作项。"""

    id: str
    project_item_id: str
    identifier: str
    kind: str
    title: str
    body: Optional[str]
    state: str
    url: str
    repository: str
    number: int
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    priority: Optional[float] = None
    blocked_by_open_count: Optional[int] = None

    # 函数说明：把工作项转换为普通字典，方便 API、事件和测试复用。
    def to_dict(self) -> Dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass
class EventRecord:
    """内存事件流中的单条事件。"""

    cursor: int
    event_type: str
    message: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # 函数说明：把事件转换为可 JSON 序列化的字典。
    def to_dict(self) -> Dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass
class RunRecord:
    """单个 Codex agent 运行状态。"""

    issue_id: str
    identifier: str
    state: str
    workspace: str
    attempt: int = 0
    thread_id: Optional[str] = None
    turn_id: Optional[str] = None
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_error: Optional[str] = None

    # 函数说明：刷新更新时间，确保 UI 能看到运行记录最近变更。
    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

    # 函数说明：把运行状态转换为 API 响应字典。
    def to_dict(self) -> Dict[str, Any]:
        return dataclass_to_dict(self)


@dataclass
class StateSnapshot:
    """服务当前状态快照。"""

    service: str
    workflow_path: Optional[str]
    config_error: Optional[str]
    running: List[RunRecord]
    candidates: List[WorkItem]
    recent_events: List[EventRecord]
    last_poll_at: Optional[str] = None
    settings_generation: int = 1
    settings_error: Optional[str] = None

    # 函数说明：把快照转换为 API 层可直接返回的字典。
    def to_dict(self) -> Dict[str, Any]:
        return dataclass_to_dict(self)


# 函数说明：递归转换 dataclass、列表和字典，保证 FastAPI 或测试拿到纯 Python 数据。
def dataclass_to_dict(value: Any) -> Any:
    # 逻辑说明：dataclass 先通过 asdict 展开，避免泄露对象内部状态。
    if is_dataclass(value):
        return asdict(value)

    # 逻辑说明：列表逐项递归，支持嵌套 WorkItem/EventRecord。
    if isinstance(value, list):
        return [dataclass_to_dict(item) for item in value]

    # 逻辑说明：字典逐值递归，保留原始 key 以兼容 GitHub payload。
    if isinstance(value, dict):
        return {key: dataclass_to_dict(item) for key, item in value.items()}

    return value
