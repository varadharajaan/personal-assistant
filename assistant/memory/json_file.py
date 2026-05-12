"""Small JSON file store with atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonFileStore:
    """Persist one JSON value in a file without leaking storage details."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def read(self, default: Any) -> Any:
        if not self._path.is_file():
            return default
        with self._path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def write(self, value: Any) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_name(f"{self._path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, self._path)

