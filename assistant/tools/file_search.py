"""Approved-folder read-only file search."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from assistant.devctl.config import get_int, get_list, get_str, get_table

LogFn = Callable[..., None]


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line_number: int
    line: str


class FileSearcher:
    """Search text only inside configured approved roots."""

    def __init__(self, approved_roots: dict[str, str]) -> None:
        self._approved_roots = {key: Path(value).expanduser().resolve() for key, value in approved_roots.items()}

    def scopes(self) -> list[str]:
        return sorted(self._approved_roots)

    def search(self, *, scope: str, query: str, limit: int, log: LogFn) -> list[SearchMatch]:
        if scope not in self._approved_roots:
            available = ", ".join(self.scopes())
            raise ValueError(f"Unknown file search scope '{scope}'. Available: {available}")
        root = self._approved_roots[scope]
        matches: list[SearchMatch] = []
        for path in self._iter_files(root):
            for match in self._search_file(path, query):
                matches.append(match)
                if len(matches) >= limit:
                    log("file search completed", level="OK", scope=scope, query_chars=len(query), matches=len(matches))
                    return matches
        log("file search completed", level="OK", scope=scope, query_chars=len(query), matches=len(matches))
        return matches

    def _iter_files(self, root: Path) -> Iterable[Path]:
        if not root.exists():
            return
        safe_suffixes = {str(item).lower() for item in get_list("tools.file_search.safe_suffixes")}
        exclude_dirs = {str(item).lower() for item in get_list("tools.file_search.exclude_dirs")}
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part.lower() in exclude_dirs for part in path.parts):
                continue
            if path.suffix.lower() not in safe_suffixes:
                continue
            if self._is_sensitive(path):
                continue
            try:
                if path.stat().st_size > get_int("tools.file_search.max_file_bytes"):
                    continue
            except OSError:
                continue
            yield path

    def _search_file(self, path: Path, query: str) -> Iterable[SearchMatch]:
        needle = query.casefold()
        context_chars = get_int("tools.file_search.context_chars")
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if needle not in line.casefold():
                        continue
                    stripped = line.strip()
                    yield SearchMatch(path=str(path), line_number=line_number, line=stripped[:context_chars])
        except OSError:
            return

    def _is_sensitive(self, path: Path) -> bool:
        patterns = [str(item) for item in get_list("tools.file_search.sensitive_name_patterns")]
        return any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns)


def configured_file_searcher() -> FileSearcher:
    return FileSearcher({key: str(value) for key, value in get_table("tools.file_search.approved_roots").items()})

