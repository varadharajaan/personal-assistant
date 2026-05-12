"""Local web UI server (loopback only).

Implements the Phase 6 console described in PLAN.md: a small operator surface
exposing chat, recipe buttons, log tails, and status panels through a single
stdlib :mod:`http.server` process that delegates real work to the existing
``assistant.devctl`` modules. There is no network exposure beyond the
loopback host configured in ``[web_ui]``; an explicit ``Host`` header check
defends against DNS-rebinding even from localhost.

Design rules followed here mirror the rest of the repo:

* Every flow logs through :mod:`shared.python.pa_logging`.
* All paths, timeouts, panels, and recipe buttons come from
  ``config/settings.toml`` -> ``[web_ui]``. No hardcoded values.
* Provider/store/tool adapters honor Liskov substitution: the chat endpoint
  reuses :func:`assistant.devctl.agent_roles.resolve_agent_call` exactly the
  same way the Telegram bridge does.
* Tasks invoked from the UI re-enter the same
  :func:`assistant.devctl.laptop_tasks.run_laptop_task` codepath as the
  Telegram bridge so safety gates and confirms cannot diverge.
"""

from __future__ import annotations

import html
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

from shared.python.pa_logging import get_logger

from .agent_roles import resolve_agent_call
from .config import (
    get_bool,
    get_int,
    get_list,
    get_path,
    get_str,
)
from .laptop_tasks import run_laptop_task, task_definition, task_names
from .log_inspector import format_log_lines, read_tail, tail_lines
from .openclaw_runner import OpenClawRunner
from .telegram_bridge import _extract_openclaw_reply  # safe internal reuse


LogFn = Callable[..., None]


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatResult:
    """Outcome of one chat turn."""

    ok: bool
    reply: str
    role: str
    agent: str
    model: str | None
    thinking: str | None
    elapsed_ms: int
    raw_returncode: int
    stderr_preview: str = ""


@dataclass(frozen=True)
class RecipeResult:
    """Outcome of one recipe-button invocation."""

    ok: bool
    title: str
    detail: str
    returncode: int
    elapsed_ms: int


@dataclass(frozen=True)
class StatusEntry:
    """One row of the status panel."""

    id: str
    label: str
    state: str  # "ok" | "warn" | "fail" | "unknown"
    detail: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _strip_request_host(raw_host: str) -> str:
    """Normalise the Host header value to the bare hostname."""

    return raw_host.split(":", 1)[0].strip().lower()


def _make_log_fn() -> LogFn:
    flow = get_str("flows.web_ui")
    return get_logger(flow)


# ---------------------------------------------------------------------------
# Chat handler
# ---------------------------------------------------------------------------


def _chat_dispatch(message: str, role_name: str, *, log: LogFn) -> ChatResult:
    """Run one OpenClaw agent turn and return the parsed reply."""

    if not message.strip():
        return ChatResult(
            ok=False,
            reply="empty message",
            role=role_name,
            agent="",
            model=None,
            thinking=None,
            elapsed_ms=0,
            raw_returncode=2,
        )

    resolved = resolve_agent_call(
        role_name=role_name,
        explicit_agent=None,
        explicit_model=None,
        explicit_thinking=None,
    )

    runner = OpenClawRunner(log=log)
    args = ["agent", "--message", message, "--json", "--agent", resolved.agent]
    if resolved.model:
        args.extend(["--model", resolved.model])
    if resolved.thinking:
        args.extend(["--thinking", resolved.thinking])

    timeout = get_int("web_ui.chat_default_timeout_seconds")
    started = _now_ms()
    result = runner.run(args, timeout_seconds=timeout)
    elapsed = _now_ms() - started

    if result.returncode != 0:
        stderr_preview = (result.stderr or "").strip()[:400]
        return ChatResult(
            ok=False,
            reply=stderr_preview or f"openclaw returned {result.returncode}",
            role=resolved.role.name,
            agent=resolved.agent,
            model=resolved.model,
            thinking=resolved.thinking,
            elapsed_ms=elapsed,
            raw_returncode=result.returncode,
            stderr_preview=stderr_preview,
        )

    reply = _extract_openclaw_reply(result)
    cap = get_int("web_ui.chat_reply_max_chars")
    if cap > 0 and len(reply) > cap:
        reply = reply[:cap] + "\n\n…[truncated]"
    return ChatResult(
        ok=True,
        reply=reply,
        role=resolved.role.name,
        agent=resolved.agent,
        model=resolved.model,
        thinking=resolved.thinking,
        elapsed_ms=elapsed,
        raw_returncode=0,
    )


# ---------------------------------------------------------------------------
# Recipe button dispatcher
# ---------------------------------------------------------------------------


def _recipe_definitions() -> list[dict[str, Any]]:
    return [dict(entry) for entry in get_list("web_ui.recipe_buttons")]


def _run_recipe(definition: dict[str, Any], *, log: LogFn) -> RecipeResult:
    kind = str(definition.get("kind", "")).strip().lower()
    label = str(definition.get("label") or definition.get("id") or "recipe")
    started = _now_ms()

    if kind == "task":
        task_name = str(definition.get("task_name") or "")
        if task_name not in task_names():
            return RecipeResult(
                ok=False,
                title=label,
                detail=f"unknown task: {task_name}",
                returncode=2,
                elapsed_ms=_now_ms() - started,
            )
        result = run_laptop_task(
            task_name,
            dry_run=False,
            confirm=bool(definition.get("task_confirm", False)),
            send_telegram=bool(definition.get("task_send_telegram", False)),
            log=log,
        )
        detail = (result.message or "").strip()
        return RecipeResult(
            ok=(result.returncode == 0),
            title=label,
            detail=detail or f"task: {task_name} / status: {result.status}",
            returncode=result.returncode,
            elapsed_ms=_now_ms() - started,
        )

    if kind == "log-tail":
        source = str(definition.get("source") or "personal-assistant")
        flow = definition.get("flow")
        lines = int(definition.get("lines", 80))
        errors_only = bool(definition.get("errors_only", False))
        try:
            results = tail_lines(
                source=source,
                lines=lines,
                contains=None,
                errors_only=errors_only,
            )
            text = format_log_lines(results)
            if flow:
                # Filter to lines whose file basename matches the requested flow.
                wanted = f"{flow}.log".lower()
                text = "\n".join(
                    line for line in text.splitlines() if wanted in line.lower()
                )
        except Exception as exc:  # defensive; tail must never crash the UI
            return RecipeResult(
                ok=False,
                title=label,
                detail=f"log tail failed: {exc}",
                returncode=1,
                elapsed_ms=_now_ms() - started,
            )
        return RecipeResult(
            ok=True,
            title=label,
            detail=text or "(no matching log lines)",
            returncode=0,
            elapsed_ms=_now_ms() - started,
        )

    return RecipeResult(
        ok=False,
        title=label,
        detail=f"unsupported recipe kind: {kind}",
        returncode=2,
        elapsed_ms=_now_ms() - started,
    )


# ---------------------------------------------------------------------------
# Status panel
# ---------------------------------------------------------------------------


def _status_check(check: dict[str, Any], *, log: LogFn) -> StatusEntry:
    cid = str(check.get("id") or "check")
    label = str(check.get("label") or cid)
    kind = str(check.get("kind") or "").strip().lower()

    if kind == "schtasks":
        task_name = str(check.get("task_name") or "")
        if not task_name:
            return StatusEntry(id=cid, label=label, state="unknown", detail="no task_name configured")
        try:
            proc = subprocess.run(
                ["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except FileNotFoundError:
            return StatusEntry(id=cid, label=label, state="unknown", detail="schtasks.exe not on PATH")
        except subprocess.TimeoutExpired:
            return StatusEntry(id=cid, label=label, state="warn", detail="schtasks query timed out")
        if proc.returncode != 0:
            return StatusEntry(
                id=cid,
                label=label,
                state="fail",
                detail=(proc.stderr or proc.stdout or "task missing").strip().splitlines()[0][:200],
            )
        status_line = next(
            (l.strip() for l in proc.stdout.splitlines() if l.strip().startswith("Status:")),
            "",
        )
        return StatusEntry(
            id=cid,
            label=label,
            state="ok",
            detail=status_line or "registered",
        )

    if kind == "openclaw-cmd":
        args = [str(a) for a in (check.get("args") or [])]
        timeout = int(check.get("timeout", 30))
        runner = OpenClawRunner(log=log)
        try:
            result = runner.run(args, timeout_seconds=timeout)
        except Exception as exc:
            return StatusEntry(id=cid, label=label, state="fail", detail=str(exc)[:200])
        if result.returncode != 0:
            return StatusEntry(
                id=cid,
                label=label,
                state="fail",
                detail=(result.stderr or "non-zero return").strip()[:200],
            )
        return StatusEntry(
            id=cid,
            label=label,
            state="ok",
            detail=(result.stdout or "").strip().splitlines()[0][:200],
        )

    return StatusEntry(id=cid, label=label, state="unknown", detail=f"unsupported check kind: {kind}")


def _status_snapshot(log: LogFn) -> list[StatusEntry]:
    return [_status_check(dict(c), log=log) for c in get_list("web_ui.status_checks")]


# ---------------------------------------------------------------------------
# Log panels
# ---------------------------------------------------------------------------


def _log_panel(panel: dict[str, Any], *, log: LogFn) -> dict[str, Any]:
    pid = str(panel.get("id") or panel.get("flow") or "log")
    label = str(panel.get("label") or pid)
    flow = str(panel.get("flow") or "_session")
    max_lines = int(panel.get("max_lines", 200))
    text = ""
    try:
        unified_root = get_path("paths.unified_logs_dir")
        target = unified_root / f"{flow}.log"
        if target.is_file():
            raw = read_tail(target)
            text = "\n".join(raw.splitlines()[-max_lines:])
    except Exception as exc:
        text = f"(log tail failed: {exc})"
    return {
        "id": pid,
        "label": label,
        "flow": flow,
        "text": text or "(no recent log lines)",
    }


def _all_log_panels(log: LogFn) -> list[dict[str, Any]]:
    return [_log_panel(dict(p), log=log) for p in get_list("web_ui.log_panels")]


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class WebUIHandler(BaseHTTPRequestHandler):
    server_version = "PersonalAssistantWebUI/1.0"

    # Class attribute populated by ``serve``. Accessed via ``type(self).log``
    # rather than ``self.log`` to avoid the descriptor protocol turning a
    # plain function attribute into a bound method (which would inject
    # ``self`` as the first positional arg to the logger).
    log_fn: staticmethod = staticmethod(lambda *a, **k: None)

    # ---- HTTP helpers -----------------------------------------------------

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: HTTPStatus, html_body: str) -> None:
        body = html_body.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: HTTPStatus, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---- Request gates ----------------------------------------------------

    def _enforce_loopback(self) -> bool:
        client_host = self.client_address[0]
        if client_host not in ("127.0.0.1", "::1"):
            self._send_text(HTTPStatus.FORBIDDEN, get_str("web_ui.unauthorized_message"))
            return False
        host_header = self.headers.get("Host", "")
        allowed = {str(h).lower() for h in get_list("web_ui.allowed_hosts")}
        if _strip_request_host(host_header) not in allowed:
            self._send_text(HTTPStatus.FORBIDDEN, get_str("web_ui.unauthorized_message"))
            return False
        return True

    # ---- Logging override -------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Route HTTP access lines through the unified logger instead of stderr.
        try:
            type(self).log_fn(
                "web ui http request",
                level="INFO",
                client=self.client_address[0],
                line=(format % args)[:200],
            )
        except Exception:
            pass

    # ---- Routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._enforce_loopback():
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._serve_index()
            return
        if parsed.path == "/api/recipes":
            self._send_json(HTTPStatus.OK, {"recipes": _recipe_definitions()})
            return
        if parsed.path == "/api/status":
            entries = [e.__dict__ for e in _status_snapshot(type(self).log_fn)]
            self._send_json(HTTPStatus.OK, {"entries": entries})
            return
        if parsed.path == "/api/logs":
            self._send_json(HTTPStatus.OK, {"panels": _all_log_panels(type(self).log_fn)})
            return
        if parsed.path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "ts": int(time.time())})
            return
        self._send_text(HTTPStatus.NOT_FOUND, get_str("web_ui.not_found_message"))

    def do_POST(self) -> None:  # noqa: N802
        if not self._enforce_loopback():
            return
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid JSON body"})
            return

        if parsed.path == "/api/chat":
            message = str(body.get("message") or "").strip()
            cap = get_int("web_ui.chat_max_message_chars")
            if cap > 0 and len(message) > cap:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": f"message exceeds {cap} chars"},
                )
                return
            role = str(body.get("role") or get_str("web_ui.chat_default_role"))
            type(self).log_fn(
                "web ui chat dispatch",
                level="INFO",
                role=role,
                message_chars=len(message),
            )
            outcome = _chat_dispatch(message, role, log=type(self).log_fn)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": outcome.ok,
                    "reply": outcome.reply,
                    "role": outcome.role,
                    "agent": outcome.agent,
                    "model": outcome.model,
                    "thinking": outcome.thinking,
                    "elapsed_ms": outcome.elapsed_ms,
                    "returncode": outcome.raw_returncode,
                },
            )
            return

        if parsed.path == "/api/recipe":
            recipe_id = str(body.get("id") or "")
            recipes = _recipe_definitions()
            match = next((r for r in recipes if str(r.get("id")) == recipe_id), None)
            if not match:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"ok": False, "error": f"unknown recipe id: {recipe_id}"},
                )
                return
            type(self).log_fn(
                "web ui recipe dispatch",
                level="INFO",
                recipe_id=recipe_id,
                kind=match.get("kind"),
            )
            result = _run_recipe(match, log=type(self).log_fn)
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": result.ok,
                    "title": result.title,
                    "detail": result.detail,
                    "returncode": result.returncode,
                    "elapsed_ms": result.elapsed_ms,
                },
            )
            return

        self._send_text(HTTPStatus.NOT_FOUND, get_str("web_ui.not_found_message"))

    # ---- Index page -------------------------------------------------------

    def _serve_index(self) -> None:
        templates_dir = get_path("web_ui.templates_dir")
        index_path = Path(templates_dir) / "index.html"
        if not index_path.is_file():
            self._send_text(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"index template missing: {index_path}",
            )
            return
        try:
            html_body = index_path.read_text(encoding="utf-8")
        except OSError as exc:
            self._send_text(HTTPStatus.INTERNAL_SERVER_ERROR, f"failed to read template: {exc}")
            return
        title = html.escape(get_str("web_ui.title"))
        html_body = html_body.replace("{{ TITLE }}", title)
        self._send_html(HTTPStatus.OK, html_body)


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------


def serve(*, log: LogFn | None = None, host: str | None = None, port: int | None = None) -> int:
    """Blocking server entrypoint. Returns an exit code."""

    log = log or _make_log_fn()

    if not get_bool("web_ui.enabled"):
        log("web ui disabled by config", level="WARN")
        sys.stderr.write(get_str("web_ui.disabled_message") + "\n")
        return 2

    bind_host = host or get_str("web_ui.host")
    bind_port = port or get_int("web_ui.port")

    handler_log = log

    class _BoundHandler(WebUIHandler):
        log = handler_log  # type: ignore[assignment]

    address = (bind_host, bind_port)
    try:
        httpd = ThreadingHTTPServer(address, _BoundHandler)
    except OSError as exc:
        log(
            "web ui bind failed",
            level="ERROR",
            host=bind_host,
            port=bind_port,
            error=str(exc)[:200],
        )
        sys.stderr.write(f"web ui bind failed: {exc}\n")
        return 1

    banner = get_str("web_ui.serve_banner").format(host=bind_host, port=bind_port)
    log(
        "web ui server starting",
        level="OK",
        host=bind_host,
        port=bind_port,
    )
    sys.stdout.write(banner + "\n")
    sys.stdout.flush()

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        log("web ui server stopped by interrupt", level="INFO")
        return 0
    finally:
        try:
            httpd.server_close()
        except Exception:
            pass
    return 0


def info() -> dict[str, Any]:
    """Return the configured surface for ``devctl web info``."""

    return {
        "enabled": get_bool("web_ui.enabled"),
        "host": get_str("web_ui.host"),
        "port": get_int("web_ui.port"),
        "allowed_hosts": list(get_list("web_ui.allowed_hosts")),
        "templates_dir": str(get_path("web_ui.templates_dir")),
        "title": get_str("web_ui.title"),
        "chat_default_role": get_str("web_ui.chat_default_role"),
        "recipe_count": len(_recipe_definitions()),
        "log_panel_count": len(get_list("web_ui.log_panels")),
        "status_check_count": len(get_list("web_ui.status_checks")),
    }
