"""内存事件流。"""

from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .models import EventRecord


class EventStore:
    """保存最近事件并提供游标分页。"""

    # 函数说明：初始化固定容量事件队列，避免长期运行时无限占用内存。
    def __init__(self, max_events: int = 1000) -> None:
        self._events: Deque[EventRecord] = deque(maxlen=max_events)
        self._next_cursor = 1

    # 函数说明：追加事件并返回记录，调用方可以立即把 cursor 写入运行状态。
    def append(
        self,
        event_type: str,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> EventRecord:
        # 逻辑说明：payload 默认使用新字典，避免多个事件共享可变对象。
        event = EventRecord(
            cursor=self._next_cursor,
            event_type=event_type,
            message=message,
            payload=payload or {},
        )
        self._events.append(event)
        self._next_cursor += 1
        return event

    # 函数说明：读取最近事件，默认返回所有仍保存在队列中的事件。
    def recent(self, limit: Optional[int] = None) -> List[EventRecord]:
        events = list(self._events)

        # 逻辑说明：limit 为 None 时保持完整队列；否则只取尾部最近事件。
        if limit is None:
            return events
        return events[-limit:]

    # 函数说明：按照 cursor 返回后续事件，用于前端轮询增量日志。
    def since(self, cursor: Optional[int] = None, limit: int = 200) -> List[EventRecord]:
        # 逻辑说明：cursor 缺失时从当前队列起点开始返回。
        if cursor is None:
            return list(self._events)[-limit:]

        # 逻辑说明：游标语义是“返回大于 cursor 的事件”。
        events = [event for event in self._events if event.cursor > cursor]
        return events[:limit]

    # 函数说明：暴露下一个 cursor，方便 UI 初始化增量轮询。
    @property
    def next_cursor(self) -> int:
        return self._next_cursor
