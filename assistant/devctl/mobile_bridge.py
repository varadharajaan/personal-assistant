"""Local HTTP bridge for mobile command capture."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .config import get_bool, get_int, get_list, get_str
from .mobile_inbox import MobileCommand, capture_command

LogFn = Callable[..., None]


@dataclass(frozen=True)
class BridgeCapture:
    command: MobileCommand
    response: dict[str, object]


@dataclass(frozen=True)
class CaptureFields:
    message: str
    sender: str
    source: str
    channel: str


def _first_string(payload: dict[str, object], field_key: str, default: str = "") -> str:
    for field in get_list(field_key):
        value = payload.get(str(field))
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _configured_token() -> str:
    return os.environ.get(get_str("mobile_bridge.token_env_var"), "").strip()


def _token_from_header(value: str) -> str:
    header = value.strip()
    prefix = get_str("mobile_bridge.bearer_prefix")
    if prefix and header.startswith(prefix):
        return header[len(prefix) :].strip()
    return header


def bridge_url() -> str:
    return f"http://{get_str('mobile_bridge.host')}:{get_int('mobile_bridge.port')}{get_str('mobile_bridge.path')}"


def health_url() -> str:
    return f"http://{get_str('mobile_bridge.host')}:{get_int('mobile_bridge.port')}{get_str('mobile_bridge.health_path')}"


def validate_bridge_token(header_value: str | None) -> tuple[bool, str]:
    if not get_bool("mobile_bridge.require_token"):
        return True, ""
    expected = _configured_token()
    if not expected:
        return False, "bridge token is required but not configured"
    if not header_value:
        return False, "missing bridge token"
    if _token_from_header(header_value) != expected:
        return False, "invalid bridge token"
    return True, ""


def payload_to_fields(payload: dict[str, object]) -> CaptureFields:
    message = _first_string(payload, "mobile_bridge.message_fields")
    if not message:
        raise ValueError("Payload must include a non-empty message field.")
    return CaptureFields(
        message=message,
        sender=_first_string(payload, "mobile_bridge.sender_fields", get_str("mobile_bridge.default_sender")),
        source=_first_string(payload, "mobile_bridge.source_fields", get_str("mobile_bridge.source")),
        channel=_first_string(payload, "mobile_bridge.channel_fields", get_str("mobile_bridge.channel")),
    )


def capture_payload(payload: dict[str, object], *, log: LogFn) -> BridgeCapture:
    fields = payload_to_fields(payload)
    command = capture_command(
        source=fields.source,
        sender=fields.sender,
        channel=fields.channel,
        text=fields.message,
        log=log,
    )
    response = {
        "status": get_str("mobile_bridge.response_ok_status"),
        "id": command.id,
        "source": command.source,
        "channel": command.channel,
        "received_at": command.received_at,
    }
    log("mobile bridge payload captured", level="OK", command_id=command.id, source=fields.source, channel=fields.channel)
    return BridgeCapture(command=command, response=response)


class _MobileBridgeHandler(BaseHTTPRequestHandler):
    server_version = "PersonalAssistantMobileBridge"

    def do_GET(self) -> None:
        if self.path != get_str("mobile_bridge.health_path"):
            self._send_json(404, {"error": "not found"})
            return
        self._send_json(
            200,
            {
                "status": "ok",
                "capture_url": bridge_url(),
                "token_required": get_bool("mobile_bridge.require_token"),
            },
        )

    def do_POST(self) -> None:
        if self.path != get_str("mobile_bridge.path"):
            self._send_json(404, {"error": "not found"})
            return
        allowed, reason = validate_bridge_token(self.headers.get(get_str("mobile_bridge.auth_header")))
        if not allowed:
            self.server.log("mobile bridge auth rejected", level="WARN", reason=reason)  # type: ignore[attr-defined]
            self._send_json(401, {"error": reason})
            return
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            self._send_json(400, {"error": "empty body"})
            return
        if content_length > get_int("mobile_bridge.max_body_bytes"):
            self._send_json(413, {"error": "payload too large"})
            return
        raw = self.rfile.read(content_length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "json body must be an object"})
            return
        try:
            captured = capture_payload(payload, log=self.server.log)  # type: ignore[attr-defined]
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, captured.response)
        if getattr(self.server, "once", False):
            self.server.should_stop = True  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        self.server.log("mobile bridge http request", path=self.path, request_message=format % args)  # type: ignore[attr-defined]

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, indent=get_int("mobile_bridge.json_indent")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MobileBridgeServer(ThreadingHTTPServer):
    def __init__(self, log: LogFn, *, once: bool = False) -> None:
        super().__init__((get_str("mobile_bridge.host"), get_int("mobile_bridge.port")), _MobileBridgeHandler)
        self.log = log
        self.once = once
        self.should_stop = False
        self.timeout = (
            get_int("mobile_bridge.once_poll_seconds")
            if once
            else get_int("mobile_bridge.request_timeout_seconds")
        )


def serve_mobile_bridge(*, log: LogFn, once: bool = False) -> None:
    server = MobileBridgeServer(log, once=once)
    log(
        "mobile bridge started",
        level="OK",
        url=bridge_url(),
        health_url=health_url(),
        once=once,
        token_required=get_bool("mobile_bridge.require_token"),
        bind_warning=get_str("mobile_bridge.bind_warning"),
    )
    try:
        if once:
            while not server.should_stop:
                server.handle_request()
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        log("mobile bridge interrupted", level="WARN")
    finally:
        server.server_close()
        log("mobile bridge stopped", level="OK")
