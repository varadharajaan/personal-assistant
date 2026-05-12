"""Local log inspection utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import get_bool, get_int, get_list, get_path, get_str, get_table
_ERROR_PATTERN = re.compile(get_str("logs.error_line_regex"), re.IGNORECASE)
_SENSITIVE_NAME_PATTERN = re.compile(get_str("logs.sensitive_name_regex"), re.IGNORECASE)


@dataclass(frozen=True)
class LogLine:
    file: Path
    line: str


def _is_safe_log_file(path: Path) -> bool:
    if _SENSITIVE_NAME_PATTERN.search(path.name):
        return False
    return path.suffix.lower() in {str(suffix).lower() for suffix in get_list("logs.safe_suffixes")}


def _configured_secret_values() -> list[str]:
    values: list[str] = []
    for raw_key in get_list("logs.redaction_secret_file_keys"):
        key = str(raw_key)
        try:
            path = get_path(key)
        except (KeyError, TypeError, ValueError):
            continue
        if not path.is_file():
            continue
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            values.append(value)
    return values


def _configured_redaction_patterns() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for raw_pattern in get_list("logs.redaction_patterns"):
        pattern = str(raw_pattern)
        if not pattern:
            continue
        patterns.append(re.compile(pattern, re.IGNORECASE))
    return patterns


def redact_text(text: str) -> str:
    if not get_bool("logs.redaction_enabled"):
        return text

    output = text
    replacement = get_str("logs.redaction_replacement")
    for secret in _configured_secret_values():
        output = output.replace(secret, replacement)
    for pattern in _configured_redaction_patterns():
        output = pattern.sub(replacement, output)
    return output


def iter_log_files(source: str) -> list[Path]:
    source_name = source.lower()
    files: list[Path] = []

    source_roots = get_table("logs.source_roots")
    selected_sources = (
        [name for name in source_roots if name != "all"]
        if source_name == "all"
        else [source_name]
    )

    for selected_source in selected_sources:
        raw_roots = source_roots.get(selected_source, [])
        if not isinstance(raw_roots, list):
            continue
        for raw_root in raw_roots:
            root = Path(str(raw_root)).expanduser()
            if root.exists():
                files.extend(path for path in root.rglob("*") if path.is_file() and _is_safe_log_file(path))

    unique_files = sorted(set(files), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return unique_files


def read_tail(path: Path, *, max_bytes: int | None = None) -> str:
    limit = max_bytes if max_bytes is not None else get_int("logs.read_tail_max_bytes")
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > limit:
                handle.seek(size - limit)
            data = handle.read()
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def tail_lines(
    *,
    source: str,
    lines: int,
    contains: str | None = None,
    errors_only: bool = False,
    max_files: int | None = None,
) -> list[LogLine]:
    output: list[LogLine] = []
    needle = contains.lower() if contains else None
    file_limit = max_files if max_files is not None else get_int("logs.max_files_default")
    for path in iter_log_files(source)[:file_limit]:
        text = read_tail(path)
        raw_lines = text.splitlines()
        if needle:
            raw_lines = [line for line in raw_lines if needle in line.lower()]
        if errors_only:
            raw_lines = [line for line in raw_lines if _ERROR_PATTERN.search(line)]
        for line in raw_lines[-lines:]:
            output.append(LogLine(file=path, line=redact_text(line)))
    return output[-lines:]


def error_lines(*, source: str, limit: int) -> list[LogLine]:
    results: list[LogLine] = []
    for path in iter_log_files(source):
        for line in read_tail(path).splitlines():
            if _ERROR_PATTERN.search(line):
                results.append(LogLine(file=path, line=redact_text(line)))
    return results[-limit:]


def summarize_logs(*, source: str) -> dict[str, object]:
    files = iter_log_files(source)
    errors = error_lines(source=source, limit=get_int("logs.summary_error_limit"))
    latest_files = [
        {
            "path": str(path),
            "size": path.stat().st_size,
            "modified": path.stat().st_mtime,
        }
        for path in files[: get_int("logs.summary_latest_file_count")]
    ]
    return {
        "source": source,
        "file_count": len(files),
        "warn_error_count_in_tails": len(errors),
        "latest_files": latest_files,
        "latest_warn_error_lines": [
            {"file": str(item.file), "line": item.line}
            for item in errors[-get_int("logs.summary_latest_error_count") :]
        ],
    }


def format_log_lines(lines: Iterable[LogLine]) -> str:
    blocks: list[str] = []
    for item in lines:
        blocks.append(f"{item.file}: {redact_text(item.line)}")
    return "\n".join(blocks)
