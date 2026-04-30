"""本地 FastAPI 控制面。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from symphony_github.core.orchestrator import Orchestrator
from symphony_github.core.runtime import build_runtime_components
from symphony_github.core.settings import (
    default_app_settings,
    export_workflow_text,
    import_workflow_text,
    normalize_app_settings,
)


# 函数说明：创建 FastAPI app；把导入放在函数内，使基础测试不依赖 FastAPI。
def create_app(orchestrator: Orchestrator):
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
    except Exception as exc:  # noqa: BLE001 - 运行入口需要清晰提示缺依赖。
        raise RuntimeError(
            "缺少 FastAPI，请先在 backend 中执行 python -m pip install -e ."
        ) from exc

    app = FastAPI(title="GitHub Symphony", version="0.1.0")

    # 逻辑说明：Electron 打包后通过 file:// 加载 React 静态页面，
    # 浏览器安全模型会把 file:// 到 http://127.0.0.1 的请求视为跨源请求。
    # 后端只绑定本机地址，且不使用浏览器 cookie，因此这里允许本地桌面前端跨源访问 API。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    # 函数说明：返回当前运行状态快照。
    @app.get("/api/v1/state")
    async def get_state() -> Dict[str, Any]:
        return orchestrator.snapshot().to_dict()

    # 函数说明：返回后端内置的默认 App 设置，供 Electron 首次启动兜底使用。
    @app.get("/api/v1/settings/default")
    async def default_settings() -> Dict[str, Any]:
        return {"settings": default_app_settings()}

    # 函数说明：校验 App 设置，返回归一化后的非敏感配置或错误列表。
    @app.post("/api/v1/settings/validate")
    async def validate_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            document = normalize_app_settings(_payload_settings(payload), github_token=None)
        except Exception as exc:  # noqa: BLE001 - API 需要把校验错误作为普通响应返回 UI。
            return {"ok": False, "errors": [str(exc)]}
        return {"ok": True, "errors": [], "normalized": document.settings}

    # 函数说明：热应用 App 设置；当前运行中的 agent 不会被取消。
    @app.post("/api/v1/settings/apply")
    async def apply_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            document = normalize_app_settings(
                _payload_settings(payload),
                github_token=payload.get("github_token"),
            )
            # 逻辑说明：GitHub Projects v2 的 GraphQL API 必须带 token 才能稳定读取。
            # validate 允许无 token，方便用户先整理表单；apply 是运行态入口，
            # 因此这里明确失败，避免后台 poll 进入未认证请求并让调度循环报隐晦错误。
            if not document.config.tracker.api_token:
                raise ValueError(
                    "GitHub token 未配置：请在 Settings / GitHub Project 保存 PAT，"
                    "或设置 GITHUB_TOKEN 后重新应用。"
                )
            runtime = build_runtime_components(
                document.config,
                document.prompt_template,
                orchestrator.events,
            )
            generation = orchestrator.reconfigure(
                runtime.config,
                runtime.prompt_template,
                runtime.tracker,
                runtime.runner_factory,
            )
        except Exception as exc:  # noqa: BLE001 - 应用失败要进入 state，便于 UI 和事件流观测。
            message = str(exc)
            orchestrator.mark_settings_error(message)
            raise HTTPException(status_code=400, detail=message) from exc

        return {"status": "applied", "generation": generation}

    # 函数说明：把 WORKFLOW.md 文本导入为 App settings，不保存 token。
    @app.post("/api/v1/settings/import-workflow")
    async def import_workflow(payload: Dict[str, Any]) -> Dict[str, Any]:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPException(status_code=400, detail="text 必须是非空 WORKFLOW.md 文本")
        try:
            result = import_workflow_text(text)
        except Exception as exc:  # noqa: BLE001 - 导入错误需要原样显示给用户。
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "settings": result.settings,
            "token_hint": result.token_hint,
            "warnings": result.warnings,
        }

    # 函数说明：把 App settings 导出为 WORKFLOW.md 文本，永远只写 token 占位符。
    @app.post("/api/v1/settings/export-workflow")
    async def export_workflow(payload: Dict[str, Any]) -> Dict[str, str]:
        try:
            text = export_workflow_text(_payload_settings(payload))
        except Exception as exc:  # noqa: BLE001 - 导出前仍需复用配置校验。
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"text": text}

    # 函数说明：按 issue id 返回本地已知详情和相关事件。
    @app.get("/api/v1/issues/{issue_id}")
    async def get_issue(issue_id: str) -> Dict[str, Any]:
        running = orchestrator.running.get(issue_id)
        candidate = next(
            (item for item in orchestrator._last_candidates if item.id == issue_id),
            None,
        )

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


# 函数说明：从 API payload 中读取 settings 对象，并给错误请求提供一致提示。
def _payload_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("settings 必须是对象")
    return settings


# 函数说明：运行 uvicorn server。
def run_app(orchestrator: Orchestrator, host: str, port: int) -> None:
    try:
        import uvicorn
    except Exception as exc:  # noqa: BLE001 - 运行入口需要清晰提示缺依赖。
        raise RuntimeError(
            "缺少 uvicorn，请先在 backend 中执行 python -m pip install -e ."
        ) from exc

    app = create_app(orchestrator)
    uvicorn.run(app, host=host, port=port)
