"""Markdown-backed local notes tool."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from assistant.devctl.config import get_int, get_list, get_path, get_str

LogFn = Callable[..., None]


@dataclass(frozen=True)
class NoteRecord:
    id: str
    title: str
    tags: list[str]
    created_at: str
    path: str
    body_chars: int


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _slug(value: str, max_chars: int) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    fallback = get_str("tools.notes.id_prefix")
    return (normalized or fallback)[:max_chars].strip("-") or fallback


def _new_id(title: str) -> str:
    stamp = datetime.now().strftime(get_str("tools.notes.timestamp_format"))
    random_part = uuid.uuid4().hex[: get_int("tools.notes.id_random_chars")]
    slug = _slug(title, get_int("tools.notes.slug_max_chars"))
    return f"{get_str('tools.notes.id_prefix')}-{stamp}-{slug}-{random_part}"


def _normalize_tags(tags: Iterable[str] | None) -> list[str]:
    selected = list(tags or [])
    if not selected:
        selected = [str(tag) for tag in get_list("tools.notes.default_tags")]
    normalized = []
    for tag in selected:
        clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(tag).strip().lower()).strip("-")
        if clean and clean not in normalized:
            normalized.append(clean)
    return normalized


class NoteStore:
    """Store notes as markdown plus a JSONL index."""

    def __init__(self, notes_dir: Path, index_file: Path) -> None:
        self._notes_dir = notes_dir
        self._index_file = index_file

    def add(self, *, title: str, body: str, tags: Iterable[str] | None, log: LogFn) -> NoteRecord:
        note_id = _new_id(title)
        created_at = _now_iso()
        safe_tags = _normalize_tags(tags)
        path = self._notes_dir / f"{note_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = [
            "---",
            f"id: {note_id}",
            f"title: {title}",
            f"created_at: {created_at}",
            f"tags: {', '.join(safe_tags)}",
            "---",
            "",
            body.rstrip(),
            "",
        ]
        path.write_text("\n".join(content), encoding="utf-8")
        record = NoteRecord(
            id=note_id,
            title=title,
            tags=safe_tags,
            created_at=created_at,
            path=str(path),
            body_chars=len(body),
        )
        self._append_index(record)
        log("note added", level="OK", note_id=note_id, title=title, tags=",".join(safe_tags), body_chars=len(body))
        return record

    def list(self, *, limit: int, tag: str | None = None) -> list[NoteRecord]:
        records = list(reversed(self._read_index()))
        if tag:
            normalized = _normalize_tags([tag])[0]
            records = [record for record in records if normalized in record.tags]
        return records[:limit]

    def get(self, note_id: str) -> tuple[NoteRecord, str]:
        for record in self._read_index():
            if record.id == note_id:
                path = Path(record.path)
                return record, path.read_text(encoding="utf-8")
        raise KeyError(f"Unknown note id: {note_id}")

    def search(self, *, query: str, limit: int) -> list[tuple[NoteRecord, str]]:
        needle = query.casefold()
        matches: list[tuple[NoteRecord, str]] = []
        for record in reversed(self._read_index()):
            path = Path(record.path)
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            haystack = f"{record.title}\n{' '.join(record.tags)}\n{content}".casefold()
            if needle in haystack:
                preview = content.replace("\r", " ").replace("\n", " ")
                matches.append((record, preview[: get_int("tools.notes.body_preview_chars")]))
            if len(matches) >= limit:
                break
        return matches

    def _append_index(self, record: NoteRecord) -> None:
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        with self._index_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def _read_index(self) -> list[NoteRecord]:
        if not self._index_file.is_file():
            return []
        records = []
        with self._index_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                raw = json.loads(line)
                records.append(
                    NoteRecord(
                        id=str(raw["id"]),
                        title=str(raw["title"]),
                        tags=[str(tag) for tag in raw.get("tags", [])],
                        created_at=str(raw["created_at"]),
                        path=str(raw["path"]),
                        body_chars=int(raw.get("body_chars", 0)),
                    )
                )
        return records


def configured_note_store() -> NoteStore:
    return NoteStore(get_path("paths.notes_dir"), get_path("paths.notes_index_file"))

