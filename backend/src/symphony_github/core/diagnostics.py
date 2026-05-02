"""持久诊断日志、脱敏、查询和诊断包导出。

本模块只使用 Python 标准库，避免给桌面打包增加额外运行时依赖。后端日志统一写入
JSON Lines 文件，Electron main 进程会把同一个日志目录传给 Python 后端，方便用户在
不重新打包的情况下直接提供完整诊断材料。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BACKEND_LOG_FILE = "backend.jsonl"
DEFAULT_MAX_FILE_MB = 10
DEFAULT_RETENTION_DAYS = 14
DEFAULT_LOG_LEVEL = "DEBUG"
SECRET_KEYS = {
    "api_token",
    "authorization",
    "github_token",
    "password",
    "pat",
    "secret",
    "token",
}
SECRET_PATTERNS = [
    re.compile(r"((?:https?|ssh)://)[^/@\s]+(?=@)", re.IGNORECASE),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE),
    re.compile(
        r"(Authorization[\"']?\s*[:=]\s*[\"']?(?:Bearer\s+)?)"
        r"[A-Za-z0-9._\-]+",
        re.IGNORECASE,
    ),
]


@dataclass
class DiagnosticsRuntimeConfig:
    """当前后端诊断日志运行时配置。"""

    log_dir: str
    level: str
    retention_days: int
    max_file_mb: int
    backend_log_file: str


_CURRENT_CONFIG: Optional[DiagnosticsRuntimeConfig] = None


class JsonLineFormatter(logging.Formatter):
    """把 logging record 格式化为单行 JSON。"""

    # 函数说明：把 LogRecord 转成结构化 JSON 字符串，并在最后一道关口执行脱敏。
    def format(self, record: logging.LogRecord) -> str:
        payload = redact_data(getattr(record, "payload", {}) or {})
        event_type = str(getattr(record, "event_type", record.name))
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event_type": event_type,
            "message": redact_text(record.getMessage()),
            "issue_id": _record_or_payload(record, payload, "issue_id"),
            "identifier": _record_or_payload(record, payload, "identifier"),
            "run_id": _record_or_payload(record, payload, "run_id"),
            "thread_id": _record_or_payload(record, payload, "thread_id"),
            "turn_id": _record_or_payload(record, payload, "turn_id"),
            "settings_generation": _record_or_payload(record, payload, "settings_generation"),
            "payload": payload,
            "exception": self.formatException(record.exc_info) if record.exc_info else None,
        }
        return json.dumps(redact_data(entry), ensure_ascii=False, sort_keys=False)


# 函数说明：返回默认日志目录；Electron 打包版会通过 SYMPHONY_LOG_DIR 覆盖到 userData/logs。
def default_log_dir() -> str:
    configured = os.environ.get("SYMPHONY_LOG_DIR")
    if configured:
        return str(Path(configured).expanduser())
    return str(Path.home() / ".github-symphony" / "logs")


# 函数说明：配置后端结构化日志文件 handler，并清理超出保留期的旧日志。
def configure_diagnostics(
    log_dir: Optional[str] = None,
    level: str = DEFAULT_LOG_LEVEL,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_file_mb: int = DEFAULT_MAX_FILE_MB,
) -> DiagnosticsRuntimeConfig:
    global _CURRENT_CONFIG

    normalized_level = _normalize_level(level)
    directory = Path(log_dir or default_log_dir()).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    max_bytes = max(1, int(max_file_mb)) * 1024 * 1024
    handler = RotatingFileHandler(
        directory / BACKEND_LOG_FILE,
        maxBytes=max_bytes,
        backupCount=max(3, int(retention_days)),
        encoding="utf-8",
    )
    handler.setFormatter(JsonLineFormatter())
    handler._github_symphony_jsonl = True  # type: ignore[attr-defined]

    logger = logging.getLogger("symphony_github")
    logger.setLevel(normalized_level)
    logger.propagate = False

    # 逻辑说明：热应用 logging 设置时替换旧 handler，避免同一事件重复写入多个文件句柄。
    for existing in list(logger.handlers):
        if getattr(existing, "_github_symphony_jsonl", False):
            logger.removeHandler(existing)
            existing.close()

    logger.addHandler(handler)
    _CURRENT_CONFIG = DiagnosticsRuntimeConfig(
        log_dir=str(directory),
        level=normalized_level,
        retention_days=max(1, int(retention_days)),
        max_file_mb=max(1, int(max_file_mb)),
        backend_log_file=str(directory / BACKEND_LOG_FILE),
    )
    cleanup_old_logs(_CURRENT_CONFIG)
    log_diagnostic(
        "system.logging.configured",
        "后端诊断日志已配置",
        payload=asdict(_CURRENT_CONFIG),
        level="DEBUG",
    )
    return _CURRENT_CONFIG


# 函数说明：读取当前日志配置；尚未显式配置时使用默认目录完成一次惰性初始化。
def current_diagnostics_config() -> DiagnosticsRuntimeConfig:
    if _CURRENT_CONFIG is None:
        return configure_diagnostics(
            os.environ.get("SYMPHONY_LOG_DIR"),
            os.environ.get("SYMPHONY_LOG_LEVEL", DEFAULT_LOG_LEVEL),
            DEFAULT_RETENTION_DAYS,
            DEFAULT_MAX_FILE_MB,
        )
    return _CURRENT_CONFIG


# 函数说明：写入一条结构化诊断日志；调用方只需提供事件类型、消息和业务 payload。
def log_diagnostic(
    event_type: str,
    message: str,
    payload: Optional[Dict[str, Any]] = None,
    level: str = "INFO",
    exc_info: Any = None,
) -> None:
    logger = logging.getLogger("symphony_github.events")
    logger.log(
        logging.getLevelName(_normalize_level(level)),
        message,
        extra={"event_type": event_type, "payload": redact_data(payload or {})},
        exc_info=exc_info,
    )


# 函数说明：按事件类型推断日志级别，让错误、失败和 warning 在 Logs 页更容易筛选。
def inferred_level_for_event(event_type: str, payload: Optional[Dict[str, Any]] = None) -> str:
    lowered = event_type.lower()
    payload = payload or {}
    if any(marker in lowered for marker in ("error", "failed", "failure")) or payload.get("error"):
        return "ERROR"
    if any(marker in lowered for marker in ("warning", "retry", "stderr")):
        return "WARNING"
    if any(marker in lowered for marker in ("notification", "api.request", "debug", "skipped")):
        return "DEBUG"
    return "INFO"


# 函数说明：查询 JSONL 日志文件，并返回分页后的结构化记录。
def query_logs(
    level: Optional[str] = None,
    event_type: Optional[str] = None,
    identifier: Optional[str] = None,
    q: Optional[str] = None,
    cursor: Optional[int] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    config = current_diagnostics_config()
    records = [
        record
        for record in _iter_log_records(Path(config.log_dir))
        if _matches_log_record(record, level, event_type, identifier, q)
    ]
    records.reverse()
    start = max(0, int(cursor or 0))
    safe_limit = min(max(1, int(limit)), 500)
    page = records[start : start + safe_limit]
    next_cursor = start + len(page) if start + len(page) < len(records) else None
    return {"entries": page, "next_cursor": next_cursor}


# 函数说明：导出脱敏诊断包，包含日志、当前 state、配置摘要和本地运行环境信息。
def export_diagnostics_bundle(
    state: Dict[str, Any],
    settings_summary: Dict[str, Any],
) -> str:
    config = current_diagnostics_config()
    log_dir = Path(config.log_dir)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = log_dir / f"github-symphony-diagnostics-{timestamp}.zip"

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in _log_files(log_dir):
            archive.write(file_path, arcname=f"logs/{file_path.name}")
        _write_json_member(archive, "state.json", state)
        _write_json_member(archive, "settings-summary.json", settings_summary)
        _write_json_member(archive, "doctor.json", _doctor_summary(config))

    log_diagnostic(
        "system.logs.exported",
        "诊断包已导出",
        {"path": str(target)},
        level="INFO",
    )
    return str(target)


# 函数说明：清理超过保留天数的日志和诊断包，避免长期运行无限占用磁盘。
def cleanup_old_logs(config: DiagnosticsRuntimeConfig) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.retention_days)
    for file_path in Path(config.log_dir).glob("*"):
        if not file_path.is_file():
            continue
        if not (
            file_path.name.endswith(".jsonl")
            or ".jsonl." in file_path.name
            or file_path.suffix == ".zip"
        ):
            continue
        modified = datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            file_path.unlink(missing_ok=True)


# 函数说明：递归脱敏 dict/list/string，防止 PAT 或 Authorization 出现在日志和诊断包里。
def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if _is_secret_key(key_text):
                result[key] = "***"
            else:
                result[key] = redact_data(item)
        return result
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


# 函数说明：对普通文本执行 token/PAT/Authorization 脱敏。
def redact_text(text: str) -> str:
    result = text
    for pattern in SECRET_PATTERNS:
        result = pattern.sub(_redaction_replacement, result)
    return result


# 函数说明：返回所有可纳入查询和诊断包的日志文件，按修改时间从旧到新排序。
def _log_files(log_dir: Path) -> List[Path]:
    candidates = [
        path
        for path in log_dir.glob("*.jsonl*")
        if path.is_file() and (path.name.endswith(".jsonl") or ".jsonl." in path.name)
    ]
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name))


# 函数说明：迭代解析日志文件；遇到坏行时生成 synthetic 记录而不是中断查询。
def _iter_log_records(log_dir: Path) -> Iterable[Dict[str, Any]]:
    index = 0
    for file_path in _log_files(log_dir):
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    record = {
                        "timestamp": None,
                        "level": "ERROR",
                        "logger": "symphony_github.diagnostics",
                        "event_type": "system.logs.malformed_line",
                        "message": "日志文件中存在无法解析的 JSONL 行",
                        "payload": {"source": file_path.name, "line_number": line_number},
                    }
                record = redact_data(record)
                record["_cursor"] = index
                record["_source"] = file_path.name
                index += 1
                yield record


# 函数说明：判断单条日志是否满足 UI 传入的过滤条件。
def _matches_log_record(
    record: Dict[str, Any],
    level: Optional[str],
    event_type: Optional[str],
    identifier: Optional[str],
    q: Optional[str],
) -> bool:
    if level and str(record.get("level", "")).upper() != level.upper():
        return False
    if event_type and event_type.lower() not in str(record.get("event_type", "")).lower():
        return False
    if identifier and identifier.lower() not in str(record.get("identifier", "")).lower():
        return False
    if q:
        haystack = json.dumps(record, ensure_ascii=False).lower()
        if q.lower() not in haystack:
            return False
    return True


# 函数说明：把 JSON 内容写入 zip 成员，写入前统一脱敏和格式化。
def _write_json_member(archive: zipfile.ZipFile, name: str, payload: Dict[str, Any]) -> None:
    archive.writestr(
        name,
        json.dumps(redact_data(payload), ensure_ascii=False, indent=2, sort_keys=True),
    )


# 函数说明：生成诊断包中的本地运行环境摘要，不执行外部命令以避免副作用。
def _doctor_summary(config: DiagnosticsRuntimeConfig) -> Dict[str, Any]:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": os.getcwd(),
        "logging": asdict(config),
        "env": {
            "SYMPHONY_LOG_DIR": os.environ.get("SYMPHONY_LOG_DIR"),
            "SYMPHONY_LOG_LEVEL": os.environ.get("SYMPHONY_LOG_LEVEL"),
        },
    }


# 函数说明：从 record 显式字段或 payload 中提取顶层关联字段。
def _record_or_payload(record: logging.LogRecord, payload: Dict[str, Any], key: str) -> Any:
    value = getattr(record, key, None)
    if value is not None:
        return value
    return payload.get(key)


# 函数说明：把日志级别字符串规整为 logging 模块支持的大写值。
def _normalize_level(level: str) -> str:
    normalized = str(level or DEFAULT_LOG_LEVEL).upper()
    if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        return DEFAULT_LOG_LEVEL
    return normalized


# 函数说明：判断字段名是否代表敏感值；精确匹配 token/PAT，避免把 path 误判为 PAT。
def _is_secret_key(key_text: str) -> bool:
    return (
        key_text in SECRET_KEYS
        or key_text.endswith("_token")
        or key_text.endswith("_secret")
        or key_text.endswith("_password")
        or key_text.endswith("_pat")
    )


# 函数说明：为正则脱敏保留前缀，例如 `Bearer `，其余敏感值替换为星号。
def _redaction_replacement(match: re.Match[str]) -> str:
    if match.groups():
        return f"{match.group(1)}***"
    return "***"
