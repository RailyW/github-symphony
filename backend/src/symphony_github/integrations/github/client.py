"""GitHub HTTP client。"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


class GitHubClientError(RuntimeError):
    """GitHub API 调用失败。"""

    # 函数说明：保留状态码和响应体，方便 tracker 决定是否降级。
    def __init__(self, message: str, status: Optional[int] = None, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class GitHubClient:
    """基于标准库的 GitHub GraphQL/REST client。"""

    token: Optional[str]
    api_base_url: str = "https://api.github.com"
    graphql_url: str = "https://api.github.com/graphql"

    # 函数说明：异步执行 GraphQL 请求。
    async def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"query": query, "variables": variables or {}}
        response = await asyncio.to_thread(
            self._request_json,
            "POST",
            self.graphql_url,
            payload,
        )

        # 逻辑说明：GraphQL 层错误也视为失败，但保留原始响应给上层诊断。
        if response.get("errors"):
            raise GitHubClientError("GitHub GraphQL 返回 errors", body=json.dumps(response))
        return response

    # 函数说明：异步执行 REST 请求，path 必须由上层确认是安全相对路径。
    async def rest(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = self._rest_url(path, query)
        return await asyncio.to_thread(self._request_json, method.upper(), url, body)

    # 函数说明：拼接 REST URL。
    def _rest_url(self, path: str, query: Optional[Dict[str, Any]]) -> str:
        if not path.startswith("/"):
            raise GitHubClientError("GitHub REST path 必须以 / 开头")

        base = self.api_base_url.rstrip("/")
        url = f"{base}{path}"

        # 逻辑说明：query 只允许对象，由 urllib 负责 URL 编码。
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        return url

    # 函数说明：同步 HTTP JSON 请求，供 asyncio.to_thread 调用。
    def _request_json(self, method: str, url: str, body: Optional[Dict[str, Any]]) -> Any:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "github-symphony/0.1.0",
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - URL 已由配置和 allowlist 控制。
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise GitHubClientError(
                f"GitHub API HTTP {exc.code}",
                status=exc.code,
                body=raw,
            ) from exc
        except urllib.error.URLError as exc:
            raise GitHubClientError(f"GitHub API 网络错误：{exc.reason}") from exc


# 函数说明：脱敏文本中的 token，避免日志或事件泄露凭据。
def redact_token(text: str, token: Optional[str]) -> str:
    if not token:
        return text
    return text.replace(token, "***")
