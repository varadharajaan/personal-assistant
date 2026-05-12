"""Append-only mobile command inbox.

Mobile integrations can call `devctl mobile capture` to record commands before
they are processed. The full command text is stored locally in data/mobile, while
normal logs only record metadata so private message content does not leak into
general logs.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from .config import get_bool, get_int, get_list, get_str
from .paths import MOBILE_COMMAND_EVENTS_FILE, ensure_runtime_dirs

LogFn = Callable[..., None]


@dataclass(frozen=True)
class MobileCommand:
    id: str
    received_at: str
    source: str
    sender: str
    channel: str
    text: str
    status: str
    updated_at: str


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sender_hash(sender: str) -> str:
    return hashlib.sha256(sender.strip().lower().encode("utf-8")).hexdigest()[
        : get_int("mobile.sender_hash_chars")
    ]


def _append_event(event: dict[str, object]) -> None:
    ensure_runtime_dirs()
    with MOBILE_COMMAND_EVENTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=get_bool("mobile.events_json_ascii")) + "\n")


def _read_events() -> list[dict[str, object]]:
    if not MOBILE_COMMAND_EVENTS_FILE.exists():
        return []

    events: list[dict[str, object]] = []
    with MOBILE_COMMAND_EVENTS_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    events.append(item)
            except json.JSONDecodeError:
                continue
    return events


def capture_command(
    *,
    source: str,
    sender: str,
    text: str,
    channel: str | None = None,
    log: LogFn,
) -> MobileCommand:
    command_id = (
        f"{get_str('mobile.command_id_prefix')}_"
        f"{datetime.now().strftime(get_str('artifacts.timestamp_format'))}_"
        f"{uuid.uuid4().hex[: get_int('artifacts.id_random_chars')]}"
    )
    timestamp = _now()
    event = {
        "event": "captured",
        "id": command_id,
        "received_at": timestamp,
        "updated_at": timestamp,
        "source": source.strip() or get_str("mobile.default_source"),
        "sender": sender.strip() or get_str("mobile.default_sender"),
        "channel": (channel or "").strip() or get_str("mobile.default_channel"),
        "text": text,
        "status": get_str("mobile.pending_status"),
    }
    _append_event(event)
    command = MobileCommand(
        id=command_id,
        received_at=timestamp,
        source=str(event["source"]),
        sender=str(event["sender"]),
        channel=str(event["channel"]),
        text=text,
        status=get_str("mobile.pending_status"),
        updated_at=timestamp,
    )
    log(
        "mobile command captured",
        level="OK",
        command_id=command.id,
        source=command.source,
        channel=command.channel,
        sender_hash=_sender_hash(command.sender),
        chars=len(command.text),
    )
    return command


def mark_command(
    command_id: str,
    *,
    status: str,
    log: LogFn,
    artifact: str | None = None,
    error: str | None = None,
) -> None:
    event: dict[str, object] = {
        "event": "status",
        "id": command_id,
        "updated_at": _now(),
        "status": status,
    }
    if artifact:
        event["artifact"] = artifact
    if error:
        event["error"] = error[: get_int("mobile.error_max_chars")]
    _append_event(event)
    log(
        "mobile command status updated",
        level=(
            "OK"
            if status in {get_str("mobile.completed_status"), get_str("mobile.skipped_status")}
            else "WARN"
            if status == get_str("mobile.failed_status")
            else "INFO"
        ),
        command_id=command_id,
        status=status,
        has_artifact=bool(artifact),
        has_error=bool(error),
    )


def list_commands(*, status: str | None = None) -> list[MobileCommand]:
    selected_status = status or str(get_list("mobile.statuses")[0])
    current: dict[str, dict[str, object]] = {}
    for event in _read_events():
        command_id = str(event.get("id", "")).strip()
        if not command_id:
            continue
        if event.get("event") == "captured":
            current[command_id] = dict(event)
        elif command_id in current:
            current[command_id]["status"] = str(event.get("status", current[command_id].get("status", "")))
            current[command_id]["updated_at"] = str(event.get("updated_at", current[command_id].get("updated_at", "")))
            if "artifact" in event:
                current[command_id]["artifact"] = event["artifact"]
            if "error" in event:
                current[command_id]["error"] = event["error"]

    commands = [
        MobileCommand(
            id=str(item.get("id", "")),
            received_at=str(item.get("received_at", "")),
            source=str(item.get("source", "")),
            sender=str(item.get("sender", "")),
            channel=str(item.get("channel", "")),
            text=str(item.get("text", "")),
            status=str(item.get("status", get_str("mobile.pending_status"))),
            updated_at=str(item.get("updated_at", item.get("received_at", ""))),
        )
        for item in current.values()
    ]
    commands.sort(key=lambda item: item.received_at, reverse=True)
    if selected_status != "all":
        commands = [item for item in commands if item.status == selected_status]
    return commands


def pending_commands(limit: int) -> Iterable[MobileCommand]:
    count = 0
    for command in reversed(list_commands(status=get_str("mobile.pending_status"))):
        if count >= limit:
            break
        yield command
        count += 1
