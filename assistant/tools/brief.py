"""Local daily brief builder."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from assistant.devctl import log_inspector
from assistant.devctl.config import get_int, get_path, get_str
from assistant.devctl.mobile_inbox import list_commands
from assistant.tools.notes import NoteStore, configured_note_store
from assistant.tools.todos import TodoStore, configured_todo_store

LogFn = Callable[..., None]


@dataclass(frozen=True)
class BriefResult:
    path: str
    title: str
    chars: int


class DailyBriefBuilder:
    """Compose a local brief from configured local stores."""

    def __init__(self, notes: NoteStore, todos: TodoStore, briefs_dir: Path) -> None:
        self._notes = notes
        self._todos = todos
        self._briefs_dir = briefs_dir

    def build(self, *, log: LogFn) -> BriefResult:
        now = datetime.now()
        title = f"{get_str('tools.daily_brief.title_prefix')} {now.date().isoformat()}"
        pending_todos = self._todos.list(
            status=get_str("tools.todos.default_status"),
            limit=get_int("tools.daily_brief.pending_todos_limit"),
        )
        latest_notes = self._notes.list(limit=get_int("tools.daily_brief.latest_notes_limit"))
        mobile_commands = list_commands(status=get_str("mobile.pending_status"))[: get_int("tools.daily_brief.mobile_limit")]
        recent_logs = log_inspector.tail_lines(
            source=get_str("logs.default_source"),
            lines=get_int("tools.daily_brief.log_lines"),
            max_files=get_int("logs.latest_log_max_files"),
        )
        content = self._render(title, pending_todos, latest_notes, mobile_commands, recent_logs)
        stamp = now.strftime(get_str("tools.daily_brief.timestamp_format"))
        path = self._briefs_dir / f"{stamp}-{get_str('tools.daily_brief.file_name_suffix')}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        log(
            "daily brief generated",
            level="OK",
            path=str(path),
            todos=len(pending_todos),
            notes=len(latest_notes),
            mobile=len(mobile_commands),
        )
        return BriefResult(path=str(path), title=title, chars=len(content))

    def _render(self, title: str, pending_todos, latest_notes, mobile_commands, recent_logs) -> str:
        lines = [f"# {title}", ""]
        lines.extend(["## Pending Todos", ""])
        if pending_todos:
            for todo in pending_todos:
                due = f" due={todo.due}" if todo.due else ""
                lines.append(f"- [{todo.priority}] {todo.id}: {todo.title}{due}")
        else:
            lines.append("- None")

        lines.extend(["", "## Latest Notes", ""])
        if latest_notes:
            for note in latest_notes:
                lines.append(f"- {note.id}: {note.title} ({', '.join(note.tags)})")
        else:
            lines.append("- None")

        lines.extend(["", "## Pending Mobile Commands", ""])
        if mobile_commands:
            for command in mobile_commands:
                preview = command.text.replace("\r", " ").replace("\n", " ")[: get_int("mobile.preview_chars")]
                lines.append(f"- {command.id}: {command.source}/{command.channel or '-'} {preview}")
        else:
            lines.append("- None")

        lines.extend(["", "## Recent Personal Assistant Logs", ""])
        if recent_logs:
            lines.extend(f"- {item.file.name}: {item.line}" for item in recent_logs)
        else:
            lines.append("- None")
        lines.append("")
        return "\n".join(lines)


def configured_daily_brief_builder() -> DailyBriefBuilder:
    return DailyBriefBuilder(configured_note_store(), configured_todo_store(), get_path("paths.briefs_dir"))
