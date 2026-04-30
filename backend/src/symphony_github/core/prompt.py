"""Prompt 渲染。"""

from __future__ import annotations

import re
from dataclasses import is_dataclass, asdict
from typing import Any, Dict


class PromptRenderError(RuntimeError):
    """Prompt 渲染失败。"""


# 函数说明：渲染 prompt 模板，优先使用 Jinja2 StrictUndefined。
def render_prompt(template: str, context: Dict[str, Any]) -> str:
    try:
        from jinja2 import StrictUndefined, Template  # type: ignore
    except Exception:  # noqa: BLE001 - Jinja2 是正式依赖，但基础测试允许缺失。
        return _render_simple_template(template, context)

    try:
        return Template(template, undefined=StrictUndefined).render(**_normalize_context(context))
    except Exception as exc:  # noqa: BLE001 - 需要统一成领域错误。
        raise PromptRenderError(f"Prompt 渲染失败：{exc}") from exc


# 函数说明：标准库 fallback，只支持 `{{ dotted.path }}` 占位符且保持严格缺变量。
def _render_simple_template(template: str, context: Dict[str, Any]) -> str:
    normalized = _normalize_context(context)

    # 逻辑说明：使用正则定位简单变量表达式，不支持过滤器和控制结构。
    pattern = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_\.]*)\s*}}")

    # 函数说明：替换单个模板变量，缺失时抛出严格错误。
    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        value = _lookup_path(normalized, path)
        return "" if value is None else str(value)

    return pattern.sub(replace, template)


# 函数说明：把 dataclass 上下文转换为字典，便于模板通过属性名访问。
def _normalize_context(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return {key: _normalize_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_context(item) for item in value]
    return value


# 函数说明：按照点路径在字典中查找变量。
def _lookup_path(context: Dict[str, Any], path: str) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        raise PromptRenderError(f"Prompt 模板变量不存在：{path}")
    return current
