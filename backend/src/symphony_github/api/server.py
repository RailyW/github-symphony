"""本地 FastAPI 控制面。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from symphony_github.core.orchestrator import Orchestrator


# 函数说明：创建 FastAPI app；把导入放在函数内，使基础测试不依赖 FastAPI。
def create_app(orchestrator: Orchestrator):
    try:
        from fastapi import FastAPI, HTTPException
    except Exception as exc:  # noqa: BLE001 - 运行入口需要清晰提示缺依赖。
        raise RuntimeError("缺少 FastAPI，请先在 backend 中执行 python -m pip install -e .") from exc

    app = FastAPI(title="GitHub Symphony", version="0.1.0")

    # 函数说明：返回当前运行状态快照。
    @app.get("/api/v1/state")
    async def get_state() -> Dict[str, Any]:
        return orchestrator.snapshot().to_dict()

    # 函数说明：按 issue id 返回本地已知详情和相关事件。
    @app.get("/api/v1/issues/{issue_id}")
    async def get_issue(issue_id: str) -> Dict[str, Any]:
        running = orchestrator.running.get(issue_id)
        candidate = next((item for item in orchestrator._last_candidates if item.id == issue_id), None)

        if running is None and candidate is None:
            raise HTTPException(status_code=404, detail="issue not found in local state")

        return {
            "issue": candidate.to_dict() if candidate is not None else None,
            "run": running.to_dict() if running is not None else None,
            "events": [
                event.to_dict()
                for event in orchestrator.events.recent(200)
                if event.payload.get("issue_id") == issue_id
                or event.payload.get("identifier") == (candidate.identifier if candidate else None)
            ],
        }

    # 函数说明：触发一次调度器刷新。
    @app.post("/api/v1/refresh")
    async def refresh() -> Dict[str, str]:
        orchestrator.trigger_refresh()
        return {"status": "queued"}

    # 函数说明：重启某个本地 run。
    @app.post("/api/v1/runs/{issue_id}/restart")
    async def restart(issue_id: str) -> Dict[str, Any]:
        restarted = await orchestrator.restart_run(issue_id)
        return {"status": "restarted" if restarted else "not_running_or_not_dispatchable"}

    # 函数说明：停止某个本地 run，不写 GitHub。
    @app.post("/api/v1/runs/{issue_id}/stop")
    async def stop(issue_id: str) -> Dict[str, Any]:
        stopped = await orchestrator.stop_run(issue_id)
        return {"status": "stopped" if stopped else "not_running"}

    # 函数说明：按 cursor 增量读取事件。
    @app.get("/api/v1/events")
    async def events(cursor: Optional[int] = None) -> Dict[str, Any]:
        records = orchestrator.events.since(cursor)
        next_cursor = records[-1].cursor if records else cursor
        return {"events": [record.to_dict() for record in records], "next_cursor": next_cursor}

    # 函数说明：应用启动时创建调度器后台任务。
    @app.on_event("startup")
    async def startup() -> None:
        app.state.orchestrator_task = asyncio.create_task(orchestrator.run_forever())

    # 函数说明：应用关闭时停止调度器。
    @app.on_event("shutdown")
    async def shutdown() -> None:
        await orchestrator.stop()
        task = getattr(app.state, "orchestrator_task", None)
        if task is not None:
            task.cancel()

    return app


# 函数说明：运行 uvicorn server。
def run_app(orchestrator: Orchestrator, host: str, port: int) -> None:
    try:
        import uvicorn
    except Exception as exc:  # noqa: BLE001 - 运行入口需要清晰提示缺依赖。
        raise RuntimeError("缺少 uvicorn，请先在 backend 中执行 python -m pip install -e .") from exc

    app = create_app(orchestrator)
    uvicorn.run(app, host=host, port=port)
