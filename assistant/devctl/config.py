"""Central config loader for personal-assistant tooling."""

from __future__ import annotations

import os
import tempfile
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

_MODULE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _MODULE_DIR.parents[1]
_CONFIG_ENV_VAR = "PERSONAL_ASSISTANT_CONFIG"
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.toml"


class _SafeFormatDict(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def project_root() -> Path:
    return _PROJECT_ROOT


def config_path() -> Path:
    override = os.environ.get(_CONFIG_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _DEFAULT_CONFIG_PATH


def _format_value(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format_map(_SafeFormatDict(context)).strip()
    if isinstance(value, list):
        return [_format_value(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _format_value(item, context) for key, item in value.items()}
    return value


def _base_context() -> dict[str, str]:
    root = project_root()
    return {
        "project_root": str(root),
        "desktop_root": str(root.parent.parent),
        "home": str(Path.home()),
        "username": os.environ.get("USERNAME") or os.environ.get("USER") or "",
        "temp": str(Path(tempfile.gettempdir()).expanduser().resolve()),
        "config_dir": str(config_path().parent),
        "appdata": str(Path(os.environ.get("APPDATA", "")).expanduser().resolve()),
    }


def _build_context(raw: dict[str, Any]) -> dict[str, str]:
    context = _base_context()
    path_values = raw.get("paths", {})
    if isinstance(path_values, dict):
        for _ in range(4):
            for key, value in path_values.items():
                if isinstance(value, str):
                    context[key] = str(Path(_format_value(value, context)).expanduser().resolve())

    execution = raw.get("recipes", {}).get("execution", {})
    if isinstance(execution, dict) and isinstance(execution.get("preface"), str):
        context["execution_preface"] = _format_value(execution["preface"], context)

    return context


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    path = config_path()
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    return _format_value(raw, _build_context(raw))


def get_value(key_path: str) -> Any:
    current: Any = load_config()
    for part in key_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"Missing config key: {key_path}")
        current = current[part]
    return current


def get_table(key_path: str) -> dict[str, Any]:
    value = get_value(key_path)
    if not isinstance(value, dict):
        raise TypeError(f"Config key is not a table: {key_path}")
    return value


def get_str(key_path: str) -> str:
    return str(get_value(key_path))


def get_int(key_path: str) -> int:
    return int(get_value(key_path))


def get_bool(key_path: str) -> bool:
    return bool(get_value(key_path))


def get_list(key_path: str) -> list[Any]:
    value = get_value(key_path)
    if not isinstance(value, list):
        raise TypeError(f"Config key is not a list: {key_path}")
    return value


def get_path(key_path: str) -> Path:
    return Path(get_str(key_path)).expanduser().resolve()
