"""Codex app-server JSON-RPC stdio client。"""

from __future__ import annotations

import asyncio
import json
import shlex
from asyncio.subprocess import PIPE, Process
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from symphony_github.core.config import CodexConfig
from symphony_github.core.events import EventStore


DynamicToolExecutor = Callable[[str, Any], Awaitable[Dict[str, Any]]]


class AppServerError(RuntimeError):
    """Codex app-server 通信失败。"""


@dataclass
class TurnResult:
    """一次 Codex turn 的结果摘要。"""

    thread_id: str
    turn_id: Optional[str]
    completed: bool
    final_state: Optional[str] = None


class CodexAppServerClient:
    """最小 Codex app-server 客户端。"""

    # 函数说明：保存 Codex 配置、工作区、事件流和动态工具执行器。
    def __init__(
        self,
        config: CodexConfig,
        workspace: str,
        events: EventStore,
        dynamic_tool_specs: Optional[list] = None,
        dynamic_tool_executor: Optional[DynamicToolExecutor] = None,
    ) -> None:
        self.config = config
        self.workspace = str(Path(workspace).resolve())
        self.events = events
        self.dynamic_tool_specs = dynamic_tool_specs or []
        self.dynamic_tool_executor = dynamic_tool_executor
        self.process: Optional[Process] = None
        self._next_request_id = 1
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._thread_id: Optional[str] = None
        self._active_turn_id: Optional[str] = None
        self._turn_completed: Optional[asyncio.Future] = None

    # 函数说明：启动 app-server 子进程并完成 initialize。
    async def start(self) -> None:
        if self.process is not None:
            return

        self.events.append(
            "codex.process.starting",
            "启动 Codex app-server",
            {"workspace": self.workspace, "command": self.config.command},
        )
        self.process = await asyncio.create_subprocess_shell(
            self.config.command,
            cwd=self.workspace,
            stdin=PIPE,
            stdout=PIPE,
            stderr=PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr_loop())

        await self._request(
            "initialize",
            {
                "clientInfo": {"name": "github-symphony", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        )

    # 函数说明：启动 thread 并注册 GitHub dynamic tools。
    async def start_thread(self) -> str:
        await self.start()
        params: Dict[str, Any] = {
            "cwd": self.workspace,
            "approvalPolicy": self.config.approval_policy,
            "sandbox": self.config.thread_sandbox,
            "serviceName": "github-symphony",
            "dynamicTools": self.dynamic_tool_specs,
        }

        if self.config.model:
            params["model"] = self.config.model

        response = await self._request("thread/start", params)
        thread = response.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise AppServerError("thread/start 响应缺少 thread.id")

        self._thread_id = str(thread_id)
        self.events.append(
            "codex.thread.started",
            "Codex thread 已启动",
            {"thread_id": self._thread_id, "workspace": self.workspace},
        )
        return self._thread_id

    # 函数说明：发送一次 turn/start 并等待 turn/completed。
    async def run_turn(self, prompt: str) -> TurnResult:
        thread_id = self._thread_id or await self.start_thread()
        self._turn_completed = asyncio.get_running_loop().create_future()
        response = await self._request(
            "turn/start",
            {
                "threadId": thread_id,
                "cwd": self.workspace,
                "input": [{"type": "text", "text": prompt}],
                "approvalPolicy": self.config.approval_policy,
                "sandboxPolicy": self.config.turn_sandbox_policy,
            },
        )

        turn = response.get("turn") or {}
        self._active_turn_id = turn.get("id") or self._active_turn_id
        completed_payload = await self._turn_completed
        return TurnResult(
            thread_id=thread_id,
            turn_id=self._active_turn_id,
            completed=True,
            final_state=_turn_final_state(completed_payload),
        )

    # 函数说明：停止 app-server 子进程和后台读取任务。
    async def close(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()

    # 函数说明：发送 JSON-RPC request 并等待 response。
    async def _request(self, method: str, params: Any) -> Dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return await future

    # 函数说明：写入单行 JSON-RPC 消息。
    async def _write(self, message: Dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise AppServerError("Codex app-server 尚未启动")

        data = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    # 函数说明：持续读取 stdout JSON-RPC 消息。
    async def _read_stdout_loop(self) -> None:
        if self.process is None or self.process.stdout is None:
            return

        while True:
            raw_line = await self.process.stdout.readline()
            if not raw_line:
                break

            try:
                message = json.loads(raw_line.decode("utf-8"))
            except json.JSONDecodeError:
                self.events.append(
                    "codex.protocol.invalid_json",
                    "Codex app-server 输出了非 JSON 行",
                    {"line": raw_line.decode("utf-8", errors="replace")[:500]},
                )
                continue

            await self._handle_message(message)

    # 函数说明：持续读取 stderr，并写入事件流用于诊断。
    async def _read_stderr_loop(self) -> None:
        if self.process is None or self.process.stderr is None:
            return

        while True:
            raw_line = await self.process.stderr.readline()
            if not raw_line:
                break
            text = raw_line.decode("utf-8", errors="replace").strip()
            if text:
                self.events.append("codex.stderr", "Codex stderr", {"line": text[:1000]})

    # 函数说明：根据 JSON-RPC 消息类型分派 response、notification 和 server request。
    async def _handle_message(self, message: Dict[str, Any]) -> None:
        if "id" in message and ("result" in message or "error" in message) and "method" not in message:
            self._resolve_response(message)
            return

        method = message.get("method")
        if method == "item/tool/call":
            await self._handle_dynamic_tool_call(message)
            return
        if method in APPROVAL_OR_INPUT_REQUESTS:
            await self._handle_approval_or_input_request(message)
            return

        self._handle_notification(message)

    # 函数说明：完成某个 pending request。
    def _resolve_response(self, message: Dict[str, Any]) -> None:
        request_id = message.get("id")
        future = self._pending.pop(request_id, None)
        if future is None:
            return

        if "error" in message:
            future.set_exception(AppServerError(json.dumps(message["error"], ensure_ascii=False)))
        else:
            future.set_result(message.get("result") or {})

    # 函数说明：处理 app-server 请求的动态工具调用。
    async def _handle_dynamic_tool_call(self, message: Dict[str, Any]) -> None:
        request_id = message.get("id")
        params = message.get("params") or {}
        tool = params.get("tool") or params.get("name")
        arguments = params.get("arguments")

        if self.dynamic_tool_executor is None or not tool:
            result = {
                "success": False,
                "contentItems": [
                    {"type": "inputText", "text": json.dumps({"error": "No dynamic tool executor"})}
                ],
            }
        else:
            result = await self.dynamic_tool_executor(str(tool), arguments)

        self.events.append(
            "codex.dynamic_tool.called",
            "Codex 调用了动态工具",
            {"tool": tool, "success": bool(result.get("success"))},
        )
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    # 函数说明：处理 approval、MCP elicitation 和 request_user_input 请求，默认拒绝或返回空输入。
    async def _handle_approval_or_input_request(self, message: Dict[str, Any]) -> None:
        method = str(message.get("method"))
        request_id = message.get("id")
        result = default_request_response(method)
        self.events.append(
            "codex.request.default_response",
            "Codex 请求需要外部确认，已按默认安全策略响应",
            {"method": method},
        )
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    # 函数说明：处理无需响应的 app-server notification。
    def _handle_notification(self, message: Dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params") or {}

        if method == "turn/started":
            turn = params.get("turn") or {}
            self._active_turn_id = turn.get("id") or self._active_turn_id

        if method == "turn/completed" and self._turn_completed is not None:
            if not self._turn_completed.done():
                self._turn_completed.set_result(params)

        if method:
            self.events.append(
                "codex.notification",
                f"Codex notification: {method}",
                {"method": method, "params": _compact_params(params)},
            )


# 函数说明：从 turn/completed payload 提取最终状态。
def _turn_final_state(payload: Dict[str, Any]) -> Optional[str]:
    turn = payload.get("turn") if isinstance(payload, dict) else None
    if isinstance(turn, dict):
        status = turn.get("status")
        if isinstance(status, dict):
            return status.get("type")
        if isinstance(status, str):
            return status
    return None


# 函数说明：压缩 notification payload，避免事件流保存过大的 app-server 数据。
def _compact_params(params: Dict[str, Any]) -> Dict[str, Any]:
    compacted = dict(params)

    # 逻辑说明：大型 items/turns 不适合完整塞进事件流，UI 只需要生命周期摘要。
    for key in ("items", "turns"):
        if key in compacted:
            compacted[key] = f"<{len(compacted[key])} items>"
    return compacted


# 函数说明：把命令字符串切分成 argv，仅供未来非 shell 启动策略复用。
def split_command(command: str) -> list:
    return shlex.split(command)


APPROVAL_OR_INPUT_REQUESTS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
    "applyPatchApproval",
    "execCommandApproval",
}


# 函数说明：为需要用户介入的 app-server 请求生成默认安全响应。
def default_request_response(method: str) -> Dict[str, Any]:
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": "decline"}
    if method == "item/permissions/requestApproval":
        return {"permissions": {}, "scope": "turn"}
    if method == "mcpServer/elicitation/request":
        return {"action": "decline", "content": None}
    if method == "item/tool/requestUserInput":
        return {"answers": {}}
    if method in {"applyPatchApproval", "execCommandApproval"}:
        return {"decision": "denied"}
    return {}
