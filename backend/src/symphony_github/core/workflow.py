"""WORKFLOW.md 加载与热重载支持。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import SymphonyConfig, build_config


@dataclass
class WorkflowDocument:
    """解析后的 WORKFLOW.md 文档。"""

    path: str
    raw_config: Dict[str, Any]
    config: SymphonyConfig
    prompt_template: str


class WorkflowStore:
    """缓存 last-known-good workflow，解析失败时保留旧配置。"""

    # 函数说明：创建工作流缓存，path 是当前服务监听的 WORKFLOW.md。
    def __init__(self, path: str) -> None:
        self.path = str(Path(path).expanduser().resolve())
        self.current: Optional[WorkflowDocument] = None
        self.last_error: Optional[str] = None
        self._last_mtime: Optional[float] = None

    # 函数说明：强制加载 WORKFLOW.md，启动阶段必须成功。
    def load_initial(self) -> WorkflowDocument:
        document = load_workflow(self.path)
        self.current = document
        self.last_error = None
        self._last_mtime = Path(self.path).stat().st_mtime
        return document

    # 函数说明：如果文件修改时间变化则尝试重载，失败时返回旧配置并记录错误。
    def reload_if_changed(self) -> Optional[WorkflowDocument]:
        path = Path(self.path)
        mtime = path.stat().st_mtime

        # 逻辑说明：mtime 未变化时避免重复解析，降低轮询成本。
        if self._last_mtime is not None and mtime <= self._last_mtime:
            return self.current

        try:
            document = load_workflow(self.path)
        except Exception as exc:  # noqa: BLE001 - 需要保留解析错误给 UI。
            self.last_error = str(exc)
            self._last_mtime = mtime
            return self.current

        self.current = document
        self.last_error = None
        self._last_mtime = mtime
        return document


# 函数说明：读取并解析 WORKFLOW.md。
def load_workflow(path: str) -> WorkflowDocument:
    workflow_path = str(Path(path).expanduser().resolve())
    text = Path(workflow_path).read_text(encoding="utf-8")
    raw_config, prompt_template = split_front_matter(text)
    config = build_config(raw_config, workflow_path=workflow_path)
    return WorkflowDocument(
        path=workflow_path,
        raw_config=raw_config,
        config=config,
        prompt_template=prompt_template,
    )


# 函数说明：拆分 YAML front matter 和 Markdown prompt body。
def split_front_matter(text: str) -> Tuple[Dict[str, Any], str]:
    # 逻辑说明：没有 front matter 时配置为空对象，prompt 使用完整文本。
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break

    if end_index is None:
        raise ValueError("WORKFLOW.md front matter 缺少结束分隔符 ---")

    yaml_text = "\n".join(lines[1:end_index])
    prompt = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return parse_yaml_mapping(yaml_text), prompt


# 函数说明：解析 YAML 字符串，优先使用 PyYAML，缺失时使用受限 fallback。
def parse_yaml_mapping(text: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception:  # noqa: BLE001 - PyYAML 是可选依赖。
        yaml = None

    if yaml is not None:
        loaded = yaml.safe_load(text) or {}
        if not isinstance(loaded, dict):
            raise ValueError("WORKFLOW.md front matter 必须是 YAML 对象")
        return loaded

    loaded, next_index = _parse_mapping(_preprocess_yaml_lines(text), 0, 0)
    if next_index < len(_preprocess_yaml_lines(text)):
        raise ValueError("fallback YAML 解析器未能消费完整 front matter")
    return loaded


# 函数说明：预处理 YAML 行，保留缩进并移除空行和整行注释。
def _preprocess_yaml_lines(text: str) -> List[str]:
    result: List[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()

        # 逻辑说明：fallback 解析器只跳过整行注释，不处理字符串内联注释。
        if not stripped or stripped.startswith("#"):
            continue
        result.append(raw_line.rstrip("\n"))
    return result


# 函数说明：解析指定缩进层级的 YAML mapping。
def _parse_mapping(lines: List[str], start: int, indent: int) -> Tuple[Dict[str, Any], int]:
    mapping: Dict[str, Any] = {}
    index = start

    while index < len(lines):
        line = lines[index]
        current_indent = _indent_of(line)

        # 逻辑说明：缩进变小表示当前 mapping 结束，交给上一层继续处理。
        if current_indent < indent:
            break
        if current_indent > indent:
            raise ValueError(f"无效 YAML 缩进：{line}")

        key, value_text = _split_key_value(line.strip())
        index += 1

        # 逻辑说明：`key: |` 表示后续缩进行组成多行字符串。
        if value_text == "|":
            value, index = _parse_block_scalar(lines, index, indent + 2)
            mapping[key] = value
            continue

        # 逻辑说明：`key:` 后面没有值时，根据下一行判断是 list 还是 mapping。
        if value_text == "":
            if index >= len(lines) or _indent_of(lines[index]) <= indent:
                mapping[key] = {}
                continue

            next_indent = _indent_of(lines[index])
            if lines[index].lstrip().startswith("- "):
                value, index = _parse_sequence(lines, index, next_indent)
            else:
                value, index = _parse_mapping(lines, index, next_indent)
            mapping[key] = value
            continue

        mapping[key] = _parse_scalar(value_text)

    return mapping, index


# 函数说明：解析 YAML sequence，支持简单标量列表和对象列表。
def _parse_sequence(lines: List[str], start: int, indent: int) -> Tuple[List[Any], int]:
    result: List[Any] = []
    index = start

    while index < len(lines):
        line = lines[index]
        current_indent = _indent_of(line)

        if current_indent < indent:
            break
        if current_indent != indent or not line.lstrip().startswith("- "):
            raise ValueError(f"无效 YAML 列表项：{line}")

        item_text = line.strip()[2:].strip()
        index += 1

        # 逻辑说明：`- key: value` 形式用于未来扩展对象列表。
        if ":" in item_text and not item_text.startswith(("'", '"')):
            key, value_text = _split_key_value(item_text)
            item: Dict[str, Any] = {key: _parse_scalar(value_text)}

            if index < len(lines) and _indent_of(lines[index]) > indent:
                nested, index = _parse_mapping(lines, index, indent + 2)
                item.update(nested)

            result.append(item)
            continue

        result.append(_parse_scalar(item_text))

    return result, index


# 函数说明：解析 YAML block scalar，把公共缩进去掉并保留换行。
def _parse_block_scalar(lines: List[str], start: int, indent: int) -> Tuple[str, int]:
    block_lines: List[str] = []
    index = start

    while index < len(lines):
        line = lines[index]
        current_indent = _indent_of(line)

        if current_indent < indent:
            break

        block_lines.append(line[indent:])
        index += 1

    return "\n".join(block_lines), index


# 函数说明：拆分 `key: value` 行。
def _split_key_value(text: str) -> Tuple[str, str]:
    if ":" not in text:
        raise ValueError(f"YAML 行缺少冒号：{text}")
    key, value = text.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"YAML key 不能为空：{text}")
    return key, value.strip()


# 函数说明：解析 fallback 支持的 YAML 标量。
def _parse_scalar(text: str) -> Any:
    if text == "":
        return ""

    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]

    if text in {"true", "True"}:
        return True
    if text in {"false", "False"}:
        return False
    if text in {"null", "Null", "~"}:
        return None

    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]

    try:
        return int(text)
    except ValueError:
        pass

    try:
        return float(text)
    except ValueError:
        return text


# 函数说明：计算行首空格数量，fallback YAML 只支持空格缩进。
def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))
