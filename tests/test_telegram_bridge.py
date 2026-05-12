"""Mocked tests for the independent Telegram bridge.

These tests never touch the network; they exercise the bridge orchestration,
owner filter, dispatch rule matching, offset persistence, OpenClaw reply
extraction, and inbound parsing.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant.devctl import telegram_bridge as bridge_module
from assistant.devctl.openclaw_runner import CommandResult
from assistant.devctl.telegram_bridge import (
    ConfiguredDispatcher,
    DispatchResult,
    InboundMessage,
    OffsetStore,
    OwnerFilter,
    SendResult,
    TelegramBridge,
    _extract_openclaw_reply,
    _sender_hash,
    bridge_status_as_dict,
    configured_rules_as_dicts,
    parse_inbound_message,
)


class StubPoller:
    def __init__(self, updates_per_call: list[list[dict[str, Any]]] | None = None, send_results: list[SendResult] | None = None) -> None:
        self.token = "stub-token-123"
        self._updates = list(updates_per_call or [])
        self._send_results = list(send_results or [])
        self.send_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def get_updates(self, *, offset: int, allowed_updates: list[str], long_timeout: int, http_timeout: int) -> list[dict[str, Any]]:
        self.get_calls.append({"offset": offset, "allowed_updates": allowed_updates})
        if not self._updates:
            return []
        return self._updates.pop(0)

    def send_message(self, *, chat_id: int, text: str, http_timeout: int) -> SendResult:
        self.send_calls.append({"chat_id": chat_id, "text": text})
        if self._send_results:
            return self._send_results.pop(0)
        return SendResult(ok=True, returncode=0, detail="")


class StubOwnerFilter:
    def __init__(self, allowed: set[int]) -> None:
        self._allowed = allowed

    def is_authorized(self, sender_id: int) -> bool:
        return sender_id in self._allowed


@dataclass
class CapturedLog:
    records: list[dict[str, Any]]

    def __call__(self, message: str, *, level: str = "INFO", **fields: Any) -> None:
        self.records.append({"message": message, "level": level, **fields})


def _capture_log() -> CapturedLog:
    return CapturedLog(records=[])


def _make_update(*, update_id: int, sender_id: int, chat_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1715500000,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": sender_id, "is_bot": False, "username": "test"},
            "text": text,
        },
    }


class ParseInboundMessageTests(unittest.TestCase):
    def test_parses_text_message(self) -> None:
        update = _make_update(update_id=10, sender_id=42, chat_id=100, text="hello")
        message = parse_inbound_message(update)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.update_id, 10)
        self.assertEqual(message.sender_id, 42)
        self.assertEqual(message.chat_id, 100)
        self.assertEqual(message.text, "hello")

    def test_returns_none_for_empty_text(self) -> None:
        update = {"update_id": 1, "message": {"chat": {"id": 1}, "from": {"id": 2}, "text": ""}}
        self.assertIsNone(parse_inbound_message(update))

    def test_returns_none_when_chat_missing(self) -> None:
        update = {"update_id": 1, "message": {"from": {"id": 2}, "text": "hi"}}
        self.assertIsNone(parse_inbound_message(update))


class OffsetStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "offset.json"
            store = OffsetStore(path=path)
            self.assertEqual(store.load(), 0)
            store.save(42)
            self.assertEqual(store.load(), 42)
            store.save(100)
            self.assertEqual(store.load(), 100)

    def test_missing_file_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = OffsetStore(path=Path(tmp) / "does-not-exist.json")
            self.assertEqual(store.load(), 0)


class ExtractOpenClawReplyTests(unittest.TestCase):
    def _result(self, stdout: str = "", stderr: str = "") -> CommandResult:
        return CommandResult(args=[], returncode=0, stdout=stdout, stderr=stderr, elapsed_seconds=0.0)

    def test_plain_text(self) -> None:
        self.assertEqual(_extract_openclaw_reply(self._result(stdout="hello there")), "hello there")

    def test_json_with_reply_key(self) -> None:
        payload = json.dumps({"reply": "json reply body"})
        self.assertEqual(_extract_openclaw_reply(self._result(stdout=payload)), "json reply body")

    def test_chat_completion_shape(self) -> None:
        payload = json.dumps({"choices": [{"message": {"content": "chat content"}}]})
        self.assertEqual(_extract_openclaw_reply(self._result(stdout=payload)), "chat content")

    def test_falls_back_to_stderr_when_stdout_empty(self) -> None:
        self.assertEqual(_extract_openclaw_reply(self._result(stderr="bad")), "bad")


class DispatcherTests(unittest.TestCase):
    def _message(self, text: str) -> InboundMessage:
        return InboundMessage(
            update_id=1,
            chat_id=999,
            sender_id=42,
            sender_username="test",
            text=text,
            raw_date=0,
        )

    def test_canned_ping_rule(self) -> None:
        dispatcher = ConfiguredDispatcher(runner_factory=lambda: None)  # type: ignore[arg-type]
        log = _capture_log()
        result = dispatcher.dispatch(self._message("ping"), log=log)
        self.assertEqual(result.rule_id, "ping")
        self.assertEqual(result.reply_text, "pong")
        self.assertEqual(result.returncode, 0)

    def test_help_rule_lists_commands(self) -> None:
        dispatcher = ConfiguredDispatcher(runner_factory=lambda: None)  # type: ignore[arg-type]
        log = _capture_log()
        result = dispatcher.dispatch(self._message("help"), log=log)
        self.assertEqual(result.rule_id, "help")
        self.assertIn("ping", result.reply_text)
        self.assertIn("task", result.reply_text)

    def test_task_by_name_unknown(self) -> None:
        dispatcher = ConfiguredDispatcher(runner_factory=lambda: None)  # type: ignore[arg-type]
        log = _capture_log()
        result = dispatcher.dispatch(self._message("task no-such-task"), log=log)
        self.assertEqual(result.rule_id, "task-by-name")
        self.assertIn("Unknown task", result.reply_text)
        self.assertEqual(result.returncode, 2)


class OwnerFilterTests(unittest.TestCase):
    def test_strips_prefix(self) -> None:
        from assistant.devctl.telegram_bridge import _normalize_owner_value

        self.assertEqual(_normalize_owner_value("telegram:12345"), "12345")
        self.assertEqual(_normalize_owner_value("tg:67890"), "67890")
        self.assertEqual(_normalize_owner_value("  98765  "), "98765")

    def test_rejects_non_owner(self) -> None:
        f = OwnerFilter(runner=None, log=None)
        # No openclaw runner; allowlist will be empty; nothing should be authorized.
        self.assertFalse(f.is_authorized(42))
        self.assertFalse(f.is_authorized(0))


class BridgeOrchestrationTests(unittest.TestCase):
    def test_authorized_message_dispatches_and_replies(self) -> None:
        update = _make_update(update_id=1, sender_id=111, chat_id=999, text="ping")
        poller = StubPoller(updates_per_call=[[update]])
        owner = StubOwnerFilter(allowed={111})
        dispatcher = ConfiguredDispatcher(runner_factory=lambda: None)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as tmp:
            offset_store = OffsetStore(path=Path(tmp) / "offset.json")
            log = _capture_log()
            bridge = TelegramBridge(
                poller=poller,
                owner_filter=owner,
                dispatcher=dispatcher,
                offset_store=offset_store,
                log=log,
            )
            outcome = bridge.poll_once()
            self.assertTrue(outcome.polled)
            self.assertEqual(outcome.updates, 1)
            self.assertEqual(outcome.processed, 1)
            self.assertEqual(outcome.sent_ok, 1)
            self.assertEqual(outcome.skipped_unauthorized, 0)
            self.assertEqual(poller.send_calls, [{"chat_id": 999, "text": "pong"}])
            self.assertEqual(offset_store.load(), 2)

    def test_unauthorized_sender_is_skipped_without_reply(self) -> None:
        update = _make_update(update_id=5, sender_id=222, chat_id=888, text="ping")
        poller = StubPoller(updates_per_call=[[update]])
        owner = StubOwnerFilter(allowed={111})
        dispatcher = ConfiguredDispatcher(runner_factory=lambda: None)  # type: ignore[arg-type]
        with tempfile.TemporaryDirectory() as tmp:
            offset_store = OffsetStore(path=Path(tmp) / "offset.json")
            log = _capture_log()
            bridge = TelegramBridge(
                poller=poller,
                owner_filter=owner,
                dispatcher=dispatcher,
                offset_store=offset_store,
                log=log,
            )
            outcome = bridge.poll_once()
            self.assertEqual(outcome.processed, 0)
            self.assertEqual(outcome.skipped_unauthorized, 1)
            self.assertEqual(poller.send_calls, [])  # no leak to unauthorized chat
            # offset still advances so we don't see this update again
            self.assertEqual(offset_store.load(), 6)

    def test_reply_truncates_at_configured_max(self) -> None:
        long_text = "task app-latest-errors"  # task path produces variable output
        # Use the simpler 'ping' but with a manually large reply via dispatcher stub
        update = _make_update(update_id=2, sender_id=111, chat_id=999, text="ping")

        class FixedDispatcher:
            def dispatch(self, message: InboundMessage, *, log: Any) -> DispatchResult:
                return DispatchResult(rule_id="x", reply_text="A" * 10_000, returncode=0)

        poller = StubPoller(updates_per_call=[[update]])
        owner = StubOwnerFilter(allowed={111})
        with tempfile.TemporaryDirectory() as tmp:
            offset_store = OffsetStore(path=Path(tmp) / "offset.json")
            log = _capture_log()
            bridge = TelegramBridge(
                poller=poller,
                owner_filter=owner,
                dispatcher=FixedDispatcher(),
                offset_store=offset_store,
                log=log,
            )
            bridge.poll_once()
        sent = poller.send_calls[0]["text"]
        # Should be no longer than reply_max_chars (3500 in config).
        self.assertLessEqual(len(sent), 3500)


class HashAndStatusTests(unittest.TestCase):
    def test_sender_hash_format(self) -> None:
        value = _sender_hash("12345")
        self.assertTrue(value.startswith("user#"))
        self.assertEqual(len(value), len("user#") + 12)

    def test_configured_rules_count_matches_config(self) -> None:
        rules = configured_rules_as_dicts()
        self.assertGreaterEqual(len(rules), 3)
        kinds = {rule["kind"] for rule in rules}
        self.assertIn("canned", kinds)
        self.assertIn("help", kinds)
        self.assertIn("task-by-name", kinds)
        self.assertIn("openclaw-ask", kinds)


if __name__ == "__main__":
    unittest.main()
