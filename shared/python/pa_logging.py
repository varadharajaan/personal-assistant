"""Shared logger for personal-assistant Python tools.

Mirrors the logging shape used by example-app-launcher and jar:

- logs/unified/<flow>.log is the primary per-flow stream.
- logs/unified/_session.log mirrors all flow lines.
- logs/py/<flow>.log keeps Python-specific lines.

Line format:
    [YYYY-MM-DD HH:MM:SS] [PY] [flow] [LEVEL] message
"""

from __future__ import annotations

import glob
import os
import time
import tomllib
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

_HELPER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _HELPER_DIR.parent.parent
_CONFIG_PATH = _REPO_ROOT / "config" / "settings.toml"
_LOG_ROOT = _REPO_ROOT / "logs"
_UNIFIED_DIR = _LOG_ROOT / "unified"
_PY_DIR = _LOG_ROOT / "py"
_DEBUG_LEVELS = {"TRACE", "DEBUG"}
_LEVELS = {"TRACE", "DEBUG", "INFO", "WARN", "ERROR", "OK"}


@lru_cache(maxsize=1)
def _logging_config() -> dict[str, Any]:
    with _CONFIG_PATH.open("rb") as handle:
        config = tomllib.load(handle)
    logging_config = config.get("logging")
    if not isinstance(logging_config, dict):
        raise KeyError("Missing [logging] table in config/settings.toml")
    return logging_config


def _config_int(key: str) -> int:
    return int(_logging_config()[key])


def _config_str(key: str) -> str:
    return str(_logging_config()[key])


def _config_str_list(key: str) -> list[str]:
    value = _logging_config()[key]
    if not isinstance(value, list):
        raise TypeError(f"logging.{key} must be a list")
    return [str(item) for item in value]


def debug_logs_enabled() -> bool:
    value = os.environ.get(_config_str("debug_env_var"), "").strip().lower()
    return value in {item.lower() for item in _config_str_list("debug_enabled_values")}


def _cleanup_old_logs(log_dir: Path) -> None:
    cutoff = time.time() - (_config_int("retention_days") * 86400)
    for file_name in glob.glob(str(log_dir / "**" / "*.log"), recursive=True):
        try:
            path = Path(file_name)
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def _roll_if_big(path: Path) -> None:
    try:
        max_size = _config_int("roll_size_mb") * 1024 * 1024
        if path.is_file() and path.stat().st_size >= max_size:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            rolled = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
            path.replace(rolled)
    except OSError:
        pass


def _append_line(path: Path, line: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _roll_if_big(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _format_suffix(fields: dict[str, Any]) -> str:
    if not fields:
        return ""
    safe_fields = {}
    redacted_terms = tuple(term.lower() for term in _config_str_list("redacted_key_terms"))
    for key, value in fields.items():
        lowered = str(key).lower()
        if any(term in lowered for term in redacted_terms):
            safe_fields[key] = "<redacted>"
        else:
            safe_fields[key] = value
    return " | " + " | ".join(f"{key}={value}" for key, value in safe_fields.items())


def get_logger(flow_name: str, *, lang: str = "PY") -> Callable[[str], None]:
    """Return a small structured logger for a flow.

    Usage:
        log = get_logger("memory-sync")
        log("started")
        log("archive complete", level="OK", records=42)
    """

    normalized_flow = flow_name.strip()
    if not normalized_flow:
        raise ValueError("flow_name is required")

    layer = lang.strip().upper() or "PY"
    _UNIFIED_DIR.mkdir(parents=True, exist_ok=True)
    _PY_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_logs(_LOG_ROOT)

    unified_file = _UNIFIED_DIR / f"{normalized_flow}.log"
    session_file = _UNIFIED_DIR / "_session.log"
    py_file = _PY_DIR / f"{normalized_flow}.log"

    def log(message: str, *, level: str = "INFO", **fields: Any) -> None:
        level_name = level.strip().upper()
        if level_name not in _LEVELS:
            level_name = "INFO"
        if level_name in _DEBUG_LEVELS and not debug_logs_enabled():
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        suffix = _format_suffix(fields)
        line = f"[{timestamp}] [{layer}] [{normalized_flow}] [{level_name}] {message}{suffix}"
        per_lang_line = f"[{timestamp}] [{level_name}] {message}{suffix}"

        _append_line(unified_file, line)
        _append_line(session_file, line)
        _append_line(py_file, per_lang_line)

    return log
