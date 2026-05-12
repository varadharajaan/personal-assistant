"""Telegram token local/S3 backup helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import get_bool, get_int, get_list, get_path, get_str


@dataclass(frozen=True)
class TokenLocationStatus:
    local_path: str
    local_exists: bool
    local_bytes: int
    s3_enabled: bool
    s3_uri: str
    s3_checked: bool
    s3_exists: bool
    s3_bytes: int
    s3_encryption: str
    s3_object_lock_mode: str
    s3_retain_until: str


@dataclass(frozen=True)
class TokenCommandResult:
    returncode: int
    stdout: str
    stderr: str


def token_s3_uri() -> str:
    bucket = get_str("mobile_channel.s3_backup_bucket").strip("/")
    key = get_str("mobile_channel.s3_backup_key").strip("/")
    return f"s3://{bucket}/{key}" if bucket and key else ""


def _resolve_executable(candidate: str) -> str:
    path = Path(candidate).expanduser()
    if path.is_file():
        return str(path.resolve())
    return shutil.which(candidate) or ""


def _aws_cli() -> str:
    for candidate in get_list("mobile_channel.aws_cli_candidates"):
        resolved = _resolve_executable(str(candidate))
        if resolved:
            return resolved
    return ""


def _aws_base(command: str, args: list[str]) -> list[str]:
    aws = _aws_cli()
    if not aws:
        return []
    full = [aws, command, *args]
    profile = get_str("mobile_channel.s3_backup_profile")
    region = get_str("mobile_channel.s3_backup_region")
    if profile:
        full.extend(["--profile", profile])
    if region:
        full.extend(["--region", region])
    return full


def _run(command: list[str]) -> TokenCommandResult:
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return TokenCommandResult(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)


def _head_s3_object() -> dict[str, object] | None:
    command = _aws_base(
        "s3api",
        [
            "head-object",
            "--bucket",
            get_str("mobile_channel.s3_backup_bucket"),
            "--key",
            get_str("mobile_channel.s3_backup_key"),
        ],
    )
    if not command:
        return None
    result = _run(command)
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def protect_local_token_file(path: Path) -> None:
    if os.name != "nt" or not get_bool("mobile_channel.token_file_protect_acl"):
        return
    grants = [str(item) for item in get_list("mobile_channel.token_file_acl_grants")]
    if not grants:
        return
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", *grants],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def token_status(*, check_s3: bool = False) -> TokenLocationStatus:
    local_path = get_path("mobile_channel.token_file")
    local_exists = local_path.is_file()
    local_bytes = local_path.stat().st_size if local_exists else 0
    s3_payload = _head_s3_object() if check_s3 and get_bool("mobile_channel.s3_backup_enabled") else None
    return TokenLocationStatus(
        local_path=str(local_path),
        local_exists=local_exists,
        local_bytes=local_bytes,
        s3_enabled=get_bool("mobile_channel.s3_backup_enabled"),
        s3_uri=token_s3_uri(),
        s3_checked=check_s3,
        s3_exists=s3_payload is not None,
        s3_bytes=int(s3_payload.get("ContentLength", 0)) if s3_payload else 0,
        s3_encryption=str(s3_payload.get("ServerSideEncryption", "")) if s3_payload else "",
        s3_object_lock_mode=str(s3_payload.get("ObjectLockMode", "")) if s3_payload else "",
        s3_retain_until=str(s3_payload.get("ObjectLockRetainUntilDate", "")) if s3_payload else "",
    )


def backup_token_to_s3(*, log, confirm: bool) -> int:
    if not get_bool("mobile_channel.s3_backup_enabled"):
        print("Telegram token S3 fallback is disabled in config.")
        return 2
    if get_bool("mobile_channel.s3_backup_requires_confirm") and not confirm:
        print("Telegram token S3 fallback upload requires --confirm.")
        return 2
    local_path = get_path("mobile_channel.token_file")
    if not local_path.is_file():
        print("Local Telegram token file is missing.")
        log("telegram token s3 backup refused", level="ERROR", reason="local-token-missing")
        return 2
    size = local_path.stat().st_size
    if size < get_int("mobile_channel.token_file_min_bytes") or size > get_int("mobile_channel.token_file_max_bytes"):
        print("Local Telegram token file size is outside configured limits.")
        log("telegram token s3 backup refused", level="ERROR", reason="local-token-size", bytes=size)
        return 2

    command = _aws_base("s3", ["cp", str(local_path), token_s3_uri()])
    if not command:
        print("AWS CLI was not found.")
        return 2
    sse = get_str("mobile_channel.s3_backup_sse")
    if sse:
        command.extend(["--sse", sse])
    log("telegram token s3 backup started", bucket=get_str("mobile_channel.s3_backup_bucket"), key=get_str("mobile_channel.s3_backup_key"))
    result = _run(command)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    log("telegram token s3 backup finished", level="OK" if result.returncode == 0 else "ERROR", returncode=result.returncode)
    return result.returncode


def restore_token_from_s3(*, log, confirm: bool, overwrite: bool) -> int:
    if not get_bool("mobile_channel.s3_backup_enabled"):
        print("Telegram token S3 fallback is disabled in config.")
        return 2
    if get_bool("mobile_channel.s3_restore_requires_confirm") and not confirm:
        print("Telegram token S3 fallback restore requires --confirm.")
        return 2
    local_path = get_path("mobile_channel.token_file")
    if local_path.exists() and not overwrite:
        print("Local Telegram token file already exists. Re-run with --overwrite to replace it.")
        return 2

    local_path.parent.mkdir(parents=True, exist_ok=True)
    command = _aws_base("s3", ["cp", token_s3_uri(), str(local_path)])
    if not command:
        print("AWS CLI was not found.")
        return 2
    log("telegram token s3 restore started", bucket=get_str("mobile_channel.s3_backup_bucket"), key=get_str("mobile_channel.s3_backup_key"))
    result = _run(command)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip())
    if result.returncode == 0:
        protect_local_token_file(local_path)
    log("telegram token s3 restore finished", level="OK" if result.returncode == 0 else "ERROR", returncode=result.returncode)
    return result.returncode


def status_as_dict(status: TokenLocationStatus) -> dict[str, object]:
    return {
        "local_path": status.local_path,
        "local_exists": status.local_exists,
        "local_bytes": status.local_bytes,
        "s3_enabled": status.s3_enabled,
        "s3_uri": status.s3_uri,
        "s3_checked": status.s3_checked,
        "s3_exists": status.s3_exists,
        "s3_bytes": status.s3_bytes,
        "s3_encryption": status.s3_encryption,
        "s3_object_lock_mode": status.s3_object_lock_mode,
        "s3_retain_until": status.s3_retain_until,
    }
