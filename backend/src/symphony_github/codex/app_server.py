"""Codex app-server JSON-RPC stdio client。"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from asyncio.subprocess import PIPE, Process
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from symphony_github.core.config import HIGH_TRUST_APPROVAL_PRESETS, CodexConfig
from symphony_github.core.diagnostics import redact_text
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

    # 函数说明：保存 Codex 配置、工作区、事件流、GitHub token 和动态工具执行器。
    def __init__(
        self,
        config: CodexConfig,
        workspace: str,
        events: EventStore,
        github_token: Optional[str] = None,
        dynamic_tool_specs: Optional[list] = None,
        dynamic_tool_executor: Optional[DynamicToolExecutor] = None,
    ) -> None:
        self.config = config
        self.workspace = str(Path(workspace).resolve())
        self.events = events
        self.github_token = github_token
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
            env=codex_subprocess_env(self.github_token),
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

        # 逻辑说明：如果 app-server 进程在响应 initialize/thread/turn 前退出，
        # stdout 会先关闭。这里主动失败所有等待中的 request，避免 runner 永远卡住。
        self._fail_pending(
            AppServerError("Codex app-server stdout 已关闭，可能是命令或运行环境无效")
        )

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
        if (
            "id" in message
            and ("result" in message or "error" in message)
            and "method" not in message
        ):
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

    # 函数说明：让所有等待中的 JSON-RPC request/turn 以同一个错误结束。
    def _fail_pending(self, error: Exception) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

        # 逻辑说明：run_turn 可能已经拿到 turn/start 响应、正在等待 turn/completed；
        # 进程退出时同样要解除等待，让调度器能够进入重试/报错路径。
        if self._turn_completed is not None and not self._turn_completed.done():
            self._turn_completed.set_exception(error)

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
            try:
                result = await self.dynamic_tool_executor(str(tool), arguments)
            except Exception as exc:  # noqa: BLE001 - app-server 边界必须把工具异常转成响应。
                # 逻辑说明：动态工具内部通常会自行返回结构化失败；这里兜底保护
                # stdout 读取循环，避免某个工具 bug 让整个 Codex 会话失去响应。
                result = {
                    "success": False,
                    "contentItems": [
                        {
                            "type": "inputText",
                            "text": json.dumps(
                                {"error": f"Dynamic tool failed: {redact_text(str(exc))}"},
                                ensure_ascii=False,
                            ),
                        }
                    ],
                }

        self.events.append(
            "codex.dynamic_tool.called",
            "Codex 调用了动态工具",
            {"tool": tool, "success": bool(result.get("success"))},
        )
        await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    # 函数说明：处理 approval、MCP elicitation 和 request_user_input 请求。
    async def _handle_approval_or_input_request(self, message: Dict[str, Any]) -> None:
        method = str(message.get("method"))
        request_id = message.get("id")
        params = message.get("params") or {}
        if approval_policy_is_never(self.config.approval_policy):
            result = auto_approved_request_response(method, params)
            self.events.append(
                "codex.request.auto_approved",
                "Codex 请求需要外部确认，已按高信任 approval policy 自动响应",
                {
                    "method": method,
                    "decision": result.get("decision") or result.get("action") or "answered",
                },
            )
        else:
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


# 函数说明：为 Codex 子进程构造环境变量，补上 GUI App 常缺失的 Node/Codex 路径和 GitHub token。
def codex_subprocess_env(github_token: Optional[str] = None) -> Dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = build_codex_path(env.get("PATH", ""))

    # 逻辑说明：agent 只能拿到当前 tracker 显式解析出的 token。先清掉父进程可能
    # 继承来的 GitHub token，避免 GUI/CLI 启动环境意外扩大 agent 权限边界。
    env.pop("GITHUB_TOKEN", None)
    env.pop("GH_TOKEN", None)

    if github_token:
        # 逻辑说明：只把 token 放入子进程环境，不写入事件 payload；EventStore/diagnostics
        # 仍会对意外出现的 token 字段和 PAT 文本做最后一道脱敏。
        env["GITHUB_TOKEN"] = github_token
        env["GH_TOKEN"] = github_token
    return env


# 函数说明：合并现有 PATH 和常见开发工具路径，供测试和 Electron 打包环境复用。
def build_codex_path(existing_path: str) -> str:
    entries = _candidate_path_entries()
    entries.extend([entry for entry in existing_path.split(os.pathsep) if entry])
    return os.pathsep.join(_dedupe_existing_path_entries(entries))


# 函数说明：返回 macOS GUI 环境中常见但未必继承到的命令目录。
def _candidate_path_entries() -> list[str]:
    home = Path.home()
    entries = [
        str(home / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]

    # 逻辑说明：通过 nvm 安装的 codex/npm/node 常位于 ~/.nvm/versions/node/<version>/bin。
    # GUI 启动的 Electron 通常不会加载 shell rc 文件，因此这里主动发现所有版本。
    nvm_node_root = home / ".nvm" / "versions" / "node"
    if nvm_node_root.exists():
        entries = [
            str(path / "bin")
            for path in sorted(nvm_node_root.iterdir(), reverse=True)
            if path.is_dir()
        ] + entries
    return entries


# 函数说明：去重并保留存在的 PATH 目录，避免传入过长或无效的环境变量。
def _dedupe_existing_path_entries(entries: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for entry in entries:
        normalized = str(Path(entry).expanduser())
        if normalized in seen or not Path(normalized).exists():
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


APPROVAL_OR_INPUT_REQUESTS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
    "applyPatchApproval",
    "execCommandApproval",
}

NON_INTERACTIVE_TOOL_INPUT_ANSWER = (
    "This is a non-interactive session. Operator input is unavailable."
)


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


# 函数说明：判断 approval policy 是否明确进入高信任 unattended 模式。
def approval_policy_is_never(approval_policy: Any) -> bool:
    if approval_policy == "never":
        return True
    if isinstance(approval_policy, str):
        return _normalize_approval_preset_name(approval_policy) in HIGH_TRUST_APPROVAL_PRESETS
    if isinstance(approval_policy, dict):
        for key in ("preset", "autonomy_preset", "mode"):
            candidate = approval_policy.get(key)
            if (
                isinstance(candidate, str)
                and _normalize_approval_preset_name(candidate) in HIGH_TRUST_APPROVAL_PRESETS
            ):
                return True
    return False


# 函数说明：归一化 approval preset 名称，确保 app-server 直接收到 preset 时也走高信任路径。
def _normalize_approval_preset_name(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


# 函数说明：为高信任 approval policy 生成自动响应；只在用户显式配置 never/preset 时调用。
def auto_approved_request_response(method: str, params: Any) -> Dict[str, Any]:
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        return {"decision": "acceptForSession"}
    if method in {"applyPatchApproval", "execCommandApproval"}:
        return {"decision": "approved_for_session"}
    if method == "item/permissions/requestApproval":
        permissions = params.get("permissions") if isinstance(params, dict) else {}
        return {
            "permissions": permissions if isinstance(permissions, dict) else {},
            "scope": "session",
        }
    if method == "item/tool/requestUserInput":
        return {"answers": _tool_user_input_answers(params)}
    return default_request_response(method)


# 函数说明：给工具 user-input 请求选择可继续的答案；approval prompt 优先选 session 级批准。
def _tool_user_input_answers(params: Any) -> Dict[str, Dict[str, list[str]]]:
    if not isinstance(params, dict) or not isinstance(params.get("questions"), list):
        return {}

    answers: Dict[str, Dict[str, list[str]]] = {}
    for question in params["questions"]:
        if not isinstance(question, dict) or not isinstance(question.get("id"), str):
            continue
        question_id = question["id"]
        label = _tool_user_input_option_label(question.get("options"))
        answers[question_id] = {"answers": [label or NON_INTERACTIVE_TOOL_INPUT_ANSWER]}
    return answers


# 函数说明：从选项中挑选最适合 unattended 继续执行的标签。
def _tool_user_input_option_label(options: Any) -> Optional[str]:
    if not isinstance(options, list):
        return None

    labels = [
        str(option.get("label")).strip()
        for option in options
        if isinstance(option, dict) and isinstance(option.get("label"), str)
    ]
    for preferred in ("Approve this Session", "Approve Once"):
        if preferred in labels:
            return preferred
    for label in labels:
        normalized = label.lower()
        if normalized.startswith(("approve", "allow")):
            return label
    for label in labels:
        normalized = label.lower()
        if not normalized.startswith(("deny", "decline", "cancel", "stop")):
            return label
    return None
