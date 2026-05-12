from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from assistant.tools.file_search import FileSearcher
from assistant.tools.notes import NoteStore
from assistant.tools.todos import TodoStore
from assistant.devctl.mobile_bridge import payload_to_fields


def _noop_log(*args, **kwargs) -> None:
    return None


class Phase1ToolTests(unittest.TestCase):
    def test_notes_add_list_search_show(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = NoteStore(root / "notes", root / "notes" / "index.jsonl")
            record = store.add(title="Test Note", body="hello assistant memory", tags=["Test"], log=_noop_log)

            self.assertEqual(record.title, "Test Note")
            self.assertIn(record, store.list(limit=10))
            self.assertEqual(store.search(query="assistant", limit=10)[0][0].id, record.id)
            _, content = store.get(record.id)
            self.assertIn("hello assistant memory", content)

    def test_todos_lifecycle(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = TodoStore(Path(temp_dir) / "todos.json")
            record = store.add(title="Ship tool", details="test", priority="normal", due="", log=_noop_log)

            self.assertEqual(record.status, "pending")
            self.assertEqual(store.list(status="pending", limit=10)[0].id, record.id)

            completed = store.set_status(todo_id=record.id, status="completed", log=_noop_log)
            self.assertEqual(completed.status, "completed")
            self.assertEqual(store.list(status="pending", limit=10), [])

    def test_file_search_uses_approved_scope_and_skips_sensitive_files(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "visible.md").write_text("needle appears here\n", encoding="utf-8")
            (root / ".env").write_text("needle should not appear\n", encoding="utf-8")

            searcher = FileSearcher({"temp": str(root)})
            matches = searcher.search(scope="temp", query="needle", limit=10, log=_noop_log)

            self.assertEqual(len(matches), 1)
            self.assertTrue(matches[0].path.endswith("visible.md"))

    def test_mobile_bridge_payload_fields_are_configured(self) -> None:
        fields = payload_to_fields(
            {
                "text": "summarize latest logs",
                "from": "phone",
                "source": "unit-test",
                "channel": "local",
            }
        )

        self.assertEqual(fields.message, "summarize latest logs")
        self.assertEqual(fields.sender, "phone")
        self.assertEqual(fields.source, "unit-test")
        self.assertEqual(fields.channel, "local")


if __name__ == "__main__":
    unittest.main()
