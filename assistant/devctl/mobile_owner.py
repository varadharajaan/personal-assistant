"""OpenClaw command-owner helpers.

OpenClaw command-owner configuration controls which external chat identity can
invoke owner-only commands. This module intentionally uses the OpenClaw CLI
instead of editing OpenClaw state files directly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable

from .config import get_bool, get_int, get_list, get_str
from .openclaw_runner import CommandResult, OpenClawRunner


@dataclass(frozen=True)
class CommandOwnerStatus:
    configured: bool
    owner_count: int
    owners: list[str]
    returncode: int
    message: str


@dataclass(frozen=True)
class CommandOwnerSetResult:
    owners: list[str]
    set_result: CommandResult
    display_result: CommandResult | None
    restart_result: CommandResult | None

    @property
    def returncode(self) -> int:
        return max(
            result.returncode
            for result in [self.set_result, self.display_result, self.restart_result]
            if result is not None
        )


def _command_from_config(key_path: str, replacements: dict[str, str] | None = None) -> list[str]:
    values = [str(item) for item in get_list(key_path)]
    replacement_values = replacements or {}
    return [value.format_map(replacement_values) for value in values]


def _redacted_owner(value: str) -> str:
    if get_bool("mobile_owner.show_raw_owner_ids"):
        return value
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[
        : get_int("mobile_owner.owner_hash_chars")
    ]
    return f"{get_str('mobile_owner.redacted_owner_prefix')}{digest}"


def redacted_owners(owners: Iterable[str]) -> list[str]:
    return [_redacted_owner(owner) for owner in owners]


def owner_payload(owners: Iterable[str]) -> str:
    cleaned: list[str] = []
    blocked = {str(item) for item in get_list("mobile_owner.blocked_owner_values")}
    min_chars = get_int("mobile_owner.owner_id_min_chars")
    for owner in owners:
        value = owner.strip()
        if not value:
            continue
        if value in blocked:
            raise ValueError(f"Owner id value is blocked by config: {value}")
        if len(value) < min_chars:
            raise ValueError(f"Owner id is shorter than configured minimum length: {value}")
        cleaned.append(value)
    if not cleaned:
        raise ValueError("At least one command owner id is required.")
    return json.dumps(cleaned, ensure_ascii=get_bool("mobile_owner.owner_json_ascii"))


def configured_owner_values(cli_owners: list[str] | None = None) -> list[str]:
    values = cli_owners if cli_owners else [str(item) for item in get_list("mobile_owner.owner_allow_from")]
    return [value.strip() for value in values if value.strip()]


def parse_owner_status(result: CommandResult) -> CommandOwnerStatus:
    if result.returncode != 0:
        return CommandOwnerStatus(
            configured=False,
            owner_count=0,
            owners=[],
            returncode=result.returncode,
            message=(result.stderr or result.stdout).strip()[: get_int("mobile_owner.status_message_max_chars")],
        )

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        parsed = []

    owners: list[str] = []
    if isinstance(parsed, list):
        owners = [str(item) for item in parsed]
    elif isinstance(parsed, dict):
        owner_values = parsed.get("ownerAllowFrom", [])
        if isinstance(owner_values, list):
            owners = [str(item) for item in owner_values]
    return CommandOwnerStatus(
        configured=bool(owners),
        owner_count=len(owners),
        owners=redacted_owners(owners),
        returncode=result.returncode,
        message=get_str("mobile_owner.status_configured_message")
        if owners
        else get_str("mobile_owner.status_missing_message"),
    )


def command_owner_status(*, runner: OpenClawRunner, timeout_seconds: int | None = None) -> CommandOwnerStatus:
    result = runner.run(
        _command_from_config("mobile_owner.owner_get_command"),
        timeout_seconds=timeout_seconds or get_int("mobile_owner.command_timeout_seconds"),
    )
    return parse_owner_status(result)


def command_owner_status_as_dict(status: CommandOwnerStatus) -> dict[str, object]:
    return asdict(status)


def set_command_owners(
    *,
    runner: OpenClawRunner,
    owners: list[str],
    log,
    confirm: bool,
    restart: bool,
    timeout_seconds: int | None = None,
) -> CommandOwnerSetResult | None:
    if get_bool("mobile_owner.set_requires_confirm") and not confirm:
        print(get_str("mobile_owner.set_refusal_message"))
        return None

    payload = owner_payload(owners)
    redacted = redacted_owners(json.loads(payload))
    timeout = timeout_seconds or get_int("mobile_owner.command_timeout_seconds")
    set_result = runner.run(
        _command_from_config("mobile_owner.owner_set_command", {"owner_allow_from_json": payload}),
        timeout_seconds=timeout,
    )
    display_result = None
    restart_result = None

    if set_result.returncode == 0 and get_bool("mobile_owner.set_owner_display"):
        display_result = runner.run(
            _command_from_config("mobile_owner.owner_display_set_command", {"owner_display": get_str("mobile_owner.owner_display")}),
            timeout_seconds=timeout,
        )

    if set_result.returncode == 0 and restart and get_bool("mobile_owner.restart_gateway_after_set"):
        restart_result = runner.run(
            _command_from_config("mobile_owner.gateway_restart_command"),
            timeout_seconds=get_int("mobile_owner.restart_timeout_seconds"),
        )

    log(
        "mobile command owner set attempted",
        level="OK" if set_result.returncode == 0 else "ERROR",
        owner_count=len(redacted),
        owners=",".join(redacted),
        restarted=bool(restart_result),
        returncode=set_result.returncode,
    )
    return CommandOwnerSetResult(
        owners=redacted,
        set_result=set_result,
        display_result=display_result,
        restart_result=restart_result,
    )


def command_owner_set_result_as_dict(result: CommandOwnerSetResult) -> dict[str, object]:
    return {
        "owners": result.owners,
        "returncode": result.returncode,
        "set_result": asdict(result.set_result),
        "display_result": asdict(result.display_result) if result.display_result else None,
        "restart_result": asdict(result.restart_result) if result.restart_result else None,
    }
