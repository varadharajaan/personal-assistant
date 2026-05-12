"""Independent Telegram bridge.

Long-polls Telegram's ``getUpdates`` directly (no OpenClaw gateway dependency),
filters incoming messages to the approved command owner, dispatches them to
configured rules (canned, help, laptop task, or OpenClaw agent), and replies
through the Telegram Bot API.

Design:

- ``TelegramPoller`` owns Telegram HTTP I/O (``getUpdates`` + ``sendMessage``).
- ``OwnerFilter`` decides whether a sender is the approved owner.
- ``Dispatcher`` maps inbound text to a configured rule and produces a reply.
- ``TelegramBridge`` orchestrates one poll cycle and the long-running loop.

All tunables come from ``[telegram_bridge]`` and ``[[telegram_bridge.rules]]``.
The token is read only from the configured token file. No secret is logged.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .config import get_bool, get_int, get_list, get_path, get_str, get_table
from .agent_roles import resolve_agent_call
from .laptop_tasks import run_laptop_task, task_definition, task_names
from .mobile_owner import command_owner_status
from .openclaw_runner import CommandResult, OpenClawRunner

LogFn = Callable[..., None]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboundMessage:
    update_id: int
    chat_id: int
    sender_id: int
    sender_username: str
    text: str
    raw_date: int


@dataclass(frozen=True)
class DispatchResult:
    rule_id: str
    reply_text: str
    returncode: int
    detail: str = ""


@dataclass(frozen=True)
class SendResult:
    ok: bool
    returncode: int
    detail: str


@dataclass(frozen=True)
class CycleOutcome:
    polled: bool
    updates: int
    processed: int
    skipped_unauthorized: int
    sent_ok: int
    sent_failed: int
    error: str = ""


@dataclass
class BridgeRule:
    id: str
    match: str
    patterns: list[str]
    description: str
    kind: str
    reply: str = ""
    role: str = ""
    timeout_seconds: int = 0
    task_name: str = ""
    task_confirm: bool = False
    task_send_telegram: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_token() -> str:
    token = get_path("telegram_bridge.token_file").read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("Telegram bridge token file is empty.")
    return token


def _sender_hash(value: str) -> str:
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[
        : get_int("telegram_bridge.sender_hash_chars")
    ]
    return f"{get_str('telegram_bridge.sender_hash_prefix')}{digest}"


def _bot_api_url(token: str, method: str) -> str:
    base = get_str("telegram_bridge.bot_api_base_url").rstrip("/")
    return f"{base}/bot{token}/{method}"


def _redact_token_in(text: str, token: str) -> str:
    if not token:
        return text
    return text.replace(token, "<redacted-token>")


def _load_rules() -> list[BridgeRule]:
    raw_rules = get_list("telegram_bridge.rules")
    rules: list[BridgeRule] = []
    for entry in raw_rules:
        if not isinstance(entry, dict):
            continue
        rules.append(
            BridgeRule(
                id=str(entry.get("id", "")),
                match=str(entry.get("match", "exact")),
                patterns=[str(item) for item in entry.get("patterns", []) or []],
                description=str(entry.get("description", "")),
                kind=str(entry.get("kind", "canned")),
                reply=str(entry.get("reply", "")),
                role=str(entry.get("role", "")),
                timeout_seconds=int(entry.get("timeout_seconds", 0) or 0),
                task_name=str(entry.get("task_name", "")),
                task_confirm=bool(entry.get("task_confirm", False)),
                task_send_telegram=bool(entry.get("task_send_telegram", False)),
            )
        )
    return rules


# ---------------------------------------------------------------------------
# Offset persistence
# ---------------------------------------------------------------------------


class OffsetStore:
    """Persisted long-poll offset so restarts don't replay or drop updates."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else get_path("telegram_bridge.offset_state_file")

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> int:
        try:
            if not self._path.is_file():
                return 0
            data = json.loads(self._path.read_text(encoding="utf-8"))
            value = int(data.get("offset", 0))
            return max(0, value)
        except (OSError, ValueError, json.JSONDecodeError):
            return 0

    def save(self, offset: int) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"offset": int(offset), "updated_at": datetime.now().isoformat(timespec="seconds")}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(self._path)


# ---------------------------------------------------------------------------
# Telegram HTTP client
# ---------------------------------------------------------------------------


class TelegramPoller:
    """Thin Telegram Bot API client with no OpenClaw dependency."""

    def __init__(self, token: str) -> None:
        self._token = token

    @property
    def token(self) -> str:
        return self._token

    def get_updates(self, *, offset: int, allowed_updates: list[str], long_timeout: int, http_timeout: int) -> list[dict[str, Any]]:
        method = get_str("telegram_bridge.get_updates_method")
        params = {
            "timeout": str(long_timeout),
            "allowed_updates": json.dumps(allowed_updates),
        }
        if offset > 0:
            params["offset"] = str(offset)
        url = _bot_api_url(self._token, method) + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=http_timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(body)
        if not parsed.get("ok"):
            raise RuntimeError(f"getUpdates failed: {parsed.get('description', '')}")
        return list(parsed.get("result", []))

    def send_message(self, *, chat_id: int, text: str, http_timeout: int) -> SendResult:
        method = get_str("telegram_bridge.send_message_method")
        payload_dict: dict[str, str] = {"chat_id": str(chat_id), "text": text}
        parse_mode = get_str("telegram_bridge.reply_parse_mode")
        if parse_mode:
            payload_dict["parse_mode"] = parse_mode
        data = urllib.parse.urlencode(payload_dict).encode("utf-8")
        request = urllib.request.Request(
            _bot_api_url(self._token, method),
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=http_timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
            ok = bool(parsed.get("ok"))
            return SendResult(
                ok=ok,
                returncode=0 if ok else 1,
                detail=str(parsed.get("description", "")),
            )
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body = ""
            return SendResult(ok=False, returncode=exc.code or 1, detail=body[:500])
        except urllib.error.URLError as exc:
            return SendResult(ok=False, returncode=1, detail=str(exc.reason)[:500])


# ---------------------------------------------------------------------------
# Owner filter
# ---------------------------------------------------------------------------


def _normalize_owner_value(value: str) -> str:
    cleaned = value.strip()
    for prefix in get_list("telegram_bridge.owner_prefixes_to_strip"):
        prefix_str = str(prefix)
        if cleaned.lower().startswith(prefix_str.lower()):
            cleaned = cleaned[len(prefix_str) :]
            break
    return cleaned.strip()


class OwnerFilter:
    """Decides whether an inbound sender is the approved command owner.

    For ``owner_source = "openclaw-command-owner"`` it reads OpenClaw's
    redacted owner ids from ``mobile_owner_status`` and pulls the raw numeric
    ids from ``[mobile_owner].owner_allow_from`` when configured. Otherwise it
    falls back to ``[telegram_bridge].explicit_owner_ids``.

    OpenClaw's status returns hashed owners by default; raw matching uses the
    sender's numeric Telegram id against the configured raw allowlist.
    """

    def __init__(self, *, runner: OpenClawRunner | None = None, log: LogFn | None = None) -> None:
        self._runner = runner
        self._log = log
        self._cache_at = 0.0
        self._cache: list[str] = []

    def _load_owner_ids(self) -> list[str]:
        now = time.monotonic()
        cache_seconds = get_int("telegram_bridge.owner_cache_seconds")
        if self._cache and (now - self._cache_at) < cache_seconds:
            return self._cache

        source = get_str("telegram_bridge.owner_source")
        ids: list[str] = []
        if source == "openclaw-command-owner":
            # First try the repo-config raw allowlist (kept empty by default,
            # but a user may populate it explicitly).
            for value in get_list("mobile_owner.owner_allow_from"):
                normalized = _normalize_owner_value(str(value))
                if normalized:
                    ids.append(normalized)
            # If repo allowlist is empty, query OpenClaw directly for the raw
            # ownerAllowFrom array. This bypasses the mobile_owner status path
            # which redacts ids for log safety.
            if not ids and self._runner is not None:
                raw_ids = _read_raw_owner_ids_from_openclaw(
                    runner=self._runner,
                    log=self._log,
                )
                for value in raw_ids:
                    normalized = _normalize_owner_value(value)
                    if normalized:
                        ids.append(normalized)
        # Always allow explicit override list to extend or substitute.
        for value in get_list("telegram_bridge.explicit_owner_ids"):
            normalized = _normalize_owner_value(str(value))
            if normalized and normalized not in ids:
                ids.append(normalized)

        self._cache = ids
        self._cache_at = now
        if self._log is not None:
            self._log(
                "telegram bridge owner ids loaded",
                level="OK" if ids else "WARN",
                owner_count=len(ids),
                source=source,
            )
        return ids

    def is_authorized(self, sender_id: int) -> bool:
        if sender_id <= 0:
            return False
        for owner in self._load_owner_ids():
            if owner == str(sender_id):
                return True
        return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher(Protocol):
    def dispatch(self, message: InboundMessage, *, log: LogFn) -> DispatchResult:
        ...


class ConfiguredDispatcher:
    """Rule-driven dispatcher that reuses devctl building blocks.

    Kinds:
    - ``canned``: reply with a configured static string.
    - ``help``: build help text from the rule list.
    - ``task-by-name``: parse ``task <name> [confirm]`` and call ``run_laptop_task``.
    - ``openclaw-ask``: call ``OpenClawRunner.agent`` for the message body.
    """

    def __init__(self, *, runner_factory: Callable[[], OpenClawRunner] | None = None) -> None:
        self._runner_factory = runner_factory or (lambda: OpenClawRunner(_noop_log))

    def _match_rule(self, text: str, rules: list[BridgeRule]) -> tuple[BridgeRule | None, str]:
        lowered = text.strip()
        fallback: BridgeRule | None = None
        for rule in rules:
            if rule.match == "exact":
                for pattern in rule.patterns:
                    if lowered.lower() == pattern.lower():
                        return rule, ""
            elif rule.match == "prefix":
                for pattern in rule.patterns:
                    if lowered.lower().startswith(pattern.lower()):
                        return rule, lowered[len(pattern) :].strip()
            elif rule.match == "fallback":
                fallback = rule
        if fallback is not None:
            return fallback, lowered
        return None, lowered

    def _help_reply(self, rules: list[BridgeRule]) -> str:
        line_template = get_str("telegram_bridge.help_command_line_template")
        lines: list[str] = []
        for rule in rules:
            if rule.kind == "help" or not rule.patterns:
                continue
            pattern = rule.patterns[0]
            lines.append(line_template.format(pattern=pattern, description=rule.description or rule.kind))
        return get_str("telegram_bridge.help_reply_template").format(commands="\n".join(lines))

    def _format_task_reply(self, name: str, result: Any) -> str:
        lines = [f"task: {name}", f"status: {result.status}", f"returncode: {result.returncode}"]
        if result.message:
            lines.append("output:")
            lines.append(result.message)
        return "\n".join(lines)

    def _handle_shortcut_task(self, rule: BridgeRule, log: LogFn) -> DispatchResult:
        name = rule.task_name
        if not name or name not in task_names():
            return DispatchResult(
                rule_id=rule.id,
                reply_text=get_str("telegram_bridge.task_unknown_reply_template").format(name=name),
                returncode=2,
            )
        result = run_laptop_task(
            name,
            dry_run=False,
            confirm=rule.task_confirm,
            send_telegram=rule.task_send_telegram,
            log=log,
        )
        return DispatchResult(
            rule_id=rule.id,
            reply_text=self._format_task_reply(name, result),
            returncode=result.returncode,
            detail=result.status,
        )

    def _handle_task(self, rule: BridgeRule, payload: str, log: LogFn) -> DispatchResult:
        parts = payload.split()
        if not parts:
            return DispatchResult(
                rule_id=rule.id,
                reply_text=get_str("telegram_bridge.task_unknown_reply_template").format(name=""),
                returncode=2,
            )
        name = parts[0]
        confirm_keywords = {str(k).lower() for k in get_list("telegram_bridge.task_confirm_keywords")}
        confirm = any(token.lower() in confirm_keywords for token in parts[1:])
        if name not in task_names():
            return DispatchResult(
                rule_id=rule.id,
                reply_text=get_str("telegram_bridge.task_unknown_reply_template").format(name=name),
                returncode=2,
            )
        definition = task_definition(name)
        if definition.get("requires_confirm") and not confirm:
            return DispatchResult(
                rule_id=rule.id,
                reply_text=get_str("telegram_bridge.task_refused_reply_template").format(name=name),
                returncode=2,
            )
        result = run_laptop_task(
            name,
            dry_run=False,
            confirm=confirm,
            send_telegram=get_bool("telegram_bridge.task_send_telegram_via_dispatch"),
            log=log,
        )
        return DispatchResult(
            rule_id=rule.id,
            reply_text=self._format_task_reply(name, result),
            returncode=result.returncode,
            detail=result.status,
        )

    def _handle_openclaw_ask(self, rule: BridgeRule, payload: str, log: LogFn) -> DispatchResult:
        message = payload.strip()
        if not message:
            return DispatchResult(rule_id=rule.id, reply_text="Empty question.", returncode=2)
        runner = self._runner_factory()
        timeout_seconds = rule.timeout_seconds or get_int("telegram_bridge.dispatch_openclaw_ask_timeout_seconds")
        role_name = rule.role or get_str("telegram_bridge.dispatch_default_role")
        try:
            resolved = resolve_agent_call(
                role_name=role_name,
                explicit_agent=None,
                explicit_model=None,
                explicit_thinking=None,
            )
        except Exception as exc:
            log("telegram bridge role resolve failed", level="WARN", role=role_name, error=str(exc)[:200])
            resolved = None
        try:
            result = runner.agent(
                message,
                agent=resolved.agent if resolved else None,
                model=resolved.model if resolved else None,
                thinking=resolved.thinking if resolved else None,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            log("telegram bridge openclaw call raised", level="ERROR", error=str(exc)[:200])
            return DispatchResult(
                rule_id=rule.id,
                reply_text=get_str("telegram_bridge.openclaw_unavailable_reply"),
                returncode=1,
                detail=str(exc)[:200],
            )
        reply_text = _extract_openclaw_reply(result) or get_str("telegram_bridge.openclaw_unavailable_reply")
        return DispatchResult(
            rule_id=rule.id,
            reply_text=reply_text,
            returncode=result.returncode,
            detail=f"openclaw rc={result.returncode}",
        )

    def dispatch(self, message: InboundMessage, *, log: LogFn) -> DispatchResult:
        rules = _load_rules()
        rule, payload = self._match_rule(message.text, rules)
        if rule is None:
            return DispatchResult(
                rule_id="unknown",
                reply_text=get_str("telegram_bridge.unknown_command_reply"),
                returncode=2,
            )

        log(
            "telegram bridge rule matched",
            rule_id=rule.id,
            kind=rule.kind,
            payload_chars=len(payload),
        )
        if rule.kind == "canned":
            return DispatchResult(rule_id=rule.id, reply_text=rule.reply, returncode=0)
        if rule.kind == "help":
            return DispatchResult(rule_id=rule.id, reply_text=self._help_reply(rules), returncode=0)
        if rule.kind == "task":
            return self._handle_shortcut_task(rule, log)
        if rule.kind == "task-by-name":
            return self._handle_task(rule, payload, log)
        if rule.kind == "openclaw-ask":
            return self._handle_openclaw_ask(rule, payload, log)
        return DispatchResult(
            rule_id=rule.id,
            reply_text=get_str("telegram_bridge.unknown_command_reply"),
            returncode=2,
        )


def _extract_openclaw_reply(result: CommandResult) -> str:
    stdout = result.stdout.strip()
    if not stdout:
        return result.stderr.strip()
    # OpenClaw agent returns JSON; try parse and pull the reply.
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout
    if isinstance(parsed, dict):
        for key in ("reply", "response", "message", "text", "output"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value
        # OpenClaw 2026.5.x: result.payloads[].text
        nested_result = parsed.get("result")
        if isinstance(nested_result, dict):
            payloads = nested_result.get("payloads")
            if isinstance(payloads, list):
                parts: list[str] = []
                for item in payloads:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
                if parts:
                    return "\n\n".join(parts)
            for key in ("text", "message", "reply", "output"):
                value = nested_result.get(key)
                if isinstance(value, str) and value.strip():
                    return value
        # Some OpenClaw versions wrap content in result.choices[].
        choices = parsed.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                for key in ("text", "message", "content"):
                    value = first.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
                # Chat-completion style: choices[0].message.content
                msg = first.get("message")
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        return content
    return stdout


def _noop_log(*args: Any, **kwargs: Any) -> None:
    return None


def _read_raw_owner_ids_from_openclaw(*, runner: OpenClawRunner, log: LogFn | None) -> list[str]:
    """Read raw ``commands.ownerAllowFrom`` ids from OpenClaw.

    The repo's ``mobile_owner_status`` path redacts ids for log safety; this
    helper deliberately uses the same OpenClaw CLI as ``mobile_owner`` but
    returns raw values for the in-memory owner filter only. Raw ids never
    leave this function and are not logged.
    """

    args = [str(item) for item in get_list("mobile_owner.owner_get_command")]
    timeout = get_int("telegram_bridge.owner_lookup_timeout_seconds")
    try:
        result = runner.run(args, timeout_seconds=timeout)
    except Exception as exc:
        if log is not None:
            log("telegram bridge owner read failed", level="WARN", error=str(exc)[:200])
        return []
    if result.returncode != 0:
        if log is not None:
            log(
                "telegram bridge owner read returned non-zero",
                level="WARN",
                returncode=result.returncode,
            )
        return []
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        if log is not None:
            log("telegram bridge owner read produced non-json", level="WARN")
        return []
    owners: list[Any] = []
    if isinstance(parsed, list):
        owners = parsed
    elif isinstance(parsed, dict):
        for key in ("ownerAllowFrom", "commands.ownerAllowFrom"):
            value = parsed.get(key)
            if isinstance(value, list):
                owners = value
                break
        if not owners:
            commands_block = parsed.get("commands")
            if isinstance(commands_block, dict):
                value = commands_block.get("ownerAllowFrom")
                if isinstance(value, list):
                    owners = value
    return [str(item) for item in owners if str(item).strip()]


# ---------------------------------------------------------------------------
# Bridge orchestration
# ---------------------------------------------------------------------------


def parse_inbound_message(update: dict[str, Any]) -> InboundMessage | None:
    update_id = int(update.get("update_id", 0) or 0)
    message = update.get("message") or update.get("edited_message") or {}
    if not isinstance(message, dict):
        return None
    text_value = message.get("text", "")
    if not isinstance(text_value, str) or not text_value.strip():
        return None
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = int(chat.get("id", 0) or 0)
    sender_id = int(sender.get("id", 0) or 0)
    username = str(sender.get("username", "")) if isinstance(sender, dict) else ""
    if chat_id == 0 or sender_id == 0:
        return None
    return InboundMessage(
        update_id=update_id,
        chat_id=chat_id,
        sender_id=sender_id,
        sender_username=username,
        text=text_value,
        raw_date=int(message.get("date", 0) or 0),
    )


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except OSError:
        pass


def _record_inbound(message: InboundMessage, rule_id: str | None, *, dispatch_ms: int | None = None) -> None:
    if not get_bool("telegram_bridge.audit_inbound_to_jsonl"):
        return
    path = get_path("telegram_bridge.audit_jsonl_path")
    text_max = get_int("telegram_bridge.inbound_text_max_chars_for_log")
    payload = {
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "update_id": message.update_id,
        "sender_hash": _sender_hash(str(message.sender_id)),
        "chat_id_hash": _sender_hash(str(message.chat_id)),
        "char_count": len(message.text),
        "rule_id": rule_id or "",
        "dispatch_ms": int(dispatch_ms) if dispatch_ms is not None else None,
        "text_preview": message.text[:text_max] if text_max > 0 else "",
    }
    _append_jsonl(path, payload)


def _record_outbound(
    message: InboundMessage,
    result: DispatchResult,
    sent: SendResult,
    *,
    dispatch_ms: int | None = None,
    send_ms: int | None = None,
) -> None:
    if not get_bool("telegram_bridge.audit_outbound_to_jsonl"):
        return
    path = get_path("telegram_bridge.audit_outbound_jsonl_path")
    total_ms = None
    if dispatch_ms is not None and send_ms is not None:
        total_ms = int(dispatch_ms) + int(send_ms)
    outbound_text_max = get_int("telegram_bridge.outbound_text_max_chars_for_log")
    payload = {
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "update_id": message.update_id,
        "sender_hash": _sender_hash(str(message.sender_id)),
        "rule_id": result.rule_id,
        "dispatch_returncode": result.returncode,
        "send_ok": sent.ok,
        "send_returncode": sent.returncode,
        "send_detail": sent.detail[:200],
        "reply_chars": len(result.reply_text),
        "reply_preview": result.reply_text[:outbound_text_max] if outbound_text_max > 0 else "",
        "dispatch_ms": int(dispatch_ms) if dispatch_ms is not None else None,
        "send_ms": int(send_ms) if send_ms is not None else None,
        "total_ms": total_ms,
    }
    _append_jsonl(path, payload)


class TelegramBridge:
    """Long-running Telegram receive/dispatch/reply loop."""

    def __init__(
        self,
        *,
        poller: TelegramPoller,
        owner_filter: OwnerFilter,
        dispatcher: Dispatcher,
        offset_store: OffsetStore,
        log: LogFn,
    ) -> None:
        self._poller = poller
        self._owner_filter = owner_filter
        self._dispatcher = dispatcher
        self._offset_store = offset_store
        self._log = log

    def _send_reply(self, message: InboundMessage, dispatch_result: DispatchResult, *, dispatch_ms: int) -> SendResult:
        reply_text = dispatch_result.reply_text or ""
        max_chars = get_int("telegram_bridge.reply_max_chars")
        if len(reply_text) > max_chars:
            reply_text = reply_text[: max_chars - 1] + "…"
        send_started = time.monotonic()
        send_result = self._poller.send_message(
            chat_id=message.chat_id,
            text=reply_text,
            http_timeout=get_int("telegram_bridge.http_request_timeout_seconds"),
        )
        send_ms = int((time.monotonic() - send_started) * 1000)
        self._log(
            "telegram bridge reply sent" if send_result.ok else "telegram bridge reply failed",
            level="OK" if send_result.ok else "ERROR",
            rule_id=dispatch_result.rule_id,
            chat_hash=_sender_hash(str(message.chat_id)),
            sender_hash=_sender_hash(str(message.sender_id)),
            reply_chars=len(reply_text),
            send_returncode=send_result.returncode,
            dispatch_ms=dispatch_ms,
            send_ms=send_ms,
        )
        _record_outbound(message, dispatch_result, send_result, dispatch_ms=dispatch_ms, send_ms=send_ms)
        return send_result

    def _process_update(self, update: dict[str, Any]) -> tuple[bool, bool, bool, int]:
        """Returns (processed, skipped_unauthorized, sent_ok, new_offset)."""
        new_offset = int(update.get("update_id", 0) or 0) + 1
        message = parse_inbound_message(update)
        if message is None:
            self._log(
                "telegram bridge update ignored",
                level="INFO",
                reason="no-text",
                update_id=int(update.get("update_id", 0) or 0),
            )
            return False, False, False, new_offset

        if not self._owner_filter.is_authorized(message.sender_id):
            self._log(
                "telegram bridge unauthorized sender ignored",
                level="WARN" if get_bool("telegram_bridge.unauthorized_log_warn") else "INFO",
                update_id=message.update_id,
                sender_hash=_sender_hash(str(message.sender_id)),
                char_count=len(message.text),
            )
            _record_inbound(message, rule_id="unauthorized")
            if get_bool("telegram_bridge.unauthorized_reply_enabled"):
                self._poller.send_message(
                    chat_id=message.chat_id,
                    text="Not authorized.",
                    http_timeout=get_int("telegram_bridge.http_request_timeout_seconds"),
                )
            return False, True, False, new_offset

        self._log(
            "telegram bridge inbound message",
            update_id=message.update_id,
            sender_hash=_sender_hash(str(message.sender_id)),
            char_count=len(message.text),
        )

        if get_bool("telegram_bridge.ack_before_dispatch"):
            ack_text = get_str("telegram_bridge.ack_message_template")
            self._poller.send_message(
                chat_id=message.chat_id,
                text=ack_text,
                http_timeout=get_int("telegram_bridge.http_request_timeout_seconds"),
            )

        dispatch_started = time.monotonic()
        dispatch_result = self._dispatcher.dispatch(message, log=self._log)
        dispatch_ms = int((time.monotonic() - dispatch_started) * 1000)
        _record_inbound(message, rule_id=dispatch_result.rule_id, dispatch_ms=dispatch_ms)
        send_result = self._send_reply(message, dispatch_result, dispatch_ms=dispatch_ms)
        return True, False, send_result.ok, new_offset

    def poll_once(self) -> CycleOutcome:
        offset = self._offset_store.load()
        try:
            updates = self._poller.get_updates(
                offset=offset,
                allowed_updates=[str(item) for item in get_list("telegram_bridge.allowed_updates")],
                long_timeout=get_int("telegram_bridge.poll_long_timeout_seconds"),
                http_timeout=get_int("telegram_bridge.http_request_timeout_seconds"),
            )
        except Exception as exc:
            message = _redact_token_in(str(exc), self._poller.token)[:300]
            self._log("telegram bridge poll failed", level="ERROR", error=message)
            return CycleOutcome(polled=False, updates=0, processed=0, skipped_unauthorized=0, sent_ok=0, sent_failed=0, error=message)

        processed = 0
        skipped_unauthorized = 0
        sent_ok = 0
        sent_failed = 0
        max_offset = offset
        for update in updates:
            did_process, did_skip, did_send_ok, new_offset = self._process_update(update)
            max_offset = max(max_offset, new_offset)
            if did_process:
                processed += 1
                if did_send_ok:
                    sent_ok += 1
                else:
                    sent_failed += 1
            if did_skip:
                skipped_unauthorized += 1
        if updates:
            self._offset_store.save(max_offset)
        self._log(
            "telegram bridge cycle finished",
            level="OK",
            updates=len(updates),
            processed=processed,
            skipped=skipped_unauthorized,
            sent_ok=sent_ok,
            sent_failed=sent_failed,
            new_offset=max_offset,
        )
        return CycleOutcome(
            polled=True,
            updates=len(updates),
            processed=processed,
            skipped_unauthorized=skipped_unauthorized,
            sent_ok=sent_ok,
            sent_failed=sent_failed,
        )

    def run_forever(self, *, stop_when: Callable[[], bool] | None = None) -> int:
        consecutive_errors = 0
        max_consecutive = get_int("telegram_bridge.loop_max_consecutive_errors")
        sleep_on_error = get_int("telegram_bridge.loop_sleep_seconds_on_error")
        self._log("telegram bridge loop started", level="OK")
        while True:
            if stop_when is not None and stop_when():
                self._log("telegram bridge loop stop requested", level="OK")
                return 0
            outcome = self.poll_once()
            if not outcome.polled:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive:
                    self._log(
                        "telegram bridge loop giving up after consecutive errors",
                        level="ERROR",
                        consecutive_errors=consecutive_errors,
                    )
                    return 1
                time.sleep(sleep_on_error)
                continue
            consecutive_errors = 0


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_bridge(*, log: LogFn) -> TelegramBridge:
    token = _read_token()
    poller = TelegramPoller(token)
    runner = OpenClawRunner(log)
    owner_filter = OwnerFilter(runner=runner, log=log)
    dispatcher = ConfiguredDispatcher(runner_factory=lambda: OpenClawRunner(log))
    offset_store = OffsetStore()
    return TelegramBridge(
        poller=poller,
        owner_filter=owner_filter,
        dispatcher=dispatcher,
        offset_store=offset_store,
        log=log,
    )


def cycle_outcome_as_dict(outcome: CycleOutcome) -> dict[str, Any]:
    return asdict(outcome)


def configured_rules_as_dicts() -> list[dict[str, Any]]:
    return [asdict(rule) for rule in _load_rules()]


@dataclass(frozen=True)
class BridgeStatus:
    enabled: bool
    token_file_exists: bool
    token_file_path: str
    rule_count: int
    offset_state_path: str
    offset: int
    owner_source: str
    owner_ids_configured: int
    audit_jsonl_path: str
    flow_name: str


def check_bridge_status(*, log: LogFn) -> BridgeStatus:
    token_path = get_path("telegram_bridge.token_file")
    offset_store = OffsetStore()
    owner_source = get_str("telegram_bridge.owner_source")
    if owner_source == "openclaw-command-owner":
        owner_count = sum(1 for value in get_list("mobile_owner.owner_allow_from") if str(value).strip())
        if owner_count == 0:
            try:
                runner = OpenClawRunner(log)
                raw_ids = _read_raw_owner_ids_from_openclaw(runner=runner, log=log)
                owner_count = sum(1 for value in raw_ids if value.strip())
            except Exception as exc:
                log("telegram bridge status owner lookup failed", level="WARN", error=str(exc)[:200])
    else:
        owner_count = sum(1 for value in get_list("telegram_bridge.explicit_owner_ids") if str(value).strip())
    status = BridgeStatus(
        enabled=get_bool("telegram_bridge.enabled"),
        token_file_exists=token_path.is_file(),
        token_file_path=str(token_path),
        rule_count=len(_load_rules()),
        offset_state_path=str(offset_store.path),
        offset=offset_store.load(),
        owner_source=owner_source,
        owner_ids_configured=owner_count,
        audit_jsonl_path=str(get_path("telegram_bridge.audit_jsonl_path")),
        flow_name=get_str("flows.telegram_bridge"),
    )
    log(
        "telegram bridge status checked",
        level="OK" if status.enabled and status.token_file_exists else "WARN",
        enabled=status.enabled,
        token_file_exists=status.token_file_exists,
        rule_count=status.rule_count,
        owner_ids_configured=status.owner_ids_configured,
        offset=status.offset,
    )
    return status


def bridge_status_as_dict(status: BridgeStatus) -> dict[str, Any]:
    return asdict(status)
