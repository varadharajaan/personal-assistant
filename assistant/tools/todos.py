"""JSON-backed local todos tool."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from assistant.devctl.config import get_int, get_list, get_path, get_str
from assistant.memory.json_file import JsonFileStore

LogFn = Callable[..., None]


@dataclass(frozen=True)
class TodoRecord:
    id: str
    title: str
    details: str
    status: str
    priority: str
    due: str
    created_at: str
    updated_at: str
    completed_at: str


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_id() -> str:
    stamp = datetime.now().strftime(get_str("tools.todos.timestamp_format"))
    random_part = uuid.uuid4().hex[: get_int("tools.todos.id_random_chars")]
    return f"{get_str('tools.todos.id_prefix')}-{stamp}-{random_part}"


class TodoStore:
    """Manage todos while keeping callers independent from storage format."""

    def __init__(self, path: Path) -> None:
        self._store = JsonFileStore(path)

    @property
    def path(self) -> Path:
        return self._store.path

    def add(self, *, title: str, details: str, priority: str, due: str, log: LogFn) -> TodoRecord:
        priority_value = priority or get_str("tools.todos.default_priority")
        self._validate_priority(priority_value)
        now = _now_iso()
        record = TodoRecord(
            id=_new_id(),
            title=title,
            details=details,
            status=get_str("tools.todos.default_status"),
            priority=priority_value,
            due=due,
            created_at=now,
            updated_at=now,
            completed_at="",
        )
        records = self._read()
        records.append(record)
        self._write(records)
        log("todo added", level="OK", todo_id=record.id, priority=record.priority, due=record.due, details_chars=len(details))
        return record

    def list(self, *, status: str, limit: int) -> list[TodoRecord]:
        records = list(reversed(self._read()))
        selected_status = status.strip()
        if selected_status != get_str("tools.todos.all_status"):
            self._validate_status(selected_status)
            records = [record for record in records if record.status == selected_status]
        return records[:limit]

    def set_status(self, *, todo_id: str, status: str, log: LogFn) -> TodoRecord:
        self._validate_status(status)
        records = self._read()
        now = _now_iso()
        updated = None
        next_records = []
        for record in records:
            if record.id == todo_id:
                updated = TodoRecord(
                    id=record.id,
                    title=record.title,
                    details=record.details,
                    status=status,
                    priority=record.priority,
                    due=record.due,
                    created_at=record.created_at,
                    updated_at=now,
                    completed_at=now if status == get_str("tools.todos.completed_status") else "",
                )
                next_records.append(updated)
            else:
                next_records.append(record)
        if updated is None:
            raise KeyError(f"Unknown todo id: {todo_id}")
        self._write(next_records)
        log("todo status updated", level="OK", todo_id=todo_id, status=status)
        return updated

    def _read(self) -> list[TodoRecord]:
        raw_records = self._store.read([])
        records = []
        for raw in raw_records:
            records.append(
                TodoRecord(
                    id=str(raw["id"]),
                    title=str(raw["title"]),
                    details=str(raw.get("details", "")),
                    status=str(raw["status"]),
                    priority=str(raw.get("priority", get_str("tools.todos.default_priority"))),
                    due=str(raw.get("due", "")),
                    created_at=str(raw["created_at"]),
                    updated_at=str(raw.get("updated_at", raw["created_at"])),
                    completed_at=str(raw.get("completed_at", "")),
                )
            )
        return records

    def _write(self, records: list[TodoRecord]) -> None:
        self._store.write([record.__dict__ for record in records])

    def _validate_status(self, status: str) -> None:
        if status not in {str(item) for item in get_list("tools.todos.statuses")}:
            raise ValueError(f"Unsupported todo status: {status}")

    def _validate_priority(self, priority: str) -> None:
        if priority not in {str(item) for item in get_list("tools.todos.priorities")}:
            raise ValueError(f"Unsupported todo priority: {priority}")


def configured_todo_store() -> TodoStore:
    return TodoStore(get_path("paths.todos_file"))
