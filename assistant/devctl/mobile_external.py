"""Mobile exposure and OpenClaw channel readiness helpers."""

from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from typing import Iterable

from .config import get_bool, get_int, get_list, get_str


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    status: str
    message: str


def tunnel_command_status() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for candidate in get_list("mobile_external.tunnel_command_candidates"):
        name = str(candidate)
        resolved = shutil.which(name)
        rows.append({"candidate": name, "path": resolved or "", "status": "available" if resolved else "missing"})
    return rows


def readiness_checks() -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []
    exposure = get_str("mobile_external.exposure")
    allowed = {str(item) for item in get_list("mobile_external.allowed_exposures")}
    host = get_str("mobile_bridge.host")
    require_token = get_bool("mobile_bridge.require_token")
    external_enabled = get_bool("mobile_external.enabled")
    token_env_var = get_str("mobile_bridge.token_env_var")
    token_configured = bool(os.environ.get(token_env_var, "").strip())

    checks.append(
        ReadinessCheck(
            id="external-enabled",
            status="ok" if external_enabled else "blocked",
            message=get_str("mobile_external.external_enabled_message")
            if external_enabled
            else get_str("mobile_external.security_note"),
        )
    )
    checks.append(
        ReadinessCheck(
            id="exposure-mode",
            status="ok" if exposure in allowed else "error",
            message=get_str("mobile_external.exposure_message_template").format(exposure=exposure),
        )
    )
    if exposure != "loopback" and get_bool("mobile_external.non_loopback_requires_token") and not require_token:
        checks.append(
            ReadinessCheck(
                id="token-required",
                status="error",
                message=get_str("mobile_external.non_loopback_token_required_message"),
            )
        )
    elif require_token and not token_configured:
        checks.append(
            ReadinessCheck(
                id="token-required",
                status="error",
                message=get_str("mobile_external.token_missing_message_template").format(token_env_var=token_env_var),
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                id="token-required",
                status="ok",
                message=get_str("mobile_external.token_ready_message"),
            )
        )
    checks.append(
        ReadinessCheck(
            id="bind-host",
            status="ok" if (exposure == "loopback" and host.startswith("127.")) or exposure != "loopback" else "warn",
            message=get_str("mobile_external.bind_host_message_template").format(host=host),
        )
    )

    if exposure == "tunnel":
        available = [row for row in tunnel_command_status() if row["status"] == "available"]
        checks.append(
            ReadinessCheck(
                id="tunnel-command",
                status="ok" if available else "blocked",
                message=get_str("mobile_external.tunnel_available_message")
                if available
                else get_str("mobile_external.tunnel_missing_message"),
            )
        )
    return checks


def readiness_as_dicts(checks: Iterable[ReadinessCheck] | None = None) -> list[dict[str, str]]:
    return [asdict(check) for check in (checks if checks is not None else readiness_checks())]


def external_info() -> dict[str, object]:
    return {
        "enabled": get_bool("mobile_external.enabled"),
        "mode": get_str("mobile_external.mode"),
        "exposure": get_str("mobile_external.exposure"),
        "public_url": get_str("mobile_external.public_url"),
        "bridge_host": get_str("mobile_bridge.host"),
        "bridge_port": get_int("mobile_bridge.port"),
        "token_required": get_bool("mobile_bridge.require_token"),
        "channel_enabled": get_bool("mobile_channel.enabled"),
        "channel": get_str("mobile_channel.channel"),
        "readiness": readiness_as_dicts(),
        "tunnel_commands": tunnel_command_status(),
    }
