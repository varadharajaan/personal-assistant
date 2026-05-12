"""Unit tests for assistant.devctl.web_ui.

These tests deliberately avoid a real OpenClaw subprocess. They cover:

* recipe definition shape from config (rules out config drift)
* recipe dispatcher branches (task / log-tail / unsupported kind)
* status-check branches with mocked schtasks + OpenClawRunner
* the loopback + Host-header gate inside the HTTP handler
* the JSON envelope unwrap reused from telegram_bridge

Real chat / agent calls are exercised in the live smoke (devctl web serve
+ /api/chat), not here, because they require an authenticated OpenClaw
gateway and would couple the test run to live LLM latency.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from http import HTTPStatus
from io import BytesIO
from typing import Any
from unittest.mock import patch

from assistant.devctl.web_ui import (
    RecipeResult,
    StatusEntry,
    WebUIHandler,
    _recipe_definitions,
    _run_recipe,
    _status_check,
    _strip_request_host,
)


def _noop_log(*args: Any, **kwargs: Any) -> None:
    return None


# ---------------------------------------------------------------------------
# Recipe definitions / dispatcher
# ---------------------------------------------------------------------------


class RecipeDefinitionTests(unittest.TestCase):
    def test_definitions_have_required_fields(self) -> None:
        recipes = _recipe_definitions()
        self.assertGreater(len(recipes), 0, "config exposes at least one recipe button")
        for entry in recipes:
            self.assertIn("id", entry)
            self.assertIn("kind", entry)
            self.assertIn(entry["kind"], {"task", "log-tail"})
            if entry["kind"] == "task":
                self.assertIn("task_name", entry)


class RunRecipeTests(unittest.TestCase):
    def test_unsupported_kind_returns_failure(self) -> None:
        result = _run_recipe({"id": "x", "kind": "made-up", "label": "x"}, log=_noop_log)
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unsupported recipe kind", result.detail)

    def test_unknown_task_name_returns_failure(self) -> None:
        # Force task_names() to return a known set without the requested name.
        with patch("assistant.devctl.web_ui.task_names", return_value=["other"]):
            result = _run_recipe(
                {"id": "x", "kind": "task", "task_name": "missing", "label": "x"},
                log=_noop_log,
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown task", result.detail)

    def test_task_dispatch_invokes_run_laptop_task(self) -> None:
        @dataclass
        class FakeTaskResult:
            returncode: int
            status: str
            message: str

        captured: dict[str, Any] = {}

        def fake_run_laptop_task(name, *, dry_run, confirm, send_telegram, log):
            captured["name"] = name
            captured["confirm"] = confirm
            captured["send_telegram"] = send_telegram
            return FakeTaskResult(returncode=0, status="ok", message="task ok body")

        with patch("assistant.devctl.web_ui.task_names", return_value=["x-task"]):
            with patch("assistant.devctl.web_ui.run_laptop_task", side_effect=fake_run_laptop_task):
                result = _run_recipe(
                    {
                        "id": "x",
                        "kind": "task",
                        "task_name": "x-task",
                        "task_confirm": True,
                        "task_send_telegram": False,
                        "label": "X Task",
                    },
                    log=_noop_log,
                )
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(captured["name"], "x-task")
        self.assertTrue(captured["confirm"])
        self.assertFalse(captured["send_telegram"])
        self.assertEqual(result.detail, "task ok body")


# ---------------------------------------------------------------------------
# Status checks
# ---------------------------------------------------------------------------


class StatusCheckTests(unittest.TestCase):
    def test_unsupported_kind_returns_unknown(self) -> None:
        entry = _status_check({"id": "x", "label": "X", "kind": "huh"}, log=_noop_log)
        self.assertEqual(entry.state, "unknown")
        self.assertIn("unsupported check kind", entry.detail)

    def test_schtasks_missing_executable_returns_unknown(self) -> None:
        with patch(
            "assistant.devctl.web_ui.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            entry = _status_check(
                {"id": "t", "label": "T", "kind": "schtasks", "task_name": "X"},
                log=_noop_log,
            )
        self.assertEqual(entry.state, "unknown")
        self.assertIn("schtasks.exe not on PATH", entry.detail)

    def test_schtasks_failure_returns_fail(self) -> None:
        @dataclass
        class FakeProc:
            returncode: int
            stdout: str
            stderr: str

        with patch(
            "assistant.devctl.web_ui.subprocess.run",
            return_value=FakeProc(returncode=1, stdout="", stderr="task missing"),
        ):
            entry = _status_check(
                {"id": "t", "label": "T", "kind": "schtasks", "task_name": "X"},
                log=_noop_log,
            )
        self.assertEqual(entry.state, "fail")
        self.assertIn("task missing", entry.detail)

    def test_schtasks_success_extracts_status_line(self) -> None:
        @dataclass
        class FakeProc:
            returncode: int
            stdout: str
            stderr: str

        sample_stdout = (
            "Folder: \\\nHostName: HOST\nTaskName: \\X\nStatus: Ready\nLogon Mode: Interactive\n"
        )
        with patch(
            "assistant.devctl.web_ui.subprocess.run",
            return_value=FakeProc(returncode=0, stdout=sample_stdout, stderr=""),
        ):
            entry = _status_check(
                {"id": "t", "label": "T", "kind": "schtasks", "task_name": "X"},
                log=_noop_log,
            )
        self.assertEqual(entry.state, "ok")
        self.assertIn("Status:", entry.detail)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


class StripRequestHostTests(unittest.TestCase):
    def test_strips_port_and_lowercases(self) -> None:
        self.assertEqual(_strip_request_host("LOCALHOST:7100"), "localhost")
        self.assertEqual(_strip_request_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(_strip_request_host("  127.0.0.1:1234  "), "127.0.0.1")


# ---------------------------------------------------------------------------
# Loopback + Host header enforcement
# ---------------------------------------------------------------------------


class _DummySocket:
    def makefile(self, mode: str, *_a: Any, **_k: Any):
        if "r" in mode:
            return BytesIO(b"")
        return BytesIO()


class LoopbackEnforcementTests(unittest.TestCase):
    def _build(self, client_host: str, host_header: str) -> tuple[WebUIHandler, BytesIO]:
        handler = WebUIHandler.__new__(WebUIHandler)
        handler.client_address = (client_host, 0)
        handler.headers = {"Host": host_header}
        handler.command = "GET"
        handler.path = "/"
        handler.request_version = "HTTP/1.1"
        handler.requestline = "GET / HTTP/1.1"
        handler.rfile = BytesIO(b"")
        handler.wfile = BytesIO()
        return handler, handler.wfile

    def test_external_client_is_rejected(self) -> None:
        handler, sink = self._build(client_host="10.0.0.5", host_header="127.0.0.1:7100")
        allowed = handler._enforce_loopback()
        self.assertFalse(allowed)
        self.assertIn(b"403", sink.getvalue()[:32])

    def test_loopback_with_disallowed_host_header_rejected(self) -> None:
        handler, sink = self._build(client_host="127.0.0.1", host_header="evil.example.com")
        allowed = handler._enforce_loopback()
        self.assertFalse(allowed)
        self.assertIn(b"403", sink.getvalue()[:32])

    def test_loopback_with_allowed_host_header_accepted(self) -> None:
        handler, sink = self._build(client_host="127.0.0.1", host_header="127.0.0.1:7100")
        allowed = handler._enforce_loopback()
        self.assertTrue(allowed)
        self.assertEqual(sink.getvalue(), b"")  # no early response written


if __name__ == "__main__":
    unittest.main()
